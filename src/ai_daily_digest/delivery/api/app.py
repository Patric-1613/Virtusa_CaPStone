"""FastAPI application factory for the public Delivery API."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping

from fastapi import FastAPI, Request, Response

from ai_daily_digest.delivery.api.dependencies import (
    ReadinessProbe,
    build_readiness_registry,
)
from ai_daily_digest.delivery.api.errors import (
    INTERNAL_ERROR_CODE,
    error_response,
    install_exception_handlers,
)
from ai_daily_digest.delivery.api.pagination import CursorCodec
from ai_daily_digest.delivery.api.routes.health import router as health_router
from ai_daily_digest.delivery.api.routes.updates import router as updates_router
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.repositories import SourceItemFeedRepository

API_TITLE = "AI Daily Digest API"
API_VERSION = "0.1.0"

LOGGER = logging.getLogger(__name__)

type RequestHandler = Callable[[Request], Awaitable[Response]]


def create_app(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    *,
    docs_enabled: bool = True,
    required_dependencies: Iterable[str] = (),
    readiness_probes: Mapping[str, ReadinessProbe] | None = None,
    database_readiness_probe: ReadinessProbe | None = None,
    source_item_feed_repository: SourceItemFeedRepository | None = None,
    cursor_codec: CursorCodec | None = None,
    cursor_signing_key: bytes | None = None,
) -> FastAPI:
    """Create an independent, side-effect-free FastAPI application instance.

    `database_readiness_probe` and `source_item_feed_repository` are both
    optional and both `None` by default: a foundation-only app with no
    database configured passes neither, and `required_dependencies` stays
    exactly what the caller passed. When `database_readiness_probe` **is**
    given, it is wired into readiness under the name `"database"` and that
    name is added to the required set automatically (ADR 0002 section 14:
    "When `DATABASE_URL` is configured and any route needs the database,
    `'database'` goes into `required_dependencies`") -- a caller that
    configures the probe does not also need to remember to list
    `"database"` itself.

    `source_item_feed_repository`, when given, is stored on `app.state`
    and mounts `GET /v1/updates` (ADR 0008 PR 4). Mounting is
    fail-closed on cursor configuration: a configured repository with
    neither `cursor_codec` nor `cursor_signing_key` raises `ValueError`
    at `create_app()` time rather than serving pagination with no way to
    produce a valid cursor.
    """
    merged_probes = dict(readiness_probes or {})
    # A tuple, not `set(required_dependencies)`: build_readiness_registry()
    # itself rejects a duplicate name ("required dependency names must
    # be unique") -- silently deduplicating here would swallow that
    # caller mistake instead of surfacing it. "database" is appended
    # only when it is not already present, so create_app() never
    # introduces a duplicate of its own.
    merged_required = tuple(required_dependencies)
    if database_readiness_probe is not None:
        merged_probes["database"] = database_readiness_probe
        if "database" not in merged_required:
            merged_required = (*merged_required, "database")
    readiness_registry = build_readiness_registry(
        required_dependencies=merged_required,
        probes=merged_probes,
    )
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        openapi_version="3.1.0",
        openapi_url="/openapi.json",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        contact=None,
        servers=None,
    )
    app.state.readiness_registry = readiness_registry
    app.state.source_item_feed_repository = source_item_feed_repository

    if cursor_codec is not None:
        app.state.cursor_codec = cursor_codec
    elif cursor_signing_key is not None:
        app.state.cursor_codec = CursorCodec(cursor_signing_key)
    elif source_item_feed_repository is not None:
        raise ValueError(
            "cursor_codec or cursor_signing_key is required when "
            "source_item_feed_repository is configured"
        )
    else:
        app.state.cursor_codec = None

    @app.middleware("http")
    async def add_request_context(request: Request, call_next: RequestHandler) -> Response:
        request.state.request_id = new_id()
        try:
            return await call_next(request)
        # This is the final HTTP safety boundary: unknown application failures
        # must become the generic 500 envelope and must never escape to clients.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.error(
                "Unhandled API exception",
                extra={
                    "request_id": str(request.state.request_id),
                    "exception_type": type(exc).__name__,
                },
            )
            return error_response(
                request,
                status_code=500,
                code=INTERNAL_ERROR_CODE,
                message="An unexpected server error occurred.",
            )

    install_exception_handlers(app)
    app.include_router(health_router)
    if source_item_feed_repository is not None:
        app.include_router(updates_router)
    return app
