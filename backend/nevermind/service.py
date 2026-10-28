from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from .clients import Decider, Nova
from .errors import AppError
from .models import Approval, DraftBundle, GoalProposal, Job, Run, Workspace, timestamp
from .planner import DRAFT_INSTRUCTIONS, PROPOSAL_INSTRUCTIONS, build_preview, draft_input, finalize
from .policy import apply_cancellation, apply_standard, proposed_goal, seal, verify_approval
from .store import Store

if TYPE_CHECKING:
    from mypy_boto3_bedrock_agentcore.client import BedrockAgentCoreClient
    from mypy_boto3_sqs.client import SQSClient

logger = logging.getLogger(__name__)


class Service:
    def __init__(
        self,
        store: Store,
        queue: SQSClient,
        queue_url: str,
        nova: Nova,
        runtime: BedrockAgentCoreClient,
        runtime_arn: str,
        qualifier: str,
    ) -> None:
        self.store, self.queue, self.queue_url = store, queue, queue_url
        self.nova, self.runtime = nova, runtime
        self.runtime_arn, self.qualifier = runtime_arn, qualifier

    def workspace(self, user: str) -> Workspace:
        state = self.store.workspace(user)
        if state is None:
            raise AppError("Load the prepared workshop first.", 404)
        return state

    def start(self, user: str, run_id: str, request: str, revision: int) -> Run:
        request = request.strip()
        if not request:
            raise AppError("Describe the change you want to explore.", 422)
        try:
            existing = self.store.run(user, run_id)
        except AppError as error:
            if error.status != 404:
                raise
        else:
            if existing.request != request or existing.base_revision != revision:
                raise AppError("This request ID was already used for a different analysis.")
            return existing
        state = self.workspace(user)
        if state.revision != revision:
            raise AppError("This plan changed; refresh it before requesting changes.")
        run = Run(id=run_id, request=request, base_revision=revision, status="queued")
        self.store.enqueue(user, run)
        try:
            self.queue.send_message(
                QueueUrl=self.queue_url,
                MessageBody=Job(user_id=user, run_id=UUID(run_id)).model_dump_json(),
            )
        except self.queue.exceptions.ClientError as error:
            run.status, run.error = "failed", "The analysis could not be queued. Nothing changed."
            self.store.finish(user, run)
            logger.error("Queue request failed code=%s", error.response["Error"]["Code"])
            raise AppError(run.error, 503) from error
        return run

    def get_run(self, user: str, run_id: str) -> Run:
        run = self.store.run(user, run_id)
        age = (datetime.now(UTC) - datetime.fromisoformat(run.updated_at)).total_seconds()
        if run.status in {"queued", "running"} and age > 360:
            run.status, run.error = (
                "failed",
                "Analysis timed out. Nothing changed. Please try again.",
            )
            self.store.finish(user, run)
        return run

    def approve(
        self, user: str, run_id: str, approval: Approval, *, cancellation: bool
    ) -> Workspace:
        state, run = self.workspace(user), self.store.run(user, run_id)
        verify_approval(state, run, approval.hash)
        if (not cancellation and run.status in {"applied", "cancellation_requested"}) or (
            cancellation and run.status == "cancellation_requested"
        ):
            return state
        transition = apply_cancellation if cancellation else apply_standard
        after, updated_run, event = transition(state, run, approval.hash)
        updated_run.updated_at = timestamp()
        self.store.commit(user, state, after, updated_run, event)
        return after

    def process(self, job: Job) -> None:
        run = self.store.claim(job.user_id, str(job.run_id))
        if run is None:
            return
        decider = Decider(self.runtime, self.runtime_arn, self.qualifier)
        try:
            state = self.workspace(job.user_id)
            if state.revision != run.base_revision:
                raise AppError("The workshop changed before analysis began. Please try again.")
            feedback = ""
            for attempt in range(2):
                proposal = self.nova.generate(
                    GoalProposal,
                    PROPOSAL_INSTRUCTIONS + feedback,
                    json.dumps(
                        {
                            "current_goal": state.goal.model_dump(mode="json"),
                            "user_request": run.request,
                        }
                    ),
                )
                try:
                    proposed_goal(state.goal, proposal, run.request)
                    break
                except AppError as error:
                    if attempt == 1:
                        raise
                    logger.warning("Requesting corrected goal proposal run_id=%s", run.id)
                    feedback = f"\nYour previous proposal failed server validation: {error}. Correct it; do not invent evidence."
            preview = build_preview(state, run.request, proposal, decider)
            if preview.questions:
                preview.summary = "Clarification is needed. Nothing has changed."
                preview = seal(preview)
            else:
                bundle = self.nova.generate(
                    DraftBundle, DRAFT_INSTRUCTIONS, draft_input(state, preview)
                )
                preview = finalize(preview, bundle)
            if self.workspace(job.user_id).revision != state.revision:
                raise AppError("The workshop changed during analysis. Please request a new review.")
            run.preview, run.status = preview, "ready"
            self.store.finish(job.user_id, run)
        except Exception as error:
            # Worker boundary: persist a visible failure instead of leaving a spinning job.
            logger.error("Analysis failed run_id=%s error_type=%s", run.id, type(error).__name__)
            run.status = "failed"
            run.error = (
                str(error)
                if isinstance(error, AppError)
                else ("A model or storage request failed. Nothing changed. Please try again.")
            )
            self.store.finish(job.user_id, run)
        finally:
            try:
                decider.close()
            except self.runtime.exceptions.ClientError as error:
                logger.error(
                    "Model session stop failed run_id=%s code=%s session_id=%s",
                    run.id,
                    error.response["Error"]["Code"],
                    decider.session,
                )
                raise
