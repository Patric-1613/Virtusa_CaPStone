"""Application-factory tests for the FastAPI foundation."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Annotated

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

import ai_daily_digest.delivery.api.app as app_module
from ai_daily_digest.delivery.api.app import create_app
from ai_daily_digest.delivery.api.errors import ErrorEnvelope


def _assert_uuid_v7(value: str) -> None:
    parsed = uuid.UUID(value)
    assert parsed.version == 7
    assert str(parsed) == value


def test_create_app_returns_independent_instances() -> None:
    first = create_app()
    second = create_app()

    assert first is not second
    assert first.state.readiness_registry is not second.state.readiness_registry


def test_import_does_not_create_a_process_global_application() -> None:
    assert not hasattr(app_module, "app")
    assert not any(isinstance(value, FastAPI) for value in vars(app_module).values())


def test_docs_configuration_keeps_openapi_enabled() -> None:
    enabled = TestClient(create_app(docs_enabled=True))
    disabled = TestClient(create_app(docs_enabled=False))

    assert enabled.get("/docs").status_code == 200
    assert enabled.get("/redoc").status_code == 200
    assert disabled.get("/docs").status_code == 404
    assert disabled.get("/redoc").status_code == 404
    assert disabled.get("/openapi.json").status_code == 200


def test_unknown_path_uses_standard_error_envelope() -> None:
    response = TestClient(create_app()).get("/v1/not-a-route")

    assert response.status_code == 404
    payload = ErrorEnvelope.model_validate(response.json())
    assert payload.error.code == "not_found"
    assert payload.error.details == {}
    assert "detail" not in response.json()
    _assert_uuid_v7(str(payload.error.request_id))


def test_unsupported_method_uses_envelope_and_preserves_allow_header() -> None:
    response = TestClient(create_app()).post("/v1/health/live")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    payload = ErrorEnvelope.model_validate(response.json())
    assert payload.error.code == "method_not_allowed"
    assert "detail" not in response.json()


class _ValidationPayload(BaseModel):
    count: int = Field(gt=0)


def test_validation_error_strips_inputs_headers_and_pydantic_context() -> None:
    app = create_app()

    @app.post("/_test/validation", include_in_schema=False)
    async def validation_target(
        payload: _ValidationPayload,
        x_private_value: Annotated[str, Header(min_length=20)],
    ) -> _ValidationPayload:
        return payload

    response = TestClient(app).post(
        "/_test/validation",
        json={"count": "body-secret-value"},
        headers={"x-private-value": "header-secret"},
    )

    assert response.status_code == 422
    payload = ErrorEnvelope.model_validate(response.json())
    assert payload.error.code == "validation_error"
    serialized = response.text
    assert "body-secret-value" not in serialized
    assert "header-secret" not in serialized
    assert '"input"' not in serialized
    assert '"ctx"' not in serialized
    issues = payload.error.details["errors"]
    assert isinstance(issues, list)
    assert issues
    assert all(set(issue) == {"loc", "type", "message"} for issue in issues)


def test_unexpected_exception_is_safe_and_log_uses_same_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/_test/failure", include_in_schema=False)
    async def failure_target() -> None:
        raise RuntimeError("private-dsn-and-credential")

    with caplog.at_level(logging.ERROR):
        response = TestClient(app, raise_server_exceptions=False).get("/_test/failure")

    assert response.status_code == 500
    payload = ErrorEnvelope.model_validate(response.json())
    assert payload.error.code == "internal_error"
    assert payload.error.message == "An unexpected server error occurred."
    assert "private-dsn-and-credential" not in response.text
    assert "traceback" not in response.text.lower()
    request_id = str(payload.error.request_id)
    _assert_uuid_v7(request_id)
    records = [record for record in caplog.records if record.message == "Unhandled API exception"]
    assert len(records) == 1
    assert vars(records[0])["request_id"] == request_id
    assert vars(records[0])["exception_type"] == "RuntimeError"
    assert "private-dsn-and-credential" not in caplog.text


def test_generated_request_ids_are_distinct_uuid_v7_values() -> None:
    client = TestClient(create_app())

    first = client.get("/missing").json()["error"]["request_id"]
    second = client.get("/missing").json()["error"]["request_id"]

    _assert_uuid_v7(first)
    _assert_uuid_v7(second)
    assert first != second
    assert re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", first)
