import hashlib
import json

from .errors import AppError, ModelError
from .models import (
    Change,
    Decision,
    DraftBundle,
    Goal,
    GoalProposal,
    HistoryEvent,
    Message,
    Operation,
    Preview,
    Run,
    Workspace,
    timestamp,
)

# Conservative starting value, not a calibrated guarantee of correctness.
MIN_CONFIDENCE = 0.70


def proposed_goal(current: Goal, proposal: GoalProposal, request: str) -> Goal:
    current_fields = current.model_dump(mode="json")
    patch = {
        field: value
        for field, value in proposal.patch.model_dump(mode="json", exclude_none=True).items()
        if value != current_fields[field] or field in {"room_required", "catering_required"}
    }
    evidence = {entry.field: entry.quote for entry in proposal.evidence}
    for field in patch:
        if field not in evidence or evidence[field].casefold() not in request.casefold():
            raise ModelError(f"The proposed {field} change has no matching quote in your request.")
    if proposal.intent in {"unrelated", "unclear"} and patch:
        raise ModelError("The model proposed changes without identifying a change request.")
    if patch.get("delivery") == "online" and "room_required" not in patch:
        patch["room_required"] = False
    if patch.get("delivery") in {"in_person", "hybrid"} and "room_required" not in patch:
        patch["room_required"] = True
    if patch.get("room_required") is False and "catering_required" not in patch:
        patch["catering_required"] = False
    return Goal.model_validate(current.model_dump(mode="json") | patch)


def changed_fields(before: Goal, after: Goal) -> list[str]:
    old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
    return [name for name in old if old[name] != new[name]]


def choose_change(resource_id: str, state: Workspace, decision: Decision | None) -> Change:
    resource = next(r for r in state.resources if r.id == resource_id)

    def result(operation: Operation, reason: str) -> Change:
        return Change(
            resource_id=resource.id,
            before=resource.content,
            expected_version=resource.version,
            operation=operation,
            reason=reason,
        )

    if decision is None:
        return result("keep", "None of this item's inputs changed.")
    if decision.confidence < MIN_CONFIDENCE or decision.choice == "unresolved":
        return result("ask", "The decision is uncertain. Clarification is required.")
    if decision.choice == "supported":
        return result("keep", "Decider found that this assumption still holds.")
    if decision.choice != "contradicted":
        raise ModelError("Unexpected assumption decision.")
    if resource.kind == "sent_request":
        if resource.status == "cancellation_requested":
            return result("ask", "A cancellation is already pending; review the existing request.")
        return result(
            "request_cancellation",
            "The original request was sent. A new cancellation request needs separate approval.",
        )
    if resource.kind == "room_hold":
        return result("update", "The existing hold no longer matches the plan.")
    return result("update", "The draft no longer matches the updated requirements.")


def seal(preview: Preview) -> Preview:
    payload = preview.model_dump(mode="json", exclude={"hash"})
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return preview.model_copy(update={"hash": digest})


def finish_preview(preview: Preview, bundle: DraftBundle) -> Preview:
    expected = {
        c.resource_id
        for c in preview.changes
        if c.operation == "update" and c.resource_id != "room"
    }
    drafts = {draft.resource_id: draft.content for draft in bundle.drafts}
    if len(drafts) != len(bundle.drafts) or set(drafts) != expected:
        raise ModelError("The model did not provide exactly the requested draft updates.")
    cancellation = any(c.operation == "request_cancellation" for c in preview.changes)
    if cancellation != bool(bundle.cancellation_message):
        raise ModelError("The model did not provide the expected cancellation draft.")
    changes = [
        change.model_copy(update={"after": drafts[change.resource_id]})
        if change.resource_id in drafts
        else change
        for change in preview.changes
    ]
    return seal(
        preview.model_copy(
            update={
                "changes": changes,
                "summary": bundle.summary,
                "cancellation_message": bundle.cancellation_message,
            }
        )
    )


def verify_approval(state: Workspace, run: Run, approval_hash: str) -> Preview:
    preview = run.preview
    if preview is None or preview.hash != approval_hash or seal(preview).hash != approval_hash:
        raise AppError("This preview does not match your approval. Review it again.")
    if not preview.can_apply:
        raise AppError(
            "This preview needs clarification or is only a hypothetical. Nothing was applied."
        )
    if run.status not in {"ready", "applied", "cancellation_requested"}:
        raise AppError("This plan is not ready to apply.")
    if run.status == "ready":
        if state.revision != preview.base_revision:
            raise AppError("This plan changed; review it again.")
        versions = {r.id: r.version for r in state.resources}
        if any(versions.get(c.resource_id) != c.expected_version for c in preview.changes):
            raise AppError("An item changed; review the plan again.")
    return preview


def apply_standard(
    state: Workspace, run: Run, approval_hash: str
) -> tuple[Workspace, Run, HistoryEvent]:
    preview = verify_approval(state, run, approval_hash)
    if run.status != "ready":
        raise AppError("This plan was already applied.")
    updated = state.model_copy(deep=True)
    updated.goal = preview.goal
    updated.revision += 1
    details = []
    for change in preview.changes:
        resource = next(r for r in updated.resources if r.id == change.resource_id)
        if change.operation in {"update", "reserve", "release"}:
            resource.content = change.after
            resource.version += 1
            if resource.kind == "room_hold":
                resource.status = "released" if change.operation == "release" else "active"
            if resource.kind == "draft":
                resource.assumption = (
                    f"The current {resource.name.lower()} is correct: {resource.content[:500]}"
                )
            elif resource.kind == "room_hold":
                resource.assumption = f"This room hold is still needed: {resource.content[:500]}"
            details.append(f"{resource.name}: {change.operation}")
    updated_run = run.model_copy(update={"status": "applied", "applied_revision": updated.revision})
    event = HistoryEvent(
        id=f"{run.id}:apply",
        title="Approved changes applied",
        details=details or ["The goal was updated; existing items were preserved."],
        revision=updated.revision,
    )
    return updated, updated_run, event


def apply_cancellation(
    state: Workspace, run: Run, approval_hash: str
) -> tuple[Workspace, Run, HistoryEvent]:
    preview = verify_approval(state, run, approval_hash)
    if run.status != "applied" or state.revision != run.applied_revision:
        raise AppError("The plan changed or has not been applied. Review cancellation again.")
    target = next((c for c in preview.changes if c.operation == "request_cancellation"), None)
    if not target or not preview.cancellation_message:
        raise AppError("There is no cancellation request to approve.")
    updated = state.model_copy(deep=True)
    resource = next(r for r in updated.resources if r.id == target.resource_id)
    if resource.version != target.expected_version or resource.status != "sent":
        raise AppError("The original request changed. Review cancellation again.")
    resource.status = "cancellation_requested"
    resource.version += 1
    updated.revision += 1
    updated.messages.append(
        Message(
            id=f"{run.id}:cancellation",
            subject="Cancellation request",
            content=preview.cancellation_message,
            created_at=timestamp(),
        )
    )
    updated_run = run.model_copy(update={"status": "cancellation_requested"})
    event = HistoryEvent(
        id=f"{run.id}:cancellation",
        title="Cancellation requested",
        details=[
            "A new sandbox message was recorded. The original sent request remains.",
            "Vendor acceptance is not known.",
        ],
        revision=updated.revision,
    )
    return updated, updated_run, event
