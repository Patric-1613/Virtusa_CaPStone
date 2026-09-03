"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ai_daily_digest.delivery.api.dependencies import (
    ReadinessRegistry,
    get_readiness_registry,
    get_request_id,
)
from ai_daily_digest.delivery.api.errors import (
    SERVICE_UNAVAILABLE_CODE,
    ApiError,
    ErrorEnvelope,
)
from ai_daily_digest.shared.ids import Uuid7Id

router = APIRouter(prefix="/v1/health", tags=["health"])


class LiveResponse(BaseModel):
    """Process liveness response with no infrastructure checks."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class ReadinessCheckResponse(BaseModel):
    """Safe public status for one configured dependency."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["ready", "not_ready"]


class ReadyResponse(BaseModel):
    """Successful readiness response."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"] = "ready"
    checks: list[ReadinessCheckResponse]


@router.get(
    "/live",
    operation_id="get_health_live",
    response_model=LiveResponse,
    responses={500: {"model": ErrorEnvelope, "description": "Unexpected server failure"}},
)
async def get_health_live() -> LiveResponse:
    """Report only whether the API process can answer an HTTP request."""
    return LiveResponse()


@router.get(
    "/ready",
    operation_id="get_health_ready",
    response_model=ReadyResponse,
    responses={
        503: {"model": ErrorEnvelope, "description": "A required dependency is unavailable"},
        500: {"model": ErrorEnvelope, "description": "Unexpected server failure"},
    },
)
async def get_health_ready(
    registry: Annotated[ReadinessRegistry, Depends(get_readiness_registry)],
    request_id: Annotated[Uuid7Id, Depends(get_request_id)],
) -> ReadyResponse:
    """Report readiness for the explicitly configured required dependencies."""
    checks = await registry.evaluate(request_id=request_id)
    public_checks = [
        ReadinessCheckResponse(name=check.name, status="ready" if check.ready else "not_ready")
        for check in checks
    ]
    if not all(check.ready for check in checks):
        raise ApiError(
            status_code=503,
            code=SERVICE_UNAVAILABLE_CODE,
            message="One or more required dependencies are unavailable.",
            details={"checks": [check.model_dump(mode="json") for check in public_checks]},
        )
    return ReadyResponse(checks=public_checks)
