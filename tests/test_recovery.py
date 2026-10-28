from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from nevermind.api import app, authenticated_user, service
from nevermind.errors import AppError, ModelError
from nevermind.fixture import prepared_workshop
from nevermind.models import (
    Approval,
    Decision,
    Draft,
    DraftBundle,
    Evidence,
    GoalPatch,
    GoalProposal,
    Run,
)
from nevermind.planner import build_preview, finalize, requirement_summary
from nevermind.policy import apply_cancellation, apply_standard, proposed_goal, seal
from nevermind.service import Service

USER = "26c6ec46-85dd-48bb-b67e-66c1487bc94c"


class Decisions:
    def __init__(self, confidence=0.95):
        self.confidence = confidence
        self.calls = []

    def decide(self, resource_id, state, *, intent=False):
        self.calls.append(resource_id)
        choices = (
            ["change_request", "hypothetical", "unrelated", "unclear"]
            if intent
            else ["supported", "contradicted", "unresolved"]
        )
        selected = "change_request" if intent else "contradicted"
        return Decision(
            resource_id=resource_id,
            choice=selected,
            confidence=self.confidence,
            probabilities={
                key: 0.97 if key == selected else 0.03 / (len(choices) - 1) for key in choices
            },
            model="test-only",
            seconds=0.01,
        )


def preview(state, *, hypothetical=False, confidence=0.95, keep_room=False):
    proposal = GoalProposal(
        intent="hypothetical" if hypothetical else "change_request",
        patch=GoalPatch(delivery="online", room_required=True if keep_room else None),
        evidence=[Evidence(field="delivery", quote="online")]
        + ([Evidence(field="room_required", quote="keep the room")] if keep_room else []),
    )
    decisions = Decisions(confidence)
    draft = build_preview(
        state,
        "Make it online, but keep the room." if keep_room else "Make it online.",
        proposal,
        decisions,
    )
    if draft.questions:
        return seal(draft), decisions
    bundle = DraftBundle(
        drafts=[
            Draft(
                resource_id=change.resource_id,
                content=f"Revised {change.resource_id}. Meeting link: to be confirmed.",
            )
            for change in draft.changes
            if change.operation == "update" and change.resource_id != "room"
        ],
        cancellation_message="Please cancel the previous refreshments request."
        if any(c.operation == "request_cancellation" for c in draft.changes)
        else "",
        summary="Proposed workshop changes.",
    )
    return finalize(draft, bundle), decisions


def ready_run(store, user=USER, **options):
    state = store.reset(user, 0)
    result, _ = preview(state, **options)
    run = Run(id=str(uuid4()), request="Make it online.", base_revision=1, status="queued")
    store.enqueue(user, run)
    run = store.claim(user, run.id)
    run.status, run.preview = "ready", result
    store.finish(user, run)
    return state, run


def test_preview_does_not_mutate_and_preserves_independent_work():
    state = prepared_workshop()
    before = state.model_dump_json()
    result, decisions = preview(state)
    assert state.model_dump_json() == before
    assert "slides" not in decisions.calls and "sponsor" not in decisions.calls
    operations = {c.resource_id: c.operation for c in result.changes}
    assert operations == {
        "room": "release",
        "invitation": "update",
        "handouts": "update",
        "slides": "keep",
        "catering": "request_cancellation",
        "sponsor": "keep",
    }
    assert result.can_apply


def test_room_exception_preserves_room_and_catering_without_model_guess():
    result, decisions = preview(prepared_workshop(), keep_room=True)
    assert result.goal.room_required and result.goal.catering_required
    assert "room" not in decisions.calls and "catering" not in decisions.calls
    assert next(c for c in result.changes if c.resource_id == "room").operation == "keep"


def test_decider_requirement_excludes_inactive_venue():
    state = prepared_workshop()
    goal = state.goal.model_copy(
        update={"delivery": "online", "room_required": False, "catering_required": False}
    )
    for resource in state.resources:
        summary = requirement_summary(goal, resource.depends_on)
        assert "online only" in summary
        assert "Room 42" not in summary
    assert "No physical room is required" in requirement_summary(
        goal, state.resources[0].depends_on
    )
    assert "No catering" in requirement_summary(goal, state.resources[4].depends_on)


def test_decider_requirement_retains_explicit_local_room():
    state = prepared_workshop()
    goal = state.goal.model_copy(update={"delivery": "online", "room_required": True})
    summary = requirement_summary(goal, state.resources[0].depends_on)
    assert "retained room for local attendees" in summary
    assert "Room 42" in summary
    assert "No attendees travel" not in summary


def test_hypothetical_cannot_apply_even_if_decider_calls_it_a_change(store):
    state, run = ready_run(store, hypothetical=True)
    assert not run.preview.can_apply
    with pytest.raises(AppError, match="hypothetical"):
        apply_standard(state, run, run.preview.hash)
    assert store.workspace(USER) == state


def test_uncertainty_blocks_apply():
    result, _ = preview(prepared_workshop(), confidence=0.4)
    assert result.questions and not result.can_apply
    assert all(c.operation in {"ask", "keep"} for c in result.changes)


def test_no_approval_no_effect(store):
    state, run = ready_run(store)
    with pytest.raises(AppError, match="approval"):
        apply_standard(state, run, "f" * 64)
    assert store.workspace(USER) == state


def test_changed_preview_hash_is_rejected(store):
    state, run = ready_run(store)
    old_hash = run.preview.hash
    run.preview.goal.venue = "Invented venue"
    with pytest.raises(AppError, match="approval"):
        apply_standard(state, run, old_hash)


def test_stale_goal_and_resource_versions_rejected(store):
    state, run = ready_run(store)
    altered = state.model_copy(deep=True)
    altered.revision += 1
    with pytest.raises(AppError, match="plan changed"):
        apply_standard(altered, run, run.preview.hash)
    altered = state.model_copy(deep=True)
    altered.resources[0].version += 1
    with pytest.raises(AppError, match="item changed"):
        apply_standard(altered, run, run.preview.hash)


def test_apply_then_separate_cancellation_keeps_original_message(store):
    state, run = ready_run(store)
    new, applied, event = apply_standard(state, run, run.preview.hash)
    store.commit(USER, state, new, applied, event)
    assert new.messages == state.messages
    assert next(r for r in new.resources if r.id == "room").status == "released"
    assert next(r for r in new.resources if r.id == "slides").content == state.resources[3].content
    final, cancelled, event = apply_cancellation(new, applied, run.preview.hash)
    store.commit(USER, new, final, cancelled, event)
    assert final.messages[0] == state.messages[0]
    assert len(final.messages) == 2
    assert next(r for r in final.resources if r.id == "catering").status == "cancellation_requested"
    assert len(store.history(USER)) == 3


def test_cancellation_cannot_precede_standard_approval(store):
    state, run = ready_run(store)
    with pytest.raises(AppError):
        apply_cancellation(state, run, run.preview.hash)


def test_changed_goal_invalidates_cancellation(store):
    state, run = ready_run(store)
    new, applied, event = apply_standard(state, run, run.preview.hash)
    store.commit(USER, state, new, applied, event)
    changed = store.reset(USER, new.revision)
    with pytest.raises(AppError, match="plan changed"):
        apply_cancellation(changed, applied, run.preview.hash)


def test_duplicate_apply_and_cancellation_have_one_effect(store):
    _, run = ready_run(store)
    backend = Service(store, MagicMock(), "queue", MagicMock(), MagicMock(), "arn", "probe")
    approval = Approval(hash=run.preview.hash)
    assert backend.approve(USER, run.id, approval, cancellation=False).revision == 2
    assert backend.approve(USER, run.id, approval, cancellation=False).revision == 2
    assert backend.approve(USER, run.id, approval, cancellation=True).revision == 3
    assert backend.approve(USER, run.id, approval, cancellation=True).revision == 3
    assert len(store.workspace(USER).messages) == 2
    assert len(store.history(USER)) == 3


def test_conflicting_transaction_has_no_partial_effect(store):
    state, run = ready_run(store)
    new, applied, event = apply_standard(state, run, run.preview.hash)
    store.reset(USER, 1)
    with pytest.raises(AppError):
        store.commit(USER, state, new, applied, event)
    assert store.run(USER, run.id).status == "ready"
    assert len(store.history(USER)) == 2
    assert store.workspace(USER).goal.delivery == "in_person"


def test_two_users_are_isolated(store):
    state, run = ready_run(store)
    assert store.workspace("someone-else") is None
    with pytest.raises(AppError, match="could not be found"):
        store.run("someone-else", run.id)
    assert store.workspace(USER) == state


def test_only_one_active_analysis(store):
    store.reset(USER, 0)
    first = Run(id=str(uuid4()), request="online", base_revision=1, status="queued")
    second = Run(id=str(uuid4()), request="later", base_revision=1, status="queued")
    store.enqueue(USER, first)
    with pytest.raises(AppError):
        store.enqueue(USER, second)
    assert store.claim(USER, first.id) is not None
    assert store.claim(USER, first.id) is None


def test_model_failures_are_persisted_without_mutation(store):
    state = store.reset(USER, 0)
    run = Run(id=str(uuid4()), request="online", base_revision=1, status="queued")
    store.enqueue(USER, run)
    generator = MagicMock()
    generator.generate.side_effect = ModelError("Model output was invalid.")
    runtime = MagicMock()
    backend = Service(store, MagicMock(), "queue", generator, runtime, "arn", "probe")
    from nevermind.models import Job

    backend.process(Job(user_id=USER, run_id=run.id))
    assert store.run(USER, run.id).status == "failed"
    assert store.workspace(USER) == state
    runtime.stop_runtime_session.assert_not_called()


def test_unreferenced_model_change_rejected():
    proposal = GoalProposal(intent="change_request", patch=GoalPatch(venue="Room 99"), evidence=[])
    with pytest.raises(ModelError, match="quote"):
        proposed_goal(prepared_workshop().goal, proposal, "Make it online")


def test_unexpected_drafts_rejected():
    result, _ = preview(prepared_workshop())
    with pytest.raises(ModelError, match="exactly"):
        finalize(result, DraftBundle(drafts=[], cancellation_message="cancel", summary="Invalid"))


def test_successive_goal_changes_preserve_unspecified_fields():
    state = prepared_workshop()
    state.goal.delivery = "online"
    state.goal.room_required = False
    proposal = GoalProposal(
        intent="change_request",
        patch=GoalPatch(time="16:00"),
        evidence=[Evidence(field="time", quote="4 PM")],
    )
    result = proposed_goal(state.goal, proposal, "Change it to 4 PM.")
    assert result.delivery == "online" and result.time == "16:00" and not result.room_required


def test_api_authentication_and_request_bounds():
    client = TestClient(app)
    assert client.get("/api/workspace").status_code == 401
    assert client.post("/api/runs", content="x" * 13000).status_code == 413


def test_api_reset_requires_confirmation(store):
    backend = Service(store, MagicMock(), "queue", MagicMock(), MagicMock(), "arn", "probe")
    app.dependency_overrides[authenticated_user] = lambda: USER
    app.dependency_overrides[service] = lambda: backend
    try:
        client = TestClient(app)
        assert (
            client.post("/api/workspace/reset", json={"confirm": False, "revision": 0}).status_code
            == 422
        )
        result = client.post("/api/workspace/reset", json={"confirm": True, "revision": 0})
        assert result.status_code == 200
        assert result.json()["workspace"]["revision"] == 1
    finally:
        app.dependency_overrides.clear()
