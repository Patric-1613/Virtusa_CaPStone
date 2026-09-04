"""Executable OpenAPI contract tests for ADR 0010's HTTP foundation."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from ai_daily_digest.delivery.api.app import API_TITLE, API_VERSION, create_app

pytestmark = pytest.mark.contract

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _operations(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        operation
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in _HTTP_METHODS
    ]


def test_generated_openapi_is_deterministic_across_fresh_apps() -> None:
    first = json.dumps(create_app().openapi(), sort_keys=True, separators=(",", ":"))
    second = json.dumps(create_app().openapi(), sort_keys=True, separators=(",", ":"))

    assert first == second


def test_openapi_metadata_is_explicit_safe_and_openapi_3_1() -> None:
    schema = create_app().openapi()

    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["title"] == API_TITLE
    assert schema["info"]["version"] == API_VERSION
    assert "contact" not in schema["info"]
    assert "servers" not in schema
    serialized = json.dumps(schema).lower()
    for forbidden in ("@", "github.com", "localhost", "/private/", "file://"):
        assert forbidden not in serialized


def test_only_implemented_paths_appear_in_openapi() -> None:
    schema = create_app().openapi()

    assert set(schema["paths"]) == {"/v1/health/live", "/v1/health/ready", "/v1/updates"}
    assert set(schema["paths"]["/v1/health/live"]) == {"get"}
    assert set(schema["paths"]["/v1/health/ready"]) == {"get"}
    assert set(schema["paths"]["/v1/updates"]) == {"get"}


def test_operation_ids_are_explicit_unique_and_stable_snake_case() -> None:
    operations = _operations(create_app().openapi())
    operation_ids = [operation["operationId"] for operation in operations]

    assert set(operation_ids) == {"get_health_live", "get_health_ready", "get_updates"}
    assert len(operation_ids) == len(set(operation_ids))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", operation_id) for operation_id in operation_ids)


def test_schema_component_names_and_responses_are_stable() -> None:
    schema = create_app().openapi()
    component_names = set(schema["components"]["schemas"])

    assert component_names == {
        "ErrorBody",
        "ErrorEnvelope",
        "LiveResponse",
        "Page_UpdateSummary_",
        "ReadinessCheckResponse",
        "ReadyResponse",
        "UpdateSummary",
        "Uuid7Id",
    }
    ready_responses = schema["paths"]["/v1/health/ready"]["get"]["responses"]
    assert ready_responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReadyResponse"
    )
    assert ready_responses["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorEnvelope"
    )
    updates_responses = schema["paths"]["/v1/updates"]["get"]["responses"]
    assert updates_responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/Page_UpdateSummary_"
    )
    assert updates_responses["400"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorEnvelope"
    )
    assert updates_responses["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorEnvelope"
    )


def test_interactive_docs_flag_does_not_change_executable_openapi() -> None:
    assert create_app(docs_enabled=True).openapi() == create_app(docs_enabled=False).openapi()
