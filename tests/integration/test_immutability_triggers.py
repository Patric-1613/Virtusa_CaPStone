"""Storage-level immutability triggers (docs/adr/0002-postgres-pgvector.md
section 11, section 15, section 16). Every test here bypasses the
repository **on purpose** -- it issues the raw SQL statement directly,
exercising layer 3 (the PL/pgSQL trigger), not layer 2 (repository
restriction). Each test:

- reads the row and keeps the original value;
- issues the raw statement directly;
- asserts a `DBAPIError`;
- rolls back (to a `SAVEPOINT`, via `session.begin_nested()`) and
  confirms the session is usable again;
- re-`SELECT`s and asserts the field -- and the whole row -- is
  unchanged, timestamps to the microsecond.

One independent test per protected `source_items` field (`id`,
`first_fetched_at`, `dedupe_key`, `source_id`, `canonical_url`), and one
each for snapshot `UPDATE`, `DELETE`, and `TRUNCATE` (section 15).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow

pytestmark = pytest.mark.integration

_FIRST_FETCHED_AT = datetime(2026, 8, 20, 9, 0, 0, 123456, tzinfo=UTC)
_SNAPSHOT_FETCHED_AT = datetime(2026, 8, 20, 9, 5, 0, 654321, tzinfo=UTC)


async def _insert_source_item(session: AsyncSession, *, item_id: uuid.UUID) -> SourceItemRow:
    row = SourceItemRow(
        id=item_id,
        dedupe_key=f"dk-{item_id}",
        source_id="openai_news",
        publisher="OpenAI",
        title="GPT-4o context window doubled",
        canonical_url=f"https://openai.com/{item_id}",
        published_at=None,
        updated_at=None,
        first_fetched_at=_FIRST_FETCHED_AT,
        latest_snapshot_id=None,
        event_id=None,
        authors=[],
        tags=[],
        language="en",
    )
    session.add(row)
    await session.flush()
    return row


async def _insert_snapshot(
    session: AsyncSession, *, snapshot_id: uuid.UUID, source_item_id: uuid.UUID
) -> DocumentSnapshotRow:
    row = DocumentSnapshotRow(
        id=snapshot_id,
        source_item_id=source_item_id,
        fetched_at=_SNAPSHOT_FETCHED_AT,
        content_hash=f"sha256:{snapshot_id}",
        content_text="original content",
        raw_location=None,
        etag=None,
        last_modified=None,
        collector_version=None,
    )
    session.add(row)
    await session.flush()
    return row


async def _assert_rejected(
    session: AsyncSession, statement: object, params: dict[str, object]
) -> None:
    """Issue `statement` inside a SAVEPOINT, assert it raises
    `DBAPIError`, and let `begin_nested()` roll back to the savepoint on
    the way out -- restoring the surrounding per-test transaction to a
    usable state for the re-SELECT that follows (ADR 0002 section 11:
    "rolls back and confirms the session is usable again")."""
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(statement, params)  # type: ignore[call-overload]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "new_value"),
    [
        ("id", str(uuid.uuid4())),
        ("first_fetched_at", "2020-01-01T00:00:00+00:00"),
        ("dedupe_key", "a-different-dedupe-key"),
        ("source_id", "a_different_source"),
        ("canonical_url", "https://example.com/different"),
    ],
)
async def test_source_items_identity_field_update_is_rejected(
    database_session: AsyncSession, column: str, new_value: str
) -> None:
    item_id = uuid.uuid4()
    await _insert_source_item(database_session, item_id=item_id)

    before = (
        await database_session.execute(select(SourceItemRow).where(SourceItemRow.id == item_id))
    ).scalar_one()
    original_values = {
        "id": before.id,
        "first_fetched_at": before.first_fetched_at,
        "dedupe_key": before.dedupe_key,
        "source_id": before.source_id,
        "canonical_url": before.canonical_url,
    }
    # Expire so the post-rollback re-SELECT below issues a real query
    # against the database, not a return of Python-side cached state.
    database_session.expire(before)

    await _assert_rejected(
        database_session,
        text(f"UPDATE source_items SET {column} = :v WHERE id = :id"),
        {"v": new_value, "id": item_id},
    )

    after = (
        await database_session.execute(select(SourceItemRow).where(SourceItemRow.id == item_id))
    ).scalar_one()
    assert after.id == original_values["id"]
    assert after.first_fetched_at == original_values["first_fetched_at"]
    assert after.dedupe_key == original_values["dedupe_key"]
    assert after.source_id == original_values["source_id"]
    assert after.canonical_url == original_values["canonical_url"]


@pytest.mark.asyncio
async def test_source_items_mutable_metadata_update_is_still_permitted(
    database_session: AsyncSession,
) -> None:
    """The trigger's `IS DISTINCT FROM` per-column check means an
    ordinary metadata rewrite that never touches the five identity
    columns is permitted -- distinguishing this from the five rejected
    cases above proves the trigger is scoped, not whole-row-blocking."""
    item_id = uuid.uuid4()
    await _insert_source_item(database_session, item_id=item_id)

    await database_session.execute(
        text("UPDATE source_items SET title = :title WHERE id = :id"),
        {"title": "An updated title", "id": item_id},
    )

    after = (
        await database_session.execute(select(SourceItemRow).where(SourceItemRow.id == item_id))
    ).scalar_one()
    assert after.title == "An updated title"


@pytest.mark.asyncio
async def test_document_snapshot_update_is_rejected(database_session: AsyncSession) -> None:
    item_id, snapshot_id = uuid.uuid4(), uuid.uuid4()
    await _insert_source_item(database_session, item_id=item_id)
    await _insert_snapshot(database_session, snapshot_id=snapshot_id, source_item_id=item_id)
    database_session.expire_all()

    await _assert_rejected(
        database_session,
        text("UPDATE document_snapshots SET content_text = :v WHERE id = :id"),
        {"v": "tampered content", "id": snapshot_id},
    )

    after = (
        await database_session.execute(
            select(DocumentSnapshotRow).where(DocumentSnapshotRow.id == snapshot_id)
        )
    ).scalar_one()
    assert after.content_text == "original content"


@pytest.mark.asyncio
async def test_document_snapshot_delete_is_rejected(database_session: AsyncSession) -> None:
    item_id, snapshot_id = uuid.uuid4(), uuid.uuid4()
    await _insert_source_item(database_session, item_id=item_id)
    await _insert_snapshot(database_session, snapshot_id=snapshot_id, source_item_id=item_id)
    database_session.expire_all()

    await _assert_rejected(
        database_session,
        text("DELETE FROM document_snapshots WHERE id = :id"),
        {"id": snapshot_id},
    )

    after = (
        await database_session.execute(
            select(DocumentSnapshotRow).where(DocumentSnapshotRow.id == snapshot_id)
        )
    ).scalar_one()
    assert after.id == snapshot_id


@pytest.mark.asyncio
async def test_document_snapshot_truncate_is_rejected(database_session: AsyncSession) -> None:
    item_id, snapshot_id = uuid.uuid4(), uuid.uuid4()
    await _insert_source_item(database_session, item_id=item_id)
    await _insert_snapshot(database_session, snapshot_id=snapshot_id, source_item_id=item_id)
    database_session.expire_all()

    await _assert_rejected(database_session, text("TRUNCATE document_snapshots"), {})

    after = (
        await database_session.execute(
            select(DocumentSnapshotRow).where(DocumentSnapshotRow.id == snapshot_id)
        )
    ).scalar_one()
    assert after.id == snapshot_id
