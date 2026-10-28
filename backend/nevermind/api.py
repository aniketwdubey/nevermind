from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from .clients import Nova
from .config import Settings
from .errors import AppError
from .models import AnalyzeRequest, Approval, ResetRequest
from .service import Service
from .store import Store

logger = logging.getLogger(__name__)


@lru_cache
def service() -> Service:
    settings = Settings()
    session = boto3.Session()
    network = Config(
        connect_timeout=5, read_timeout=80, retries={"total_max_attempts": 2, "mode": "adaptive"}
    )
    return Service(
        Store(session.client("dynamodb", config=network), settings.table_name),
        session.client("sqs", config=network),
        settings.queue_url,
        Nova(session.client("bedrock-runtime", config=network), settings.model_id),
        session.client(
            "bedrock-agentcore",
            config=Config(
                connect_timeout=5,
                read_timeout=110,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        ),
        settings.runtime_arn,
        settings.runtime_qualifier,
    )


def authenticated_user(request: Request) -> str:
    event = request.scope.get("aws.event", {})
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    user = claims.get("sub")
    if not isinstance(user, str) or claims.get("token_use") != "access":
        raise AppError("Sign in to use the workshop sandbox.", 401)
    try:
        return str(UUID(user))
    except ValueError as error:
        raise AppError("The signed-in identity is invalid.", 401) from error


User = Annotated[str, Depends(authenticated_user)]
Backend = Annotated[Service, Depends(service)]
app = FastAPI(title="Nevermind", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(AppError)
async def app_error(request: Request, error: AppError) -> JSONResponse:
    logger.warning("Request rejected path=%s status=%s", request.url.path, error.status)
    return JSONResponse({"error": str(error)}, status_code=error.status)


@app.exception_handler(ClientError)
async def aws_error(request: Request, error: ClientError) -> JSONResponse:
    logger.error(
        "AWS request failed code=%s request_id=%s",
        error.response["Error"]["Code"],
        error.response["ResponseMetadata"]["RequestId"],
    )
    return JSONResponse(
        {"error": "The service could not complete this request. Refresh and try again."},
        status_code=503,
    )


@app.middleware("http")
async def request_boundary(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    size = request.headers.get("content-length", "0")
    if not size.isdigit() or int(size) > 12_000 or len(await request.body()) > 12_000:
        return JSONResponse({"error": "The request is too large."}, status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/workspace")
def workspace(user: User, backend: Backend) -> dict[str, object]:
    state = backend.store.workspace(user)
    latest = backend.store.latest_run(user)
    return {"workspace": state, "history": backend.store.history(user), "run": latest}


@app.post("/api/workspace/reset")
def reset(payload: ResetRequest, user: User, backend: Backend) -> dict[str, object]:
    return {"workspace": backend.store.reset(user, payload.revision)}


@app.post("/api/runs", status_code=202)
def analyze(payload: AnalyzeRequest, user: User, backend: Backend) -> dict[str, object]:
    return {"run": backend.start(user, str(payload.id), payload.request, payload.revision)}


@app.get("/api/runs/{run_id}")
def run(run_id: UUID, user: User, backend: Backend) -> dict[str, object]:
    return {"run": backend.get_run(user, str(run_id))}


@app.post("/api/runs/{run_id}/apply")
def apply(run_id: UUID, approval: Approval, user: User, backend: Backend) -> dict[str, object]:
    return {"workspace": backend.approve(user, str(run_id), approval, cancellation=False)}


@app.post("/api/runs/{run_id}/request-cancellation")
def cancel(run_id: UUID, approval: Approval, user: User, backend: Backend) -> dict[str, object]:
    return {"workspace": backend.approve(user, str(run_id), approval, cancellation=True)}
