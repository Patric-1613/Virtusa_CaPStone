"""Cursor-paginated updates endpoint — ADR 0008 PR 4."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from ai_daily_digest.delivery.api.dependencies import (
    get_cursor_codec,
    get_source_item_feed_repository,
)
from ai_daily_digest.delivery.api.errors import ErrorEnvelope, error_response
from ai_daily_digest.delivery.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    CursorCodec,
    FilterInput,
    FilterValidationError,
    InvalidCursorError,
    Page,
    build_cursor_payload,
    canonicalize_filters,
)
from ai_daily_digest.delivery.api.schemas import UpdateSummary
from ai_daily_digest.shared.repositories import SourceItemFeedRepository

__all__ = ["UPDATES_RESOURCE", "UPDATES_SORT", "router"]

UPDATES_RESOURCE = "updates"
UPDATES_SORT = "first_fetched_at:desc,id:desc"

router = APIRouter(prefix="/v1", tags=["updates"])


@router.get(
    "/updates",
    summary="List source updates",
    operation_id="get_updates",
    response_model=Page[UpdateSummary],
    responses={
        200: {"description": "A cursor-paginated page of source updates."},
        400: {"model": ErrorEnvelope, "description": "Invalid pagination cursor."},
        422: {"model": ErrorEnvelope, "description": "Invalid query parameter or filter."},
    },
)
async def get_updates(
    request: Request,
    cursor_codec: Annotated[CursorCodec, Depends(get_cursor_codec)],
    repository: Annotated[SourceItemFeedRepository, Depends(get_source_item_feed_repository)],
    limit: Annotated[
        int,
        Query(
            ge=MIN_LIMIT,
            le=MAX_LIMIT,
            description="Maximum number of items to return in the page.",
        ),
    ] = DEFAULT_LIMIT,
    cursor: Annotated[
        str | None,
        Query(
            description="Opaque cursor for resuming pagination from a previous page.",
        ),
    ] = None,
    publisher: Annotated[
        str | None,
        Query(
            description="Filter by publisher name (case-sensitive, trimmed).",
        ),
    ] = None,
    source_id: Annotated[
        str | None,
        Query(
            description="Filter by source identifier (case-sensitive, trimmed).",
        ),
    ] = None,
) -> Response | Page[UpdateSummary]:
    """Return a cursor-paginated list of source updates ordered by (first_fetched_at DESC, id DESC)."""
    raw_filters: dict[str, FilterInput] = {}
    if publisher is not None:
        raw_filters["publisher"] = publisher
    if source_id is not None:
        raw_filters["source_id"] = source_id

    try:
        for filter_name, filter_val in raw_filters.items():
            if isinstance(filter_val, str) and not filter_val.strip():
                raise FilterValidationError(f"{filter_name} must not be empty or whitespace-only")
        canonical_filters = canonicalize_filters(
            resource=UPDATES_RESOURCE,
            sort=UPDATES_SORT,
            filters=raw_filters,
        )
    except FilterValidationError:
        return error_response(
            request,
            status_code=422,
            code="validation_error",
            message="Invalid filter parameter.",
        )

    after: tuple[datetime, uuid.UUID] | None = None
    if cursor is not None:
        try:
            payload = cursor_codec.decode(cursor, filters=canonical_filters)
            after_dt = datetime.strptime(payload.k.t, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
            after = (after_dt, payload.k.id)
        except InvalidCursorError:
            return error_response(
                request,
                status_code=400,
                code="invalid_cursor",
                message="The pagination cursor is invalid for this request.",
            )

    canonical_dict = canonical_filters.as_dict()
    filter_publisher: str | None = None
    filter_source_id: str | None = None
    if "publisher" in canonical_dict and isinstance(canonical_dict["publisher"], str):
        filter_publisher = canonical_dict["publisher"]
    if "source_id" in canonical_dict and isinstance(canonical_dict["source_id"], str):
        filter_source_id = canonical_dict["source_id"]

    items = await repository.list_source_items(
        publisher=filter_publisher,
        source_id=filter_source_id,
        after=after,
        limit=limit,
    )

    has_more = len(items) > limit
    returned_items = items[:limit]

    next_cursor: str | None = None
    if has_more and returned_items:
        last_item = returned_items[-1]
        cursor_payload = build_cursor_payload(
            filters=canonical_filters,
            last_sort_value=last_item.first_fetched_at,
            last_id=last_item.id,
        )
        next_cursor = cursor_codec.encode(cursor_payload)

    summaries = [
        UpdateSummary(
            id=item.id,
            source_id=item.source_id,
            publisher=item.publisher,
            title=item.title,
            canonical_url=item.canonical_url,
            published_at=item.published_at,
            first_fetched_at=item.first_fetched_at,
            event_id=item.event_id,
            tags=list(item.tags),
            language=item.language,
            latest_snapshot_id=item.latest_snapshot_id,
        )
        for item in returned_items
    ]

    return Page[UpdateSummary](items=summaries, next_cursor=next_cursor)
