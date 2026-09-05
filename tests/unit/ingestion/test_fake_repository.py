"""Unit tests for the in-memory fake (`fake_repository.py`) -- these
also serve as the executable specification of the ingestion write
protocol and the shared read protocol, so a future `PostgresSourceItemRepository`
integration test (`tests/integration/test_source_item_repository.py`)
can assert the identical behaviour against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from ai_daily_digest.ingestion.persistence import SourceItemMetadata
from tests.unit.ingestion.fake_repository import InMemorySourceItemRepository

ITEM_1 = uuid.UUID("01a01e6a-a260-79e3-9a2b-1e4e705d3101")
ITEM_2 = uuid.UUID("01a01e6a-a260-79e3-9a2b-1e4e705d3102")
SNAPSHOT_1 = uuid.UUID("01a01e6a-a260-79e3-9a2b-1e4e705d3201")
SNAPSHOT_2 = uuid.UUID("01a01e6a-a260-79e3-9a2b-1e4e705d3202")
SNAPSHOT_3 = uuid.UUID("01a01e6a-a260-79e3-9a2b-1e4e705d3203")


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
async def test_find_or_create_inserts_a_new_item_with_initial_metadata() -> None:
    repo = InMemorySourceItemRepository()
    fetched_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

    item = await repo.find_or_create_source_item(
        item_id=ITEM_1,
        dedupe_key="dk-1",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=fetched_at,
        metadata=_metadata(title="Initial title"),
    )

    assert item.id == ITEM_1
    assert item.dedupe_key == "dk-1"
    assert item.title == "Initial title"
    assert item.latest_snapshot_id is None


@pytest.mark.asyncio
async def test_find_or_create_on_existing_dedupe_key_writes_no_metadata() -> None:
    """ADR 0002 section 12.1: "On find, it writes nothing." A second
    call with a different id/metadata for the same dedupe_key must
    return the original row, untouched."""
    repo = InMemorySourceItemRepository()
    fetched_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    original = await repo.find_or_create_source_item(
        item_id=ITEM_1,
        dedupe_key="dk-1",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=fetched_at,
        metadata=_metadata(title="Original title"),
    )

    found = await repo.find_or_create_source_item(
        item_id=ITEM_2,  # a different candidate id -- must be ignored
        dedupe_key="dk-1",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=fetched_at,
        metadata=_metadata(title="A different, later title"),
    )

    assert found == original
    assert found.id == ITEM_1
    assert found.title == "Original title"


@pytest.mark.asyncio
async def test_add_snapshot_if_new_is_idempotent_for_identical_content() -> None:
    repo = InMemorySourceItemRepository()
    fetched_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

    first = await repo.add_snapshot_if_new(
        snapshot_id=SNAPSHOT_1,
        source_item_id=ITEM_1,
        fetched_at=fetched_at,
        content_hash="sha256:same",
        content_text="content",
        raw_location=None,
        etag=None,
        last_modified=None,
        collector_version=None,
    )
    second = await repo.add_snapshot_if_new(
        snapshot_id=SNAPSHOT_2,  # a different candidate id -- must be ignored
        source_item_id=ITEM_1,
        fetched_at=fetched_at,
        content_hash="sha256:same",
        content_text="content",
        raw_location=None,
        etag=None,
        last_modified=None,
        collector_version=None,
    )

    assert first == second
    assert second.id == SNAPSHOT_1


@pytest.mark.asyncio
async def test_advance_applies_for_the_first_snapshot_of_an_item() -> None:
    repo = InMemorySourceItemRepository()
    await repo.find_or_create_source_item(
        item_id=ITEM_1,
        dedupe_key="dk-1",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(),
    )

    applied = await repo.advance_latest_snapshot_and_metadata(
        source_item_id=ITEM_1,
        snapshot_id=SNAPSHOT_1,
        fetched_at=datetime(2026, 8, 20, 9, 5, tzinfo=UTC),
        metadata=_metadata(title="Updated title"),
    )

    assert applied is True
    item = (await repo.list_source_items(after=None, limit=10))[0]
    assert item.latest_snapshot_id == SNAPSHOT_1
    assert item.title == "Updated title"


@pytest.mark.asyncio
async def test_advance_rejects_an_older_candidate_and_regresses_neither_field() -> None:
    """The Finding-1 regression this ADR correction fixes: an older
    candidate must advance neither the pointer nor the metadata."""
    repo = InMemorySourceItemRepository()
    await repo.find_or_create_source_item(
        item_id=ITEM_1,
        dedupe_key="dk-1",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(),
    )
    newer_fetched_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    older_fetched_at = datetime(2026, 8, 20, 9, 5, tzinfo=UTC)
    for snapshot_id, fetched_at in ((SNAPSHOT_1, older_fetched_at), (SNAPSHOT_2, newer_fetched_at)):
        await repo.add_snapshot_if_new(
            snapshot_id=snapshot_id,
            source_item_id=ITEM_1,
            fetched_at=fetched_at,
            content_hash=f"sha256:{snapshot_id}",
            content_text="content",
            raw_location=None,
            etag=None,
            last_modified=None,
            collector_version=None,
        )
    await repo.advance_latest_snapshot_and_metadata(
        source_item_id=ITEM_1,
        snapshot_id=SNAPSHOT_2,
        fetched_at=newer_fetched_at,
        metadata=_metadata(title="Newer title"),
    )

    applied = await repo.advance_latest_snapshot_and_metadata(
        source_item_id=ITEM_1,
        snapshot_id=SNAPSHOT_1,
        fetched_at=older_fetched_at,  # older than SNAPSHOT_2's
        metadata=_metadata(title="Stale title that must not win"),
    )

    assert applied is False
    item = (await repo.list_source_items(after=None, limit=10))[0]
    assert item.latest_snapshot_id == SNAPSHOT_2
    assert item.title == "Newer title"


@pytest.mark.asyncio
async def test_advance_uses_id_as_tiebreaker_for_equal_fetched_at() -> None:
    repo = InMemorySourceItemRepository()
    await repo.find_or_create_source_item(
        item_id=ITEM_1,
        dedupe_key="dk-1",
        source_id="openai_news",
        canonical_url="https://openai.com/a",
        first_fetched_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        metadata=_metadata(),
    )
    same_instant = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    lower_id, higher_id = sorted([SNAPSHOT_1, SNAPSHOT_3])
    for snapshot_id in (lower_id, higher_id):
        await repo.add_snapshot_if_new(
            snapshot_id=snapshot_id,
            source_item_id=ITEM_1,
            fetched_at=same_instant,
            content_hash=f"sha256:{snapshot_id}",
            content_text="content",
            raw_location=None,
            etag=None,
            last_modified=None,
            collector_version=None,
        )

    await repo.advance_latest_snapshot_and_metadata(
        source_item_id=ITEM_1,
        snapshot_id=higher_id,
        fetched_at=same_instant,
        metadata=_metadata(title="From the higher id"),
    )
    applied = await repo.advance_latest_snapshot_and_metadata(
        source_item_id=ITEM_1,
        snapshot_id=lower_id,
        fetched_at=same_instant,
        metadata=_metadata(title="From the lower id -- must lose"),
    )

    assert applied is False
    item = (await repo.list_source_items(after=None, limit=10))[0]
    assert item.latest_snapshot_id == higher_id


@pytest.mark.asyncio
async def test_list_source_items_orders_desc_and_fetches_limit_plus_one() -> None:
    repo = InMemorySourceItemRepository()
    for index, item_id in enumerate([ITEM_1, ITEM_2]):
        await repo.find_or_create_source_item(
            item_id=item_id,
            dedupe_key=f"dk-{index}",
            source_id="openai_news",
            canonical_url=f"https://openai.com/{index}",
            first_fetched_at=datetime(2026, 8, 20, 9, index, tzinfo=UTC),
            metadata=_metadata(),
        )

    page = await repo.list_source_items(after=None, limit=1)

    # limit=1 with 2 rows present -> exactly limit + 1 = 2 rows back,
    # newest (ITEM_2, first_fetched_at 09:01) first (ADR 0002 section
    # 12.2: "the method fetches limit + 1 rows").
    assert [item.id for item in page] == [ITEM_2, ITEM_1]


@pytest.mark.asyncio
async def test_list_source_items_rejects_non_positive_limit() -> None:
    repo = InMemorySourceItemRepository()
    with pytest.raises(ValueError, match="limit must be positive"):
        await repo.list_source_items(after=None, limit=0)
