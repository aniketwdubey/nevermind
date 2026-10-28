from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as Date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Goal(Model):
    topic: str = Field(default="Practical Python", min_length=1, max_length=100)
    delivery: Literal["in_person", "online", "hybrid"] = "in_person"
    date: Date = Date(2026, 10, 30)
    time: str = Field(default="14:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: Literal["Asia/Kolkata"] = "Asia/Kolkata"
    venue: str = Field(default="Room 42", min_length=1, max_length=150)
    room_required: bool = True
    catering_required: bool = True
    attendees: int = Field(default=20, ge=1, le=200)
    meeting_url: str = Field(default="", max_length=300, pattern=r"^(https://[^\s]+)?$")


class GoalPatch(Model):
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    delivery: Literal["in_person", "online", "hybrid"] | None = None
    date: Date | None = None
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    venue: str | None = Field(default=None, min_length=1, max_length=150)
    room_required: bool | None = Field(
        default=None,
        description="Only set for an explicit request about retaining or removing the room. Otherwise null.",
    )
    catering_required: bool | None = Field(
        default=None,
        description="Only set for an explicit request about food or catering. Do not infer this from delivery mode.",
    )
    attendees: int | None = Field(default=None, ge=1, le=200)
    meeting_url: str | None = Field(default=None, max_length=300, pattern=r"^(https://[^\s]+)?$")


Intent = Literal["change_request", "hypothetical", "unrelated", "unclear"]
Relation = Literal["supported", "contradicted", "unresolved"]
Operation = Literal["keep", "update", "release", "reserve", "request_cancellation", "ask"]


class Evidence(Model):
    field: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=400)


class GoalProposal(Model):
    intent: Intent
    patch: GoalPatch
    evidence: list[Evidence] = Field(max_length=10)
    questions: list[str] = Field(default_factory=list, max_length=4)


class Resource(Model):
    id: str
    name: str
    kind: Literal["draft", "room_hold", "sent_request"]
    content: str = Field(max_length=4000)
    status: str
    version: int = Field(default=1, ge=1)
    depends_on: list[str]
    assumption: str = Field(max_length=700)


class Message(Model):
    id: str
    subject: str
    content: str = Field(max_length=4000)
    created_at: str


class Workspace(Model):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=1)
    goal: Goal
    resources: list[Resource]
    messages: list[Message]


class Decision(Model):
    resource_id: str
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]
    seconds: float = Field(ge=0)
    model: str

    @model_validator(mode="after")
    def valid_distribution(self) -> Decision:
        if self.choice not in self.probabilities:
            raise ValueError("Decision choice is absent from its probability distribution.")
        if any(not 0 <= p <= 1 for p in self.probabilities.values()):
            raise ValueError("Invalid decision probability.")
        if abs(sum(self.probabilities.values()) - 1) > 0.01:
            raise ValueError("Decision probabilities do not sum to one.")
        return self


class Change(Model):
    resource_id: str
    operation: Operation
    before: str
    after: str = ""
    reason: str
    expected_version: int


class Preview(Model):
    base_revision: int
    goal: Goal
    changed_fields: list[str]
    intent: Intent
    changes: list[Change]
    decisions: list[Decision]
    questions: list[str]
    summary: str
    can_apply: bool
    cancellation_message: str = ""
    hash: str = ""


class Draft(Model):
    resource_id: str
    content: str = Field(min_length=1, max_length=4000)


class DraftBundle(Model):
    drafts: list[Draft] = Field(max_length=6)
    cancellation_message: str = Field(default="", max_length=4000)
    summary: str = Field(min_length=1, max_length=700)


class Run(Model):
    id: str
    request: str = Field(min_length=1, max_length=1500)
    base_revision: int
    status: Literal["queued", "running", "ready", "applied", "cancellation_requested", "failed"]
    created_at: str = Field(default_factory=timestamp)
    updated_at: str = Field(default_factory=timestamp)
    preview: Preview | None = None
    error: str | None = None
    applied_revision: int | None = None


class HistoryEvent(Model):
    id: str
    created_at: str = Field(default_factory=timestamp)
    title: str
    details: list[str]
    revision: int


class AnalyzeRequest(Model):
    id: UUID
    request: str = Field(min_length=1, max_length=1500)
    revision: int = Field(ge=1)


class Approval(Model):
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResetRequest(Model):
    confirm: Literal[True]
    revision: int = Field(ge=0)


class Job(Model):
    user_id: str = Field(min_length=1, max_length=100)
    run_id: UUID
