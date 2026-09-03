"""Health-route and readiness-configuration tests."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from ai_daily_digest.delivery.api.app import create_app
from ai_daily_digest.delivery.api.errors import ErrorEnvelope


class _FakeProbe:
    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.call_count = 0

    async def is_ready(self) -> bool:
        self.call_count += 1
        return self.ready


class _FailingProbe:
    async def is_ready(self) -> bool:
        raise ConnectionError("postgresql://user:password@private-host/database")


def test_liveness_is_typed_and_does_not_run_readiness_probes() -> None:
    probe = _FakeProbe(ready=False)
    client = TestClient(
        create_app(required_dependencies=("database",), readiness_probes={"database": probe})
    )

    response = client.get("/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert probe.call_count == 0


def test_readiness_succeeds_with_explicitly_empty_required_set() -> None:
    response = TestClient(create_app()).get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": []}


def test_readiness_succeeds_with_injected_ready_probes_in_stable_order() -> None:
    database = _FakeProbe(ready=True)
    search = _FakeProbe(ready=True)
    client = TestClient(
        create_app(
            required_dependencies=("search", "database"),
            readiness_probes={"database": database, "search": search},
        )
    )

    response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": [
            {"name": "database", "status": "ready"},
            {"name": "search", "status": "ready"},
        ],
    }
    assert database.call_count == 1
    assert search.call_count == 1


def test_readiness_failure_returns_only_safe_coarse_statuses() -> None:
    client = TestClient(
        create_app(
            required_dependencies=("database", "search"),
            readiness_probes={
                "database": _FakeProbe(ready=True),
                "search": _FakeProbe(ready=False),
            },
        )
    )

    response = client.get("/v1/health/ready")

    assert response.status_code == 503
    payload = ErrorEnvelope.model_validate(response.json())
    assert payload.error.code == "service_unavailable"
    assert payload.error.details == {
        "checks": [
            {"name": "database", "status": "ready"},
            {"name": "search", "status": "not_ready"},
        ]
    }


def test_probe_exception_fails_closed_without_exposing_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(
        create_app(
            required_dependencies=("database",),
            readiness_probes={"database": _FailingProbe()},
        )
    )

    with caplog.at_level(logging.ERROR):
        response = client.get("/v1/health/ready")

    assert response.status_code == 503
    assert "postgresql" not in response.text
    request_id = response.json()["error"]["request_id"]
    records = [
        record
        for record in caplog.records
        if record.message == "Readiness probe raised an exception"
    ]
    assert len(records) == 1
    assert vars(records[0])["request_id"] == request_id
    assert vars(records[0])["dependency"] == "database"
    assert vars(records[0])["exception_type"] == "ConnectionError"
    assert "postgresql://" not in caplog.text


@pytest.mark.parametrize(
    ("required", "probes", "match"),
    [
        (("database",), {}, "no readiness probe"),
        (("database", "database"), {"database": _FakeProbe(ready=True)}, "must be unique"),
        (("Database Host",), {"Database Host": _FakeProbe(ready=True)}, "invalid"),
        (("database",), {"database": object()}, "no callable is_ready"),
    ],
)
def test_invalid_readiness_configuration_is_rejected_before_startup(
    required: tuple[str, ...], probes: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        create_app(required_dependencies=required, readiness_probes=probes)  # type: ignore[arg-type]
