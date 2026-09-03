"""Safe, uniform HTTP error responses for the Delivery API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from ai_daily_digest.delivery.api.dependencies import get_request_id
from ai_daily_digest.shared.ids import Uuid7Id

VALIDATION_ERROR_CODE = "validation_error"
NOT_FOUND_CODE = "not_found"
METHOD_NOT_ALLOWED_CODE = "method_not_allowed"
SERVICE_UNAVAILABLE_CODE = "service_unavailable"
INTERNAL_ERROR_CODE = "internal_error"


class ErrorBody(BaseModel):
    """The inner, stable API error contract."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: Uuid7Id
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """The public wrapper used for every HTTP error."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


class ApiError(Exception):
    """A known API failure with an explicit public code and safe message."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})


def error_response(  # pylint: disable=too-many-arguments
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the standard envelope using the request's existing correlation ID."""
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=get_request_id(request),
            details=dict(details or {}),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=dict(headers or {}),
    )


def _sanitized_validation_details(exc: RequestValidationError) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    for issue in exc.errors():
        location = [part if isinstance(part, int) else str(part) for part in issue.get("loc", ())]
        errors.append(
            {
                "loc": location,
                "type": str(issue.get("type", "validation_error")),
                "message": "Invalid request value.",
            }
        )
    return {"errors": errors}


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    api_error = exc
    if not isinstance(api_error, ApiError):  # Defensive narrowing for type checkers.
        raise TypeError("API error handler received an unexpected exception type")
    return error_response(
        request,
        status_code=api_error.status_code,
        code=api_error.code,
        message=api_error.message,
        details=api_error.details,
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_error = exc
    if not isinstance(validation_error, RequestValidationError):
        raise TypeError("validation handler received an unexpected exception type")
    return error_response(
        request,
        status_code=422,
        code=VALIDATION_ERROR_CODE,
        message="The request could not be validated.",
        details=_sanitized_validation_details(validation_error),
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    http_error = exc
    if not isinstance(http_error, StarletteHTTPException):
        raise TypeError("HTTP error handler received an unexpected exception type")

    if http_error.status_code == 404:
        code = NOT_FOUND_CODE
        message = "The requested endpoint was not found."
    elif http_error.status_code == 405:
        code = METHOD_NOT_ALLOWED_CODE
        message = "The requested method is not allowed for this endpoint."
    else:
        code = "http_error"
        message = "The HTTP request could not be completed."

    safe_headers: dict[str, str] = {}
    if http_error.status_code == 405 and http_error.headers:
        allow = http_error.headers.get("Allow")
        if allow is not None:
            safe_headers["Allow"] = allow
    return error_response(
        request,
        status_code=http_error.status_code,
        code=code,
        message=message,
        headers=safe_headers,
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers for known application and framework errors."""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
