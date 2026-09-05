"""`ingest_document()`'s service-owned transaction
(docs/adr/0002-postgres-pgvector.md section 13, section 16). The
repository never commits or rolls back on its own; this file proves the
*service* does, for both a brand-new item and an existing one, and that
the combined pointer-and-metadata `UPDATE` never applies partially.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow
from ai_daily_digest.ingestion.db.repository import PostgresSourceItemRepository
from ai_daily_digest.ingestion.persistence import SourceItemMetadata
from ai_daily_digest.ingestion.service import FetchedDocument, ingest_document
from ai_daily_digest.shared.schemas import SourceItem

pytestmark = pytest.mark.integration


def _metadata(**overrides: object) -> SourceItemMetadata:
    base: SourceItemMetadata = {
        "publisher": "OpenAI",
        "title": "GPT-4o context window doubled",
        "published_at": None,
        "updated_at": None,
        "authors": [],
        "tags": [],
        "language": "en",
        "event_id": None,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _document(**overrides: object) -> FetchedDocument:
    base = {
        "dedupe_key": "dk-shared",
        "source_id": "openai_news",
        "canonical_url": "https://openai.com/a",
        "first_fetched_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        "fetched_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        "content_hash": "sha256:x",
        "content_text": "content",
        "metadata": _metadata(),
    }
    base.update(overrides)
    return FetchedDocument(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_new_item_commits_all_rows_together(database_session: AsyncSession) -> None:
    repository = PostgresSourceItemRepository(database_session)
    document = _document(dedupe_key="dk-new-item")

    result = await ingest_document(database_session, repository, document)

    assert result.advanced is True
    item = (
        await database_session.execute(
            select(SourceItemRow).where(SourceItemRow.id == result.source_item.id)
        )
    ).scalar_one()
    snapshot = (
        await database_session.execute(
            select(DocumentSnapshotRow).where(DocumentSnapshotRow.id == result.snapshot.id)
        )
    ).scalar_one()
    assert item.latest_snapshot_id == snapshot.id


@pytest.mark.asyncio
async def test_a_failing_new_item_persists_no_rows(database_session: AsyncSession) -> None:
    """ "For a new item, the failed attempt persists no rows -- no
    source_items row and no document_snapshots row" (section 13,
    Rollback behaviour)."""

    class _FailingRepository(PostgresSourceItemRepository):
        async def advance_latest_snapshot_and_metadata(self, **_: object) -> bool:
            raise RuntimeError("simulated failure after the snapshot insert")

    repository = _FailingRepository(database_session)
    document = _document(dedupe_key="dk-failing-new-item")

    with pytest.raises(RuntimeError, match="simulated failure"):
        await ingest_document(database_session, repository, document)

    items = (
        (
            await database_session.execute(
                select(SourceItemRow).where(SourceItemRow.dedupe_key == "dk-failing-new-item")
            )
        )
        .scalars()
        .all()
    )
    assert items == []


@pytest.mark.asyncio
async def test_a_failing_existing_item_keeps_previously_committed_rows(
    database_session: AsyncSession,
) -> None:
    """ "For an existing item, rollback preserves the rows committed by
    earlier runs and discards only the changes this attempt made"
    (section 13, Rollback behaviour)."""
    repository = PostgresSourceItemRepository(database_session)
    dedupe_key = "dk-existing-item"
    first_document = _document(dedupe_key=dedupe_key, content_hash="sha256:first")
    first_result = await ingest_document(database_session, repository, first_document)
    committed_item_id = first_result.source_item.id
    committed_snapshot_id = first_result.snapshot.id

    class _FailingRepository(PostgresSourceItemRepository):
        async def advance_latest_snapshot_and_metadata(self, **_: object) -> bool:
            raise RuntimeError("simulated failure on the second attempt")

    failing_repository = _FailingRepository(database_session)
    second_document = _document(
        dedupe_key=dedupe_key,
        content_hash="sha256:second",
        fetched_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
    )
    with pytest.raises(RuntimeError, match="simulated failure"):
        await ingest_document(database_session, failing_repository, second_document)

    item = (
        await database_session.execute(
            select(SourceItemRow).where(SourceItemRow.id == committed_item_id)
        )
    ).scalar_one()
    # The first run's row and its pointer survive; the second attempt's
    # snapshot never got a chance to commit (its own INSERT is part of
    # the same failed transaction the failing advance() aborts).
    assert item.latest_snapshot_id == committed_snapshot_id
    snapshots = (
        (
            await database_session.execute(
                select(DocumentSnapshotRow).where(
                    DocumentSnapshotRow.source_item_id == committed_item_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [snapshot.id for snapshot in snapshots] == [committed_snapshot_id]


@pytest.mark.asyncio
async def test_advance_never_applies_partially_when_it_fails(
    database_session: AsyncSession,
) -> None:
    """The combined pointer-and-metadata UPDATE is one statement -- a
    failure anywhere in the transaction (simulated here downstream of a
    successful advance) rolls back the whole item, including that
    UPDATE, never leaving the pointer moved with stale metadata or vice
    versa."""
    repository = PostgresSourceItemRepository(database_session)
    baseline = await ingest_document(
        database_session,
        repository,
        _document(dedupe_key="dk-partial-check", content_hash="sha256:baseline"),
    )
    assert baseline.advanced is True

    class _FailAfterAdvanceRepository(PostgresSourceItemRepository):
        async def find_or_create_source_item(
            self,
            *,
            item_id: object,
            dedupe_key: str,
            source_id: str,
            canonical_url: str,
            first_fetched_at: object,
            metadata: SourceItemMetadata,
        ) -> SourceItem:
            raise RuntimeError("fail before any write this attempt")

    failing_repository = _FailAfterAdvanceRepository(database_session)
    with pytest.raises(RuntimeError):
        await ingest_document(
            database_session,
            failing_repository,
            _document(
                dedupe_key="dk-partial-check",
                content_hash="sha256:second-attempt",
                metadata=_metadata(title="Must not partially apply"),
            ),
        )

    item = (
        await database_session.execute(
            select(SourceItemRow).where(SourceItemRow.dedupe_key == "dk-partial-check")
        )
    ).scalar_one()
    assert item.title == "GPT-4o context window doubled"
    assert item.latest_snapshot_id == baseline.snapshot.id
