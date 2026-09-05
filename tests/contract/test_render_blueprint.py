"""Contract checks for the zero-cost Render deployment blueprint."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import yaml
from uvicorn.importer import import_from_string

from ai_daily_digest.delivery.api.production import UVICORN_FACTORY, create_production_app


def _blueprint() -> dict[str, Any]:
    loaded = yaml.safe_load(Path("render.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_blueprint_contains_only_free_api_and_static_site_services() -> None:
    services = _blueprint()["services"]

    assert [(service["name"], service["runtime"], service["plan"]) for service in services] == [
        ("ai-daily-digest-api", "python", "free"),
        ("ai-daily-digest-web", "static", "free"),
    ]
    assert all(service["type"] == "web" for service in services)
    assert not any(service.get("runtime") in {"cron", "postgres"} for service in services)


def test_api_start_command_targets_importable_factory_and_liveness_check() -> None:
    api = _blueprint()["services"][0]
    command = shlex.split(api["startCommand"])

    assert command == [
        ".venv/bin/uvicorn",
        UVICORN_FACTORY,
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "$PORT",
    ]
    assert import_from_string(UVICORN_FACTORY) is create_production_app
    assert api["healthCheckPath"] == "/v1/health/live"
    assert api["buildCommand"] == "pip install uv && uv sync --locked --no-dev --no-editable"


def test_blueprint_prompts_for_public_origins_and_generates_no_committed_secret() -> None:
    api, frontend = _blueprint()["services"]
    api_env = {item["key"]: item for item in api["envVars"]}
    frontend_env = {item["key"]: item for item in frontend["envVars"]}

    assert api_env["FRONTEND_ORIGIN"] == {"key": "FRONTEND_ORIGIN", "sync": False}
    assert api_env["PAGINATION_CURSOR_SECRET"] == {
        "key": "PAGINATION_CURSOR_SECRET",
        "generateValue": True,
    }
    assert frontend_env["VITE_API_BASE_URL"] == {
        "key": "VITE_API_BASE_URL",
        "sync": False,
    }
    assert frontend["rootDir"] == "frontend"
    assert frontend["buildCommand"] == "npm ci && npm run build"
    assert frontend["staticPublishPath"] == "./dist"
