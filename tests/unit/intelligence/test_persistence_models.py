"""Unit tests for intelligence persistence models and repository logic — ADR 0011."""
# pylint: disable=too-many-locals

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow
from ai_daily_digest.intelligence.db.models import ChangeSetModel
from ai_daily_digest.intelligence.db.repository import PostgresFactStore
from ai_daily_digest.shared.db.engine import create_engine, create_session_factory
from ai_daily_digest.shared.db.metadata import Base
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import (
    DisclosureStatus,
    ExtractedFact,
    ExtractionMethod,
    Subject,
)

pytestmark = pytest.mark.unit

BASE_TIME = datetime(2026, 9, 4, 12, 0, 0, 0, tzinfo=UTC)


def run_async(coro_fn: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """Decorator to run async unit test deterministically."""

    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(coro_fn(*args, **kwargs))

    return wrapper


async def _init_session() -> tuple[Any, AsyncSession]:
    engine = create_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = create_session_factory(engine)
    session = session_factory()
    return engine, session


async def _create_source_and_snapshot(
    session: AsyncSession,
    *,
    item_id: uuid.UUID | None = None,
    snapshot_id: uuid.UUID | None = None,
    fetched_at: datetime | None = None,
) -> tuple[SourceItemRow, DocumentSnapshotRow]:
    s_id = item_id or new_id()
    snap_id = snapshot_id or new_id()
    dt = fetched_at or BASE_TIME

    item = SourceItemRow(
        id=s_id,
        dedupe_key=f"sha256:{s_id}",
        source_id="openai_news",
        publisher="OpenAI",
        title="OpenAI Announcement",
        canonical_url="https://openai.com/news/1",
        first_fetched_at=dt,
    )
    snapshot = DocumentSnapshotRow(
        id=snap_id,
        source_item_id=s_id,
        content_hash=f"hash-{snap_id}",
        fetched_at=dt,
        content_text="Sample snapshot text",
    )
    session.add(item)
    await session.flush()
    session.add(snapshot)
    await session.flush()
    return item, snapshot


@run_async
async def test_subject_canonical_key_resolution() -> None:
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        s1 = Subject(company="OpenAI", product="GPT-4")
        s2 = Subject(company="openai", product="gpt-4.")

        sub1 = await store.ensure_subject(s1)
        sub2 = await store.ensure_subject(s2)

        assert sub1.company_key == "openai"
        assert sub1.product_key == "gpt 4"
        assert sub2.company_key == sub1.company_key
        assert sub2.product_key == sub1.product_key
        # First-seen display casing preserved
        assert sub2.company == "OpenAI"
    finally:
        await session.close()
        await engine.dispose()


@run_async
async def test_record_facts_replay_and_verification() -> None:
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        subject = Subject(company="Anthropic", product="Claude")
        _, snap = await _create_source_and_snapshot(session)

        fact = ExtractedFact(
            id=new_id(),
            snapshot_id=snap.id,
            field="context_window",
            value="200k",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        # Initial insert
        records1 = await store.record_extracted_facts(
            subject, [fact], snapshot_observed_at=snap.fetched_at, extraction_version=1
        )
        assert len(records1) == 1

        # Replay identical fact: succeeds cleanly
        records2 = await store.record_extracted_facts(
            subject, [fact], snapshot_observed_at=snap.fetched_at, extraction_version=1
        )
        assert len(records2) == 1
        assert records2[0].id == records1[0].id

        # Divergent replay: fails closed
        divergent_fact = ExtractedFact(
            id=new_id(),
            snapshot_id=snap.id,
            field="context_window",
            value="100k",  # different value on same attempt!
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        with pytest.raises(ValueError, match="Replay verification failed"):
            await store.record_extracted_facts(
                subject,
                [divergent_fact],
                snapshot_observed_at=snap.fetched_at,
                extraction_version=1,
            )
    finally:
        await session.close()
        await engine.dispose()


@run_async
async def test_current_facts_advance_4_tuple_ordering() -> None:
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        subject = Subject(company="Anthropic", product="Claude")

        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(hours=1)
        _, snap1 = await _create_source_and_snapshot(session, fetched_at=t1)
        _, snap2 = await _create_source_and_snapshot(session, fetched_at=t2)

        f1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="pricing",
            value="$20",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="pricing",
            value="$15",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        # Step 1: Initial observation at t1 advances
        rec1 = await store.record_extracted_facts(subject, [f1], snapshot_observed_at=t1)
        res1 = await store.advance_current_facts(subject, rec1)
        assert res1["pricing"] is True

        # Step 2: Newer observation at t2 advances
        rec2 = await store.record_extracted_facts(subject, [f2], snapshot_observed_at=t2)
        res2 = await store.advance_current_facts(subject, rec2)
        assert res2["pricing"] is True

        # Step 3: Older observation at t1 arrives after t2 -> does NOT advance
        res3 = await store.advance_current_facts(subject, rec1)
        assert res3["pricing"] is False

        # Step 4: Test equal-time snapshots with different extraction versions
        # (Issue #56 / ADR 0011)
        # Higher extraction version for same snapshot supersedes lower version:
        f2_v2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="pricing",
            value="$16",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        rec2_v2 = await store.record_extracted_facts(
            subject, [f2_v2], snapshot_observed_at=t2, extraction_version=2
        )
        res4 = await store.advance_current_facts(subject, rec2_v2)
        assert res4["pricing"] is True

        # And lower version for the same snapshot cannot supersede higher version:
        res5 = await store.advance_current_facts(subject, rec2)
        assert res5["pricing"] is False
    finally:
        await session.close()
        await engine.dispose()


@run_async
async def test_detect_and_persist_changes_across_snapshots() -> None:
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        subject = Subject(company="OpenAI", product="GPT-4")

        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)
        _, snap1 = await _create_source_and_snapshot(session, fetched_at=t1)
        _, snap2 = await _create_source_and_snapshot(session, fetched_at=t2)

        fact1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="context_window",
            value="8k",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        fact2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="context_window",
            value="128k",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        # First observation: establishes baseline state without emitting a business Change
        changes1 = await store.detect_and_persist_changes(
            subject, [fact1], snapshot_observed_at=t1, detected_at=t1, extraction_version=1
        )
        assert len(changes1) == 0

        # Second observation across distinct snapshot emits a "changed" Change
        changes2 = await store.detect_and_persist_changes(
            subject, [fact2], snapshot_observed_at=t2, detected_at=t2, extraction_version=1
        )
        assert len(changes2) == 1
        assert changes2[0].change_type == "changed"
        assert changes2[0].previous is not None
        assert changes2[0].previous.value == "8k"
        assert changes2[0].current.value == "128k"
    finally:
        await session.close()
        await engine.dispose()


@run_async
async def test_correction_on_current_snapshot_emits_no_change() -> None:
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        subject = Subject(company="OpenAI", product="GPT-4")

        t1 = BASE_TIME
        _, snap1 = await _create_source_and_snapshot(session, fetched_at=t1)

        fact_v1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="pricing",
            value="$30",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        fact_v2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,  # SAME snapshot
            field="pricing",
            value="$20",  # Parser bug corrected
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        # Version 1 initial observation establishes baseline state without emitting Change
        changes1 = await store.detect_and_persist_changes(
            subject, [fact_v1], snapshot_observed_at=t1, detected_at=t1, extraction_version=1
        )
        assert len(changes1) == 0

        # Version 2 correction on SAME snapshot advances pointer but emits NO Change!
        changes2 = await store.detect_and_persist_changes(
            subject, [fact_v2], snapshot_observed_at=t1, detected_at=t1, extraction_version=2
        )
        assert len(changes2) == 0

        # Verify pointer was updated to v2
        current_map = await store.read_current_facts(subject, ["pricing"])
        cf, ef = current_map["pricing"]
        assert cf.extraction_version == 2
        assert ef.value == "$20"
    finally:
        await session.close()
        await engine.dispose()


@run_async
async def test_derive_changeset_citations_first_occurrence_order() -> None:
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        subject = Subject(company="Google", product="Gemini")

        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(hours=1)
        t3 = BASE_TIME + timedelta(hours=2)
        _, snap1 = await _create_source_and_snapshot(session, fetched_at=t1)
        _, snap2 = await _create_source_and_snapshot(session, fetched_at=t2)
        _, snap3 = await _create_source_and_snapshot(session, fetched_at=t3)

        # Prime previous state with snap1 and snap2
        f_prev1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="param_a",
            value="old_a",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_prev2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="param_b",
            value="old_b",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        await store.detect_and_persist_changes(
            subject, [f_prev1], snapshot_observed_at=t1, detected_at=t1
        )
        await store.detect_and_persist_changes(
            subject, [f_prev2], snapshot_observed_at=t2, detected_at=t2
        )

        # Now observe snap3 modifying both param_b then param_a
        f_curr1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap3.id,
            field="param_b",
            value="new_b",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_curr2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap3.id,
            field="param_a",
            value="new_a",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        cs_id = new_id()
        changes = await store.detect_and_persist_changes(
            subject,
            [f_curr1, f_curr2],
            snapshot_observed_at=t3,
            detected_at=t3,
            change_set_id=cs_id,
        )
        assert len(changes) == 2

        # Derive citations
        current_ids, previous_ids = await store.derive_changeset_citations(cs_id)
        assert current_ids == [snap3.id]
        # Previous citations should follow first occurrence:
        # param_b was first (snap2), param_a was second (snap1)
        assert previous_ids == [snap2.id, snap1.id]
    finally:
        await session.close()
        await engine.dispose()


@run_async
async def test_multi_field_batch_shares_single_changeset_id() -> None:
    """A multi-field batch without explicit change_set_id shares one ID across all Changes."""
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        subject = Subject(company="Anthropic", product="Claude")

        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(hours=1)
        _, snap1 = await _create_source_and_snapshot(session, fetched_at=t1)
        _, snap2 = await _create_source_and_snapshot(session, fetched_at=t2)

        # Prime base facts
        f_p1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="f1",
            value="v1",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_p2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="f2",
            value="v2",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        await store.detect_and_persist_changes(
            subject, [f_p1, f_p2], snapshot_observed_at=t1, detected_at=t1
        )

        # Multi-field modification on snap2 without passing change_set_id
        f_c1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="f1",
            value="v1_modified",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_c2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="f2",
            value="v2_modified",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        changes = await store.detect_and_persist_changes(
            subject, [f_c1, f_c2], snapshot_observed_at=t2, detected_at=t2
        )
        assert len(changes) == 2
        # All returned Change objects must share the exact same change_set_id
        assert changes[0].change_set_id == changes[1].change_set_id
        batch_id = changes[0].change_set_id

        # Verify ChangeSetModel exists in database with this batch_id
        cs = await session.get(ChangeSetModel, batch_id)
        assert cs is not None
        assert cs.id == batch_id
    finally:
        await session.close()
        await engine.dispose()
