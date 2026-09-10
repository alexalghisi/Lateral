"""Health probes.

ARCHITECTURAL DECISION -- liveness and readiness are separate endpoints with
deliberately different dependency footprints.

    /health/live   Is the process alive? Touches nothing external.
                   A failure here means "restart me".

    /health/ready  Can the process serve traffic right now? Verifies the
                   database. A failure here means "stop routing to me, but
                   leave me running".

Collapsing these into a single `/health` is a common and expensive mistake.
If liveness checks the database, a thirty-second database failover makes every
replica report unhealthy, the orchestrator restarts all of them at once, and a
transient dependency blip becomes a full outage compounded by cold starts.

The probes are also excluded from the OpenAPI schema. They are infrastructure
contracts for the orchestrator and the load balancer, not part of the public
API surface offered to clients.
"""

from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DbSession

router = APIRouter(prefix="/health", tags=["health"], include_in_schema=False)


class LivenessResponse(BaseModel):
    """Payload of a successful liveness probe."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Payload of a readiness probe, successful or not."""

    status: Literal["ready", "unready"]
    database: Literal["ok", "unavailable"]


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
def liveness() -> LivenessResponse:
    """Report that the process is running and the ASGI stack is serving.

    Intentionally dependency-free. If this handler executes at all, the answer
    is yes -- which is precisely the question a liveness probe asks.
    """
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
def readiness(session: DbSession) -> ReadinessResponse | JSONResponse:
    """Report whether the process can currently serve requests.

    `SELECT 1` is the cheapest statement that proves the full path works:
    a connection was obtained from the pool, authentication succeeded, and the
    server is answering. It reads no application table, so it stays valid
    before the first migration has ever run.

    A dependency failure is reported as 503 rather than 500. The distinction is
    operationally meaningful: 503 says "this replica is temporarily unable to
    serve", which is exactly what a load balancer needs in order to drain
    traffic, whereas 500 asserts an application defect and points responders in
    the wrong direction.
    """
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # The exception is swallowed on purpose. This endpoint is polled every
        # few seconds by infrastructure; propagating the driver error would
        # bury genuine signal under thousands of identical stack traces during
        # any outage. The 503 itself is the alertable signal.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ReadinessResponse(status="unready", database="unavailable").model_dump(),
        )

    return ReadinessResponse(status="ready", database="ok")
