"""`PostgresSourceItemRepository` against real PostgreSQL
(docs/adr/0002-postgres-pgvector.md sections 8, 9, 12.1, 13, 15, 16).

Covers: find-vs-create metadata behaviour; the two `UNIQUE` constraints
and their idempotent-under-retry convergence; foreign-key behaviour;
UUID v7 and `timestamptz` round-tripping; and the four mandatory
latest-pointer-and-metadata concurrency cases that are this file's
reason for existing (the Finding 1 regression ADR 0002 section 13
corrects).

Concurrency cases use `open_database_session` -- two genuinely separate
connections that really commit, not the single per-test rollback
transaction `database_session` provides (section 15: "a single per-test
rollback fixture does not isolate every integration test").
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_daily_digest.ingestion.db.models import SourceItemRow
from ai_daily_digest.ingestion.db.repository import PostgresSourceItemRepository
from ai_daily_digest.ingestion.persistence import SourceItemMetadata
from ai_daily_digest.shared.ids import new_id

pytestmark = pytest.mark.integration

_OpenSession = Callable[[], AbstractAsyncContextManager[AsyncSession]]


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


@pytest.mark.asyncio
async def test_find_or_create_inserts_a_new_item_with_initial_metadata(
    database_session: AsyncSession,
) -> None:
    repo = PostgresSourceItemRepository(database_session)
    item_id = new_id()

    item = await repo.find_or_create_source_item(
        item_id=item_id,
        dedupe_key=f"dk-{item_id}",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, 0, 123456, tzinfo=UTC),
        metadata=_metadata(title="Initial title"),
    )
    await database_session.flush()

    assert item.id == item_id
    assert item.title == "Initial title"
    assert item.latest_snapshot_id is None
    # ADR 0007: a new_id() value round-trips through the native uuid
    # column unchanged and canonical.
    assert str(item.id) == str(item_id)


@pytest.mark.asyncio
async def test_find_or_create_on_existing_row_writes_no_metadata(
    database_session: AsyncSession,
) -> None:
    repo = PostgresSourceItemRepository(database_session)
    item_id = new_id()
    dedupe_key = f"dk-{item_id}"

    original = await repo.find_or_create_source_item(
        item_id=item_id,
        dedupe_key=dedupe_key,
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(title="Original title"),
    )
    await database_session.flush()

    found = await repo.find_or_create_source_item(
        item_id=new_id(),  # a different candidate id -- must be ignored
        dedupe_key=dedupe_key,
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(title="A later title that must not win"),
    )

    assert found.id == original.id
    assert found.title == "Original title"


@pytest.mark.asyncio
async def test_dedupe_key_unique_constraint_converges_concurrent_inserts(
    open_database_session: _OpenSession,
) -> None:
    """Two separate connections race to insert the same `dedupe_key`;
    both commit for real; the loser converges on the winner's row via
    `ON CONFLICT DO NOTHING` + re-SELECT (ADR 0002 section 13's
    "Uniqueness-conflict handling") -- no duplicate row, no error
    surfaced to either caller."""
    dedupe_key = f"dk-{uuid.uuid4()}"
    fetched_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

    async with open_database_session() as session_a:
        repo_a = PostgresSourceItemRepository(session_a)
        item_a = await repo_a.find_or_create_source_item(
            item_id=new_id(),
            dedupe_key=dedupe_key,
            source_id="openai_news",
            canonical_url="https://openai.com/a",
            first_fetched_at=fetched_at,
            metadata=_metadata(title="From connection A"),
        )
        await session_a.commit()

    async with open_database_session() as session_b:
        repo_b = PostgresSourceItemRepository(session_b)
        item_b = await repo_b.find_or_create_source_item(
            item_id=new_id(),
            dedupe_key=dedupe_key,
            source_id="openai_news",
            canonical_url="https://openai.com/a",
            first_fetched_at=fetched_at,
            metadata=_metadata(title="From connection B"),
        )
        await session_b.commit()

    assert item_a.id == item_b.id
    assert item_b.title == "From connection A"


@pytest.mark.asyncio
async def test_add_snapshot_if_new_is_idempotent_for_identical_content(
    database_session: AsyncSession,
) -> None:
    repo = PostgresSourceItemRepository(database_session)
    item_id = new_id()
    await repo.find_or_create_source_item(
        item_id=item_id,
        dedupe_key=f"dk-{item_id}",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(),
    )
    await database_session.flush()

    kwargs = {
        "source_item_id": item_id,
        "fetched_at": datetime(2026, 8, 20, 9, 5, tzinfo=UTC),
        "content_hash": "sha256:same-content",
        "content_text": "content",
        "raw_location": None,
        "etag": None,
        "last_modified": None,
        "collector_version": None,
    }
    first = await repo.add_snapshot_if_new(snapshot_id=new_id(), **kwargs)  # type: ignore[arg-type]
    await database_session.flush()
    second = await repo.add_snapshot_if_new(snapshot_id=new_id(), **kwargs)  # type: ignore[arg-type]

    assert first.id == second.id


@pytest.mark.asyncio
async def test_snapshot_for_missing_source_item_is_rejected(
    database_session: AsyncSession,
) -> None:
    repo = PostgresSourceItemRepository(database_session)
    with pytest.raises(DBAPIError):
        await repo.add_snapshot_if_new(
            snapshot_id=new_id(),
            source_item_id=new_id(),  # never inserted
            fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
            content_hash="sha256:orphan",
            content_text="content",
            raw_location=None,
            etag=None,
            last_modified=None,
            collector_version=None,
        )


@pytest.mark.asyncio
async def test_source_item_with_snapshots_cannot_be_deleted(
    database_session: AsyncSession,
) -> None:
    repo = PostgresSourceItemRepository(database_session)
    item_id = new_id()
    await repo.find_or_create_source_item(
        item_id=item_id,
        dedupe_key=f"dk-{item_id}",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(),
    )
    await repo.add_snapshot_if_new(
        snapshot_id=new_id(),
        source_item_id=item_id,
        fetched_at=datetime(2026, 8, 20, 9, 5, tzinfo=UTC),
        content_hash="sha256:x",
        content_text="content",
        raw_location=None,
        etag=None,
        last_modified=None,
        collector_version=None,
    )
    await database_session.flush()

    with pytest.raises(IntegrityError):
        async with database_session.begin_nested():
            await database_session.execute(delete(SourceItemRow).where(SourceItemRow.id == item_id))


@pytest.mark.asyncio
async def test_advance_out_of_order_commit_leaves_pointer_and_metadata_at_greatest_tuple(
    open_database_session: _OpenSession,
) -> None:
    """Concurrency case 1 (ADR 0002 section 15): two different
    snapshots, carrying different mutable metadata, committed
    out-of-arrival-order on separate connections leave both
    `latest_snapshot_id` and the mutable metadata corresponding to the
    same greatest `(fetched_at, id)` candidate."""
    item_id = new_id()
    older_snapshot_id, newer_snapshot_id = new_id(), new_id()
    older_fetched_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    newer_fetched_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    async with open_database_session() as setup_session:
        repo = PostgresSourceItemRepository(setup_session)
        await repo.find_or_create_source_item(
            item_id=item_id,
            dedupe_key=f"dk-{item_id}",
            source_id="openai_news",
            canonical_url="https://openai.com/a",
            first_fetched_at=older_fetched_at,
            metadata=_metadata(),
        )
        for snapshot_id, fetched_at in (
            (older_snapshot_id, older_fetched_at),
            (newer_snapshot_id, newer_fetched_at),
        ):
            await repo.add_snapshot_if_new(
                snapshot_id=snapshot_id,
                source_item_id=item_id,
                fetched_at=fetched_at,
                content_hash=f"sha256:{snapshot_id}",
                content_text="content",
                raw_location=None,
                etag=None,
                last_modified=None,
                collector_version=None,
            )
        await setup_session.commit()

    # The NEWER snapshot's transaction commits FIRST -- out of arrival
    # order relative to the OLDER one, which commits second.
    async with open_database_session() as session_newer:
        await PostgresSourceItemRepository(session_newer).advance_latest_snapshot_and_metadata(
            source_item_id=item_id,
            snapshot_id=newer_snapshot_id,
            fetched_at=newer_fetched_at,
            metadata=_metadata(title="From the newer snapshot"),
        )
        await session_newer.commit()

    async with open_database_session() as session_older:
        applied = await PostgresSourceItemRepository(
            session_older
        ).advance_latest_snapshot_and_metadata(
            source_item_id=item_id,
            snapshot_id=older_snapshot_id,
            fetched_at=older_fetched_at,
            metadata=_metadata(title="From the older snapshot -- must not win"),
        )
        await session_older.commit()

    assert applied is False
    async with open_database_session() as verify_session:
        item = (
            await verify_session.execute(select(SourceItemRow).where(SourceItemRow.id == item_id))
        ).scalar_one()
        assert item.latest_snapshot_id == newer_snapshot_id
        assert item.title == "From the newer snapshot"


@pytest.mark.asyncio
async def test_advance_older_retry_regresses_neither_pointer_nor_metadata(
    open_database_session: _OpenSession,
) -> None:
    """Concurrency case 2: an older retry (a candidate whose
    `(fetched_at, id)` loses the comparison) regresses neither the
    pointer nor the metadata."""
    item_id = new_id()
    snapshot_id = new_id()
    fetched_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    async with open_database_session() as session:
        repo = PostgresSourceItemRepository(session)
        await repo.find_or_create_source_item(
            item_id=item_id,
            dedupe_key=f"dk-{item_id}",
            source_id="openai_news",
            canonical_url="https://openai.com/a",
            first_fetched_at=fetched_at,
            metadata=_metadata(),
        )
        await repo.add_snapshot_if_new(
            snapshot_id=snapshot_id,
            source_item_id=item_id,
            fetched_at=fetched_at,
            content_hash="sha256:x",
            content_text="content",
            raw_location=None,
            etag=None,
            last_modified=None,
            collector_version=None,
        )
        await repo.advance_latest_snapshot_and_metadata(
            source_item_id=item_id,
            snapshot_id=snapshot_id,
            fetched_at=fetched_at,
            metadata=_metadata(title="Established title"),
        )
        await session.commit()

    # A retry of the SAME candidate the pointer already references --
    # not strictly newer, so it must not "reapply" and must not be
    # reported as applied (equal, not greater, loses the comparison).
    async with open_database_session() as retry_session:
        applied = await PostgresSourceItemRepository(
            retry_session
        ).advance_latest_snapshot_and_metadata(
            source_item_id=item_id,
            snapshot_id=snapshot_id,
            fetched_at=fetched_at,
            metadata=_metadata(title="Retried title that must not apply"),
        )
        await retry_session.commit()

    assert applied is False
    async with open_database_session() as verify_session:
        item = (
            await verify_session.execute(select(SourceItemRow).where(SourceItemRow.id == item_id))
        ).scalar_one()
        assert item.latest_snapshot_id == snapshot_id
        assert item.title == "Established title"


@pytest.mark.asyncio
async def test_advance_losing_transaction_writes_zero_of_the_eight_metadata_columns(
    open_database_session: _OpenSession,
) -> None:
    """Concurrency case 3: a losing candidate writes zero of the eight
    mutable columns, not some -- the pointer and metadata are one
    conditional UPDATE, so there is no partial-application outcome."""
    item_id = new_id()
    winner_id, loser_id = new_id(), new_id()
    winner_fetched_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    loser_fetched_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

    async with open_database_session() as session:
        repo = PostgresSourceItemRepository(session)
        await repo.find_or_create_source_item(
            item_id=item_id,
            dedupe_key=f"dk-{item_id}",
            source_id="openai_news",
            canonical_url="https://openai.com/a",
            first_fetched_at=loser_fetched_at,
            metadata=_metadata(title="Baseline title", authors=["A"], tags=["t1"], event_id="ev-1"),
        )
        for snapshot_id, fetched_at in (
            (winner_id, winner_fetched_at),
            (loser_id, loser_fetched_at),
        ):
            await repo.add_snapshot_if_new(
                snapshot_id=snapshot_id,
                source_item_id=item_id,
                fetched_at=fetched_at,
                content_hash=f"sha256:{snapshot_id}",
                content_text="content",
                raw_location=None,
                etag=None,
                last_modified=None,
                collector_version=None,
            )
        await repo.advance_latest_snapshot_and_metadata(
            source_item_id=item_id,
            snapshot_id=winner_id,
            fetched_at=winner_fetched_at,
            metadata=_metadata(
                title="Winning title", authors=["Winner"], tags=["win"], event_id="ev-win"
            ),
        )
        await session.commit()

    async with open_database_session() as loser_session:
        applied = await PostgresSourceItemRepository(
            loser_session
        ).advance_latest_snapshot_and_metadata(
            source_item_id=item_id,
            snapshot_id=loser_id,
            fetched_at=loser_fetched_at,
            metadata=_metadata(
                title="Losing title",
                authors=["Loser"],
                tags=["lose"],
                event_id="ev-lose",
                language="fr",
                published_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        )
        await loser_session.commit()

    assert applied is False
    async with open_database_session() as verify_session:
        item = (
            await verify_session.execute(select(SourceItemRow).where(SourceItemRow.id == item_id))
        ).scalar_one()
        # Every one of the eight mutable columns must still read back as
        # the winner's value -- not a mix of winner and loser fields.
        assert item.latest_snapshot_id == winner_id
        assert item.title == "Winning title"
        assert item.authors == ["Winner"]
        assert item.tags == ["win"]
        assert item.event_id == "ev-win"
        assert item.language == "en"
        assert item.published_at is None
        assert item.updated_at is None


@pytest.mark.asyncio
async def test_duplicate_content_fetch_leaves_metadata_unchanged(
    database_session: AsyncSession,
) -> None:
    """Concurrency case 4 -- the documented Phase-1 limitation (ADR 0002
    section 13): a duplicate-content fetch (no new content_hash, so no
    new snapshot) never reaches advance_latest_snapshot_and_metadata at
    all, so stored metadata is unchanged even when the candidate's
    metadata differs."""
    repo = PostgresSourceItemRepository(database_session)
    item_id = new_id()
    await repo.find_or_create_source_item(
        item_id=item_id,
        dedupe_key=f"dk-{item_id}",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(title="Original title"),
    )
    snapshot = await repo.add_snapshot_if_new(
        snapshot_id=new_id(),
        source_item_id=item_id,
        fetched_at=datetime(2026, 8, 20, 9, 5, tzinfo=UTC),
        content_hash="sha256:unchanged",
        content_text="content",
        raw_location=None,
        etag=None,
        last_modified=None,
        collector_version=None,
    )
    await repo.advance_latest_snapshot_and_metadata(
        source_item_id=item_id,
        snapshot_id=snapshot.id,
        fetched_at=snapshot.fetched_at,
        metadata=_metadata(title="Original title"),
    )
    await database_session.flush()

    # A re-fetch of byte-identical content -- e.g. a publisher rename
    # observed only through a duplicate fetch. add_snapshot_if_new
    # returns the EXISTING row (same content_hash); the caller's
    # write-sequence step 3 only runs "if, and only if, a new snapshot
    # was inserted" (section 13) -- simulated here by simply never
    # calling advance again, matching the real ingestion service's
    # control flow.
    duplicate = await repo.add_snapshot_if_new(
        snapshot_id=new_id(),
        source_item_id=item_id,
        fetched_at=datetime(2026, 8, 21, 9, 5, tzinfo=UTC),
        content_hash="sha256:unchanged",  # identical -- no new snapshot
        content_text="content",
        raw_location=None,
        etag=None,
        last_modified=None,
        collector_version=None,
    )
    assert duplicate.id == snapshot.id

    item = (
        await database_session.execute(select(SourceItemRow).where(SourceItemRow.id == item_id))
    ).scalar_one()
    assert item.title == "Original title"
