"""The ingestion write protocol (docs/adr/0002-postgres-pgvector.md
section 12.1). Lives next to its only implementer
(`ingestion/db/repository.py`) and its only caller (the ingestion
service) -- not in `shared/`, because no other module calls it.

Every method runs inside the caller-supplied `AsyncSession` transaction.
A method may `flush()` but must never independently `commit()` or
`rollback()` -- transaction control belongs to the ingestion service
alone (section 13: "The ingestion service owns exactly one AsyncSession
transaction per ingested item").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol, TypedDict

from ai_daily_digest.shared.schemas import DocumentSnapshot, SourceItem


class SourceItemMetadata(TypedDict):
    """The exact allowed-mutable `source_items` metadata set (ADR 0002
    section 12.1): `publisher`, `title`, `published_at`, `updated_at`,
    `authors`, `tags`, `language`, `event_id`. Deliberately excludes the
    five identity fields (`id`, `first_fetched_at`, `dedupe_key`,
    `source_id`, `canonical_url`) -- a `TypedDict` with exactly these
    eight keys makes "which columns may this write touch" a type-checked
    fact, not a convention a caller could accidentally violate by passing
    an extra field through `**kwargs`."""

    publisher: str
    title: str
    published_at: datetime | None
    updated_at: datetime | None
    authors: list[str]
    tags: list[str]
    language: str
    event_id: str | None


class IngestionWriteRepository(Protocol):
    """The three write operations the ingestion service needs, and only
    those three -- no generic `update(**fields)` passthrough, no setter
    for any of the five `source_items` identity fields, and no method
    that could reassign `id` (ADR 0002 section 11, layer 2: "the
    ingestion write protocol exposes no method that updates a protected
    column"). All three are idempotent under retries and concurrent
    duplicate ingestion."""

    # Six keyword-only parameters, matching ADR 0002 section 12.1's
    # method shape exactly (create-or-find identity plus initial
    # metadata) -- collapsing them into one dict/dataclass parameter
    # would trade this signature's explicitness for a shape the ADR
    # does not specify.
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
        """Create-or-find by canonical `dedupe_key`.

        **On find, this writes nothing** -- it never updates the
        allowed-mutable metadata set for an existing row, only reads it
        back. `metadata`, `item_id`, and `first_fetched_at` are then simply
        ignored for that call; the caller does not need to branch on
        which case occurred.

        **On create**, the new row is inserted with `item_id`,
        `first_fetched_at`, `dedupe_key`, `source_id`, `canonical_url`,
        and the initial `metadata` -- all set exactly once, at this
        insert, and never again (the storage-layer trigger backstops the
        five identity fields once persistence exists). A new item may
        therefore be inserted with its initial metadata before it has
        any snapshot.

        Deliberately **not** named or shaped as an "upsert": an upsert
        implies find-and-write, and this operation must never write on
        find (ADR 0002 section 12.1, section 13's write-sequence
        rationale)."""

    # Nine keyword-only parameters -- one per DocumentSnapshot storage
    # column, matching ADR 0002 section 9's schema exactly.
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
        """Insert a snapshot only when `(source_item_id, content_hash)`
        is new; return the existing row otherwise. Never creates a
        second row for identical content -- the snapshot idempotency
        key is `(source_item_id, content_hash)`, not `id`."""

    async def advance_latest_snapshot_and_metadata(
        self,
        *,
        source_item_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        fetched_at: datetime,
        metadata: SourceItemMetadata,
    ) -> bool:
        """After the snapshot row exists, conditionally advance
        `latest_snapshot_id` **and** the allowed-mutable metadata
        **together**, under the exact same condition: only when
        `snapshot_id` belongs to `source_item_id` and its `(fetched_at,
        snapshot_id)` tuple is newer than the currently referenced
        snapshot's tuple (or no snapshot is currently referenced yet).

        An older or equal candidate advances **neither** the pointer nor
        the metadata -- there is no path where one moves without the
        other. Returns whether the update applied.

        This name and signature exist specifically to make that joint
        condition part of the contract, replacing the earlier
        `advance_latest_snapshot(source_item_id, snapshot_id) -> bool`
        shape, which advanced the pointer conditionally but left
        metadata writes to a separate, unconditional step -- the
        regression ADR 0002 section 13 corrects."""
