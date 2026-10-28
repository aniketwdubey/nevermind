import json
from typing import Protocol

from .models import Decision, DraftBundle, Goal, GoalProposal, Preview, Workspace
from .policy import MIN_CONFIDENCE, changed_fields, choose_change, finish_preview, proposed_goal


class Generator(Protocol):
    def generate(
        self, schema: type[GoalProposal], instructions: str, state: str
    ) -> GoalProposal: ...


class DecisionMaker(Protocol):
    def decide(
        self, resource_id: str, state: dict[str, object], *, intent: bool = False
    ) -> Decision: ...


PROPOSAL_INSTRUCTIONS = """Interpret a workshop change request without executing it.
Return change_request only for a request to change the active plan, hypothetical for
'what if' exploration, unrelated for comments, unclear for undecided requests.
Set patch fields only if requested. Omitted fields keep their existing values.
Use exact substrings of the user request as evidence for every non-null patch field.
Do not invent a venue, URL, date, time, attendees, or topic.
'Make it online' sets delivery=online; unless an exception is requested, the application
will remove the physical room requirement and catering. 'Keep the room' sets room_required=true.
For a basic online request, room_required and catering_required MUST be null: the application
handles those implications. Never set catering_required unless the user explicitly mentions
catering, meals, food, or refreshments. Retaining a room does not itself request cancelling food.
Every non-null patch field requires an evidence entry with an exact quote from the request.
For hypothetical changes, propose the hypothetical patch but mark intent=hypothetical.
For incomplete changes, ask concrete questions. Never change unrelated fields."""

DRAFT_INSTRUCTIONS = """Draft only the specified resource updates using the proposed goal.
Do not invent a meeting link. If online and no URL exists, write 'Meeting link: to be confirmed.'
Include exact date, time, and timezone in the invitation. Preserve unrelated details.
Return exactly the requested draft IDs. Draft a cancellation only when requested.
The cancellation is a new request referencing the original message, not proof of acceptance.
Summarize proposed changes, not completed actions. All effects are a simulated sandbox."""


def requirement_summary(goal: Goal, dependencies: list[str]) -> str:
    if goal.delivery == "online" and not goal.room_required:
        sentences = ["The workshop is online only. No attendees travel to the physical venue."]
    elif goal.delivery == "online":
        sentences = [
            "The workshop is online. Attendees can join remotely without travelling to the venue. "
            "A room is retained for local attendees who choose to join together. "
            "Remote attendees receive the workshop materials digitally, not in person."
        ]
    elif goal.delivery == "hybrid":
        sentences = ["The workshop has both online and in-person attendees."]
    else:
        sentences = ["The workshop is in person."]
    if "room_required" in dependencies:
        sentences.append(
            "A physical room is required."
            if goal.room_required
            else "No physical room is required."
        )
    if "catering_required" in dependencies:
        sentences.append(
            "Catering is required."
            if goal.catering_required
            else "No catering or refreshments are required."
        )
    # A retained location value is historical when no physical service needs it.
    if "venue" in dependencies and (goal.room_required or goal.catering_required):
        sentences.append(f"The physical venue is {goal.venue}.")
    if "date" in dependencies:
        sentences.append(f"The workshop date is {goal.date}.")
    if "time" in dependencies:
        sentences.append(f"The workshop starts at {goal.time} {goal.timezone}.")
    if "topic" in dependencies:
        sentences.append(f"The workshop topic is {goal.topic}.")
    if "attendees" in dependencies:
        sentences.append(f"There are {goal.attendees} attendees.")
    if "meeting_url" in dependencies and goal.meeting_url:
        sentences.append(f"The meeting URL is {goal.meeting_url}.")
    return " ".join(sentences)


def build_preview(
    state: Workspace, request: str, proposal: GoalProposal, decider: DecisionMaker
) -> Preview:
    goal = proposed_goal(state.goal, proposal, request)
    changed = changed_fields(state.goal, goal)
    intent = decider.decide(
        "request",
        {
            "current_goal": "A prepared Python workshop.",
            "user_request": request,
        },
        intent=True,
    )
    decisions = [intent]
    questions = list(proposal.questions)
    if proposal.intent == "change_request" and (
        intent.choice != "change_request" or intent.confidence < MIN_CONFIDENCE
    ):
        questions.append("Please confirm the exact change you want to make to the active plan.")
    changes = []
    for resource in state.resources:
        affected = set(resource.depends_on) & set(changed)
        decision = None
        if affected:
            # Each decision gets only the inputs that this resource actually depends on.
            decision = decider.decide(
                resource.id,
                {
                    "updated_goal": requirement_summary(goal, resource.depends_on),
                    "assumption": resource.assumption,
                },
            )
            decisions.append(decision)
        change = choose_change(resource.id, state, decision)
        if change.operation == "ask":
            questions.append(f"Please clarify what should happen to {resource.name.lower()}.")
        if resource.kind == "room_hold" and change.operation == "update":
            if not goal.room_required:
                change.operation, change.after = "release", "Room hold released in the sandbox."
            else:
                change.operation = "reserve" if resource.status == "released" else "update"
                change.after = f"{goal.venue} is held for {goal.attendees} attendees on {goal.date} at {goal.time} {goal.timezone}."
        changes.append(change)
    can_apply = proposal.intent == "change_request" and bool(changed) and not questions
    return Preview(
        base_revision=state.revision,
        goal=goal,
        changed_fields=changed,
        intent=proposal.intent,
        changes=changes,
        decisions=decisions,
        questions=list(dict.fromkeys(questions)),
        summary="Review the proposed changes. No workshop items have changed.",
        can_apply=can_apply,
    )


def draft_input(state: Workspace, preview: Preview) -> str:
    return json.dumps(
        {
            "updated_goal": preview.goal.model_dump(mode="json"),
            "drafts_requested": [
                {"resource_id": c.resource_id, "current_content": c.before}
                for c in preview.changes
                if c.operation == "update" and c.resource_id != "room"
            ],
            "cancellation_requested": any(
                c.operation == "request_cancellation" for c in preview.changes
            ),
            "original_sent_requests": [
                message.model_dump(mode="json") for message in state.messages
            ],
        }
    )


def finalize(preview: Preview, drafts: DraftBundle) -> Preview:
    return finish_preview(preview, drafts)
