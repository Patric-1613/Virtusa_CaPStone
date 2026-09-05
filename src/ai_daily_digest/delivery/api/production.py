"""Side-effect-free Uvicorn factory for the deployed Delivery API."""

from __future__ import annotations

from fastapi import FastAPI

from ai_daily_digest.delivery.api.app import create_app
from ai_daily_digest.delivery.api.config import DeliverySettings

UVICORN_FACTORY = "ai_daily_digest.delivery.api.production:create_production_app"


def create_production_app() -> FastAPI:
    """Build the HTTP process from validated deployment environment settings."""
    settings = DeliverySettings.from_environment()
    return create_app(
        docs_enabled=settings.docs_enabled,
        cursor_signing_key=settings.pagination_cursor_secret,
        frontend_origin=settings.frontend_origin,
    )
