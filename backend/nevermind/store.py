from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import (
        AttributeValueTypeDef,
        PutTypeDef,
        TransactWriteItemTypeDef,
    )

from .errors import AppError
from .fixture import prepared_workshop
from .models import HistoryEvent, Model, Run, Workspace, timestamp

logger = logging.getLogger(__name__)


class Store:
    def __init__(self, client: DynamoDBClient, table: str) -> None:
        self.client = client
        self.table = table

    @staticmethod
    def key(user: str, name: str) -> dict[str, AttributeValueTypeDef]:
        return {"pk": {"S": f"USER#{user}"}, "sk": {"S": name}}

    def item(self, user: str, name: str, model: Model) -> dict[str, AttributeValueTypeDef]:
        body = model.model_dump_json()
        if len(body.encode()) > 250_000:
            raise AppError(
                "This workspace is too large. Download your drafts and start a new sandbox.", 413
            )
        item = self.key(user, name) | {"body": {"S": body}}
        if isinstance(model, Workspace):
            item["revision"] = {"N": str(model.revision)}
        if isinstance(model, Run):
            item["status"] = {"S": model.status}
            item["expires_at"] = {"N": str(int(time.time()) + 7 * 86400)}
        return item

    def body(self, user: str, name: str) -> str | None:
        item = self.client.get_item(
            TableName=self.table, Key=self.key(user, name), ConsistentRead=True
        ).get("Item")
        return item["body"]["S"] if item else None

    def workspace(self, user: str) -> Workspace | None:
        body = self.body(user, "WORKSPACE")
        return Workspace.model_validate_json(body) if body else None

    def run(self, user: str, run_id: str) -> Run:
        body = self.body(user, f"RUN#{run_id}")
        if body is None:
            raise AppError("This analysis could not be found.", 404)
        return Run.model_validate_json(body)

    def history(self, user: str) -> list[HistoryEvent]:
        response = self.client.query(
            TableName=self.table,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={":pk": {"S": f"USER#{user}"}, ":prefix": {"S": "EVENT#"}},
            ScanIndexForward=False,
            Limit=30,
            ConsistentRead=True,
        )
        return [
            HistoryEvent.model_validate_json(item["body"]["S"])
            for item in response.get("Items", [])
        ]

    def latest_run(self, user: str) -> Run | None:
        item = self.client.get_item(
            TableName=self.table,
            Key=self.key(user, "LATEST"),
            ConsistentRead=True,
        ).get("Item")
        if not item:
            return None
        try:
            return self.run(user, item["run_id"]["S"])
        except AppError as error:
            if error.status != 404:
                raise
            logger.info("Latest analysis has expired.")
            return None

    def transaction(self, items: list[TransactWriteItemTypeDef]) -> None:
        try:
            self.client.transact_write_items(TransactItems=items)
        except self.client.exceptions.TransactionCanceledException as error:
            logger.warning(
                "State transaction rejected request_id=%s",
                error.response["ResponseMetadata"]["RequestId"],
            )
            raise AppError(
                "The workspace changed, another analysis is active, or today's analysis limit was reached. Refresh and review again."
            ) from error

    def reset(self, user: str, expected: int) -> Workspace:
        state = prepared_workshop(expected + 1)
        condition = "attribute_not_exists(pk)" if expected == 0 else "revision = :revision"
        put: PutTypeDef = {
            "TableName": self.table,
            "Item": self.item(user, "WORKSPACE", state),
            "ConditionExpression": condition,
        }
        if expected:
            put["ExpressionAttributeValues"] = {":revision": {"N": str(expected)}}
        event = HistoryEvent(
            id=f"reset-{state.revision}",
            title="Prepared workshop loaded",
            details=["Synthetic workshop data loaded. No real bookings or messages were sent."],
            revision=state.revision,
        )
        self.transaction(
            [
                {"Put": put},
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.item(user, f"EVENT#{event.created_at}#{event.id}", event),
                    }
                },
                {"Delete": {"TableName": self.table, "Key": self.key(user, "LATEST")}},
            ]
        )
        return state

    def enqueue(self, user: str, run: Run) -> None:
        now = int(time.time())
        day = datetime.now(UTC).date().isoformat()
        self.transaction(
            [
                {
                    "ConditionCheck": {
                        "TableName": self.table,
                        "Key": self.key(user, "WORKSPACE"),
                        "ConditionExpression": "revision = :revision",
                        "ExpressionAttributeValues": {":revision": {"N": str(run.base_revision)}},
                    }
                },
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.item(user, f"RUN#{run.id}", run),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.key(user, "LOCK")
                        | {"run_id": {"S": run.id}, "expires_at": {"N": str(now + 600)}},
                        "ConditionExpression": "attribute_not_exists(pk) OR expires_at < :now",
                        "ExpressionAttributeValues": {":now": {"N": str(now)}},
                    }
                },
                {
                    "Update": {
                        "TableName": self.table,
                        "Key": self.key(user, f"QUOTA#{day}"),
                        "UpdateExpression": "ADD analyses :one",
                        "ConditionExpression": "attribute_not_exists(analyses) OR analyses < :maximum",
                        "ExpressionAttributeValues": {":one": {"N": "1"}, ":maximum": {"N": "20"}},
                    }
                },
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.key(user, "LATEST") | {"run_id": {"S": run.id}},
                    }
                },
            ]
        )

    def claim(self, user: str, run_id: str) -> Run | None:
        run = self.run(user, run_id)
        if run.status != "queued":
            return None
        run = run.model_copy(update={"status": "running", "updated_at": timestamp()})
        try:
            self.client.put_item(
                TableName=self.table,
                Item=self.item(user, f"RUN#{run.id}", run),
                ConditionExpression="#status = :queued",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":queued": {"S": "queued"}},
            )
        except self.client.exceptions.ConditionalCheckFailedException:
            logger.info("Analysis was already claimed run_id=%s", run_id)
            return None
        return run

    def finish(self, user: str, run: Run) -> None:
        run.updated_at = timestamp()
        self.transaction(
            [
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.item(user, f"RUN#{run.id}", run),
                        "ConditionExpression": "#status IN (:queued, :running)",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {
                            ":queued": {"S": "queued"},
                            ":running": {"S": "running"},
                        },
                    }
                },
                {
                    "Delete": {
                        "TableName": self.table,
                        "Key": self.key(user, "LOCK"),
                        "ConditionExpression": "run_id = :id",
                        "ExpressionAttributeValues": {":id": {"S": run.id}},
                    }
                },
            ]
        )

    def commit(
        self, user: str, before: Workspace, after: Workspace, run: Run, event: HistoryEvent
    ) -> None:
        previous_status = "ready" if run.status == "applied" else "applied"
        self.transaction(
            [
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.item(user, "WORKSPACE", after),
                        "ConditionExpression": "revision = :revision",
                        "ExpressionAttributeValues": {":revision": {"N": str(before.revision)}},
                    }
                },
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.item(user, f"RUN#{run.id}", run),
                        "ConditionExpression": "#status = :expected",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {":expected": {"S": previous_status}},
                    }
                },
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self.item(user, f"EVENT#{event.created_at}#{event.id}", event),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
            ]
        )
