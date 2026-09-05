"""PostgreSQL implementation of the ingestion write protocol
(`ingestion/persistence.py`) and the shared source-item feed read
protocol (`shared/repositories.py`) -- docs/adr/0002-postgres-pgvector.md
sections 12.1, 12.2, 13.

Takes an injected `AsyncSession` -- never builds its own engine or pool
(section 12.4). Every method runs inside the caller-supplied session's
transaction; a method may `flush()` but never `commit()`s or
`rollback()`s on its own -- transaction control belongs to the ingestion
service alone (section 13).

`PostgresSourceItemRepository` is not a subclass of either Protocol --
both `IngestionWriteRepository` and `SourceItemFeedRepository` are
`typing.Protocol`s, satisfied structurally, exactly as
`shared/snapshot_resolver.py`'s `InMemorySnapshotResolver` satisfies
`SnapshotResolver`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow
from ai_daily_digest.ingestion.persistence import SourceItemMetadata
from ai_daily_digest.shared.repositories import FeedFilter
from ai_daily_digest.shared.schemas import DocumentSnapshot, SourceItem

# The exact "one condition" the joint pointer-and-metadata UPDATE is
# gated by (ADR 0002 section 13 step 3): the correlated subquery pulls
# the currently-referenced snapshot's `(fetched_at, id)` tuple, and the
# row-value comparison `(:fetched_at, :snapshot_id) > (subquery)` is
# PostgreSQL syntax not expressible through the ORM's portable query
# builder -- so this one statement is raw, fully parameterized text.
# Every value is a bound parameter; none is ever formatted into the SQL
# string itself (AGENTS.md: parameterized, never string interpolation).
_ADVANCE_LATEST_SNAPSHOT_AND_METADATA_SQL = text(
    """
    UPDATE source_items
    SET latest_snapshot_id = :snapshot_id,
        publisher = :publisher,
        title = :title,
        published_at = :published_at,
        updated_at = :updated_at,
        authors = :authors,
        tags = :tags,
        language = :language,
        event_id = :event_id
    WHERE id = :source_item_id
      AND (
        latest_snapshot_id IS NULL
        OR (:fetched_at, :snapshot_id) > (
            SELECT ds.fetched_at, ds.id
            FROM document_snapshots ds
            WHERE ds.id = source_items.latest_snapshot_id
        )
      )
    RETURNING id
    """
)

# `(first_fetched_at, id) < (after_ts, after_id)` -- the ADR 0008 section
# 4 keyset predicate. Row-value comparison against two bound parameters,
# also not expressible portably through the query builder for a
# heterogeneous (timestamp, uuid) tuple.
_KEYSET_PREDICATE_SQL = text("(first_fetched_at, id) < (:after_ts, :after_id)")


class PostgresSourceItemRepository:
    """The one production implementation of both the ingestion write
    protocol and the shared source-item feed read protocol."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- ingestion write protocol (ingestion/persistence.py) -----------

    # Matches IngestionWriteRepository's protocol signature exactly (see
    # its own too-many-arguments justification).
    async def find_or_create_source_item(  # pylint: disable=too-many-arguments
        self,
        *,
        item_id: uuid.UUID,
        dedupe_key: str,
        source_id: str,
        canonical_url: str,
        first_fetched_at: datetime,
        metadata: SourceItemMetadata,
    ) -> SourceItem:
        insert_stmt = (
            pg_insert(SourceItemRow)
            .values(
                id=item_id,
                dedupe_key=dedupe_key,
                source_id=source_id,
                canonical_url=canonical_url,
                first_fetched_at=first_fetched_at,
                publisher=metadata["publisher"],
                title=metadata["title"],
                published_at=metadata["published_at"],
                updated_at=metadata["updated_at"],
                authors=metadata["authors"],
                tags=metadata["tags"],
                language=metadata["language"],
                event_id=metadata["event_id"],
            )
            # Step 1 (ADR 0002 section 13): DO NOTHING, never DO UPDATE --
            # this branch must never write the allowed-mutable metadata
            # columns for an existing row. There is no conflict-time
            # metadata write to race on at all.
            .on_conflict_do_nothing(index_elements=[SourceItemRow.dedupe_key])
            .returning(SourceItemRow)
        )
        inserted = (await self._session.execute(insert_stmt)).scalars().one_or_none()
        if inserted is not None:
            return _source_item_from_row(inserted)

        # The item already existed. Read it back -- neither branch above
        # nor this SELECT writes the allowed-mutable metadata columns
        # (ADR 0002 section 12.1: "On find, it writes nothing").
        select_stmt = select(SourceItemRow).where(SourceItemRow.dedupe_key == dedupe_key)
        existing = (await self._session.execute(select_stmt)).scalars().one()
        return _source_item_from_row(existing)

    # Matches IngestionWriteRepository's protocol signature exactly (see
    # its own too-many-arguments justification).
    async def add_snapshot_if_new(  # pylint: disable=too-many-arguments
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
        insert_stmt = (
            pg_insert(DocumentSnapshotRow)
            .values(
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
            .on_conflict_do_nothing(
                index_elements=[
                    DocumentSnapshotRow.source_item_id,
                    DocumentSnapshotRow.content_hash,
                ]
            )
            .returning(DocumentSnapshotRow)
        )
        inserted = (await self._session.execute(insert_stmt)).scalars().one_or_none()
        if inserted is not None:
            return _document_snapshot_from_row(inserted)

        select_stmt = select(DocumentSnapshotRow).where(
            DocumentSnapshotRow.source_item_id == source_item_id,
            DocumentSnapshotRow.content_hash == content_hash,
        )
        existing = (await self._session.execute(select_stmt)).scalars().one()
        return _document_snapshot_from_row(existing)

    async def advance_latest_snapshot_and_metadata(
        self,
        *,
        source_item_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        fetched_at: datetime,
        metadata: SourceItemMetadata,
    ) -> bool:
        result = await self._session.execute(
            _ADVANCE_LATEST_SNAPSHOT_AND_METADATA_SQL,
            {
                "snapshot_id": snapshot_id,
                "source_item_id": source_item_id,
                "fetched_at": fetched_at,
                "publisher": metadata["publisher"],
                "title": metadata["title"],
                "published_at": metadata["published_at"],
                "updated_at": metadata["updated_at"],
                "authors": metadata["authors"],
                "tags": metadata["tags"],
                "language": metadata["language"],
                "event_id": metadata["event_id"],
            },
        )
        # RETURNING id yields exactly one row when the WHERE condition
        # matched (the pointer and all eight metadata columns advanced
        # together) and none when it didn't (neither advanced) -- there
        # is no partial-application case to check for.
        return result.one_or_none() is not None

    # -- shared read protocol (shared/repositories.py) ------------------

    async def list_source_items(
        self,
        *,
        feed_filter: FeedFilter | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 20,
    ) -> Sequence[SourceItem]:
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")
        stmt = select(SourceItemRow)
        # feed_filter.publisher/source_id are already-canonical (trimmed,
        # NFC-normalized, case-sensitive) per shared/repositories.py's
        # contract -- applied exactly as given, never re-normalized here.
        if feed_filter is not None and feed_filter.publisher is not None:
            stmt = stmt.where(SourceItemRow.publisher == feed_filter.publisher)
        if feed_filter is not None and feed_filter.source_id is not None:
            stmt = stmt.where(SourceItemRow.source_id == feed_filter.source_id)
        if after is not None:
            after_ts, after_id = after
            stmt = stmt.where(
                _KEYSET_PREDICATE_SQL.bindparams(after_ts=after_ts, after_id=after_id)
            )
        stmt = stmt.order_by(SourceItemRow.first_fetched_at.desc(), SourceItemRow.id.desc()).limit(
            limit + 1
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_source_item_from_row(row) for row in rows]


def _source_item_from_row(row: SourceItemRow) -> SourceItem:
    """Convert one persisted row into the shared `SourceItem` domain
    model. Uses `model_validate` on a plain dict, not the keyword
    constructor, so a raw `str` `canonical_url` is validated/coerced into
    `HttpUrl` the same way `intelligence/loaders.py::FixtureLoader`
    already converts fixture dicts -- one coercion path, not a second
    one reinvented here."""
    payload: dict[str, Any] = {
        "id": row.id,
        "dedupe_key": row.dedupe_key,
        "source_id": row.source_id,
        "publisher": row.publisher,
        "title": row.title,
        "canonical_url": row.canonical_url,
        "published_at": row.published_at,
        "updated_at": row.updated_at,
        "first_fetched_at": row.first_fetched_at,
        "latest_snapshot_id": row.latest_snapshot_id,
        "event_id": row.event_id,
        "authors": list(row.authors),
        "tags": list(row.tags),
        "language": row.language,
    }
    return SourceItem.model_validate(payload)


def _document_snapshot_from_row(row: DocumentSnapshotRow) -> DocumentSnapshot:
    payload: dict[str, Any] = {
        "id": row.id,
        "source_item_id": row.source_item_id,
        "fetched_at": row.fetched_at,
        "content_hash": row.content_hash,
        "content_text": row.content_text,
        "raw_location": row.raw_location,
        "etag": row.etag,
        "last_modified": row.last_modified,
        "collector_version": row.collector_version,
    }
    return DocumentSnapshot.model_validate(payload)
