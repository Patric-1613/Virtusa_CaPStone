"""In-memory fake satisfying both `IngestionWriteRepository`
(`ingestion/persistence.py`) and `SourceItemFeedRepository`
(`shared/repositories.py`) structurally -- unit tests for ingestion and
delivery logic use this, never a real database
(docs/adr/0002-postgres-pgvector.md section 15: "Unit tests for delivery
and ingestion logic use the in-memory fake repositories -- no database
at all").

Mirrors `shared/snapshot_resolver.py::InMemorySnapshotResolver`'s shape,
but lives in `tests/`, not `shared/`: this is a test double for a
protocol whose one production implementation is PostgreSQL-specific
(the row-value tuple comparisons in `PostgresSourceItemRepository`), not
a second real implementation a production caller might select.

Every method reproduces the same conditional semantics the real
PostgreSQL repository implements (ADR 0002 section 13) -- a test written
against this fake exercises the same contract a test against real
PostgreSQL would: find-or-create writes no metadata on find, and the
pointer-and-metadata advance is gated by one `(fetched_at, id)`
comparison, never split into two independent writes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ai_daily_digest.ingestion.persistence import SourceItemMetadata
from ai_daily_digest.shared.repositories import FeedFilter
from ai_daily_digest.shared.schemas import DocumentSnapshot, SourceItem


class InMemorySourceItemRepository:
    """A plain-dict-backed fake. Not thread-safe and not process-shared
    -- exactly like `InMemorySnapshotResolver`, it exists to let a unit
    test exercise ingestion/delivery logic without any infrastructure."""

    def __init__(self) -> None:
        self._items_by_id: dict[uuid.UUID, SourceItem] = {}
        self._item_id_by_dedupe_key: dict[str, uuid.UUID] = {}
        self._snapshots_by_id: dict[uuid.UUID, DocumentSnapshot] = {}
        self._snapshot_id_by_item_and_hash: dict[tuple[uuid.UUID, str], uuid.UUID] = {}

    # -- IngestionWriteRepository ---------------------------------------

    async def find_or_create_source_item(
        self,
        *,
        item_id: uuid.UUID,
        dedupe_key: str,
        source_id: str,
        canonical_url: str,
        first_fetched_at: datetime,
        metadata: SourceItemMetadata,
    ) -> SourceItem:
        existing_id = self._item_id_by_dedupe_key.get(dedupe_key)
        if existing_id is not None:
            # On find, this writes nothing -- the existing row is
            # returned exactly as stored; `metadata` for this call is
            # discarded, matching the real repository's contract.
            return self._items_by_id[existing_id]

        payload: dict[str, Any] = {
            "id": item_id,
            "dedupe_key": dedupe_key,
            "source_id": source_id,
            "canonical_url": canonical_url,
            "first_fetched_at": first_fetched_at,
            "latest_snapshot_id": None,
            **metadata,
        }
        item = SourceItem.model_validate(payload)
        self._items_by_id[item_id] = item
        self._item_id_by_dedupe_key[dedupe_key] = item_id
        return item

    async def add_snapshot_if_new(
        self,
        *,
        snapshot_id: uuid.UUID,
        source_item_id: uuid.UUID,
        fetched_at: datetime,
        content_hash: str,
        content_text: str,
        raw_location: str | None,
        etag: str | None,
        last_modified: str | None,
        collector_version: str | None,
    ) -> DocumentSnapshot:
        key = (source_item_id, content_hash)
        existing_id = self._snapshot_id_by_item_and_hash.get(key)
        if existing_id is not None:
            return self._snapshots_by_id[existing_id]

        snapshot = DocumentSnapshot(
            id=snapshot_id,
            source_item_id=source_item_id,
            fetched_at=fetched_at,
            content_hash=content_hash,
            content_text=content_text,
            raw_location=raw_location,
            etag=etag,
            last_modified=last_modified,
            collector_version=collector_version,
        )
        self._snapshots_by_id[snapshot_id] = snapshot
        self._snapshot_id_by_item_and_hash[key] = snapshot_id
        return snapshot

    async def advance_latest_snapshot_and_metadata(
        self,
        *,
        source_item_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        fetched_at: datetime,
        metadata: SourceItemMetadata,
    ) -> bool:
        item = self._items_by_id.get(source_item_id)
        if item is None:
            return False

        current_snapshot_id = item.latest_snapshot_id
        if current_snapshot_id is not None:
            current_snapshot = self._snapshots_by_id[current_snapshot_id]
            current_tuple = (current_snapshot.fetched_at, current_snapshot.id)
            candidate_tuple = (fetched_at, snapshot_id)
            # An older-or-equal candidate advances neither the pointer
            # nor the metadata -- the same joint condition
            # PostgresSourceItemRepository's one conditional UPDATE
            # enforces (ADR 0002 section 13 step 3).
            if candidate_tuple <= current_tuple:
                return False

        # `update=` here never names `id` or `first_fetched_at` --
        # SourceItem's two protected ordering-tuple fields -- only
        # `latest_snapshot_id` and the eight allowed-mutable metadata
        # keys, matching the real repository's exact column set.
        update: dict[str, Any] = {"latest_snapshot_id": snapshot_id, **metadata}
        self._items_by_id[source_item_id] = item.model_copy(update=update)
        return True

    # -- SourceItemFeedRepository ---------------------------------------

    async def list_source_items(
        self,
        *,
        feed_filter: FeedFilter | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 20,
    ) -> Sequence[SourceItem]:
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")
        items = list(self._items_by_id.values())
        if feed_filter is not None and feed_filter.publisher is not None:
            items = [item for item in items if item.publisher == feed_filter.publisher]
        if feed_filter is not None and feed_filter.source_id is not None:
            items = [item for item in items if item.source_id == feed_filter.source_id]
        if after is not None:
            after_ts, after_id = after
            items = [
                item for item in items if (item.first_fetched_at, item.id) < (after_ts, after_id)
            ]
        items.sort(key=lambda item: (item.first_fetched_at, item.id), reverse=True)
        return items[: limit + 1]
