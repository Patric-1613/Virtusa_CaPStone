"""Unit tests for intelligence persistence models and repository logic — ADR 0011."""
# pylint: disable=too-many-locals,protected-access

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import ARRAY

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow
from ai_daily_digest.intelligence.db.models import (
    ChangeSetModel,
    CurrentFactModel,
    ExtractedFactModel,
)
from ai_daily_digest.intelligence.db.repository import PostgresFactStore
from ai_daily_digest.shared.db.metadata import Base
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import (
    DisclosureStatus,
    ExtractedFact,
    ExtractionMethod,
    Subject,
)


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:  # pylint: disable=unused-argument
    return "TEXT"


BASE_TIME = datetime(2026, 9, 4, 12, 0, 0, 0, tzinfo=UTC)


def run_async(coro_fn: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """Decorator to run async unit test deterministically."""

    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(coro_fn(*args, **kwargs))

    return wrapper


async def _init_session() -> tuple[Any, AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
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
async def test_current_facts_four_part_ordering_regression() -> None:  # pylint: disable=too-many-statements,protected-access
    """Regression test for ADR 0011 4-part ordering tuple (Issue #56).

    Tuple: (observed_at DESC, snapshot_id DESC, extraction_version DESC, id DESC).
    Tests:
    1. Equal observed_at, distinct snapshot_id: lower snapshot_id with higher
       extraction_version loses to higher snapshot_id (tested in both arrival orders).
    2. Equal observed_at, snapshot_id, extraction_version: final tie-breaker by
       fact_id (tested in both arrival orders).
    3. Existing outcomes: first-write, newer-wins, older-ignored.
    """
    engine, session = await _init_session()
    try:
        store = PostgresFactStore(session)
        t_eq = BASE_TIME + timedelta(days=2)

        # Setup 2 snapshots at the exact same observed_at with deterministically ordered IDs
        id_a, id_b = new_id(), new_id()
        snap_id_low, snap_id_high = (id_a, id_b) if id_a < id_b else (id_b, id_a)

        _, snap_low = await _create_source_and_snapshot(
            session, snapshot_id=snap_id_low, fetched_at=t_eq
        )
        _, snap_high = await _create_source_and_snapshot(
            session, snapshot_id=snap_id_high, fetched_at=t_eq
        )

        # --- 1. Equal observed_at, distinct snapshot_id ---
        # snap_low has higher extraction_version (10), snap_high has lower extraction_version (1).
        # snap_high MUST win because snapshot_id is compared before extraction_version.

        # Arrival order 1: Higher snapshot_id arrives first, then lower snapshot_id with
        # higher extraction_version arrives -> lower snapshot_id does NOT win.
        subj1 = Subject(company="CoOne", product="ModelAlpha")
        f_high_1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_high.id,
            field="benchmark_scores",
            value="85.0",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_low_1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_low.id,
            field="benchmark_scores",
            value="90.0",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        rec_high_1 = await store.record_extracted_facts(
            subj1, [f_high_1], snapshot_observed_at=t_eq, extraction_version=1
        )
        res_high_1 = await store.advance_current_facts(subj1, rec_high_1)
        assert res_high_1["benchmark_scores"] is True

        rec_low_1 = await store.record_extracted_facts(
            subj1, [f_low_1], snapshot_observed_at=t_eq, extraction_version=10
        )
        res_low_1 = await store.advance_current_facts(subj1, rec_low_1)
        assert (
            res_low_1["benchmark_scores"] is False
        )  # Higher extraction_version loses by snapshot_id

        cf1 = await session.get(CurrentFactModel, ("coone", "modelalpha", "benchmark_scores"))
        assert cf1 is not None
        assert cf1.fact_id == f_high_1.id
        assert cf1.snapshot_id == snap_high.id
        assert cf1.extraction_version == 1

        # Arrival order 2: Lower snapshot_id with higher extraction_version arrives first,
        # then higher snapshot_id arrives -> higher snapshot_id DOES win and overwrites.
        subj2 = Subject(company="CoTwo", product="ModelBeta")
        f_low_2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_low.id,
            field="benchmark_scores",
            value="90.0",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_high_2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_high.id,
            field="benchmark_scores",
            value="85.0",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        rec_low_2 = await store.record_extracted_facts(
            subj2, [f_low_2], snapshot_observed_at=t_eq, extraction_version=10
        )
        res_low_2 = await store.advance_current_facts(subj2, rec_low_2)
        assert res_low_2["benchmark_scores"] is True  # First write succeeds

        rec_high_2 = await store.record_extracted_facts(
            subj2, [f_high_2], snapshot_observed_at=t_eq, extraction_version=1
        )
        res_high_2 = await store.advance_current_facts(subj2, rec_high_2)
        assert res_high_2["benchmark_scores"] is True  # Higher snapshot_id wins over lower

        cf2 = await session.get(CurrentFactModel, ("cotwo", "modelbeta", "benchmark_scores"))
        assert cf2 is not None
        assert cf2.fact_id == f_high_2.id
        assert cf2.snapshot_id == snap_high.id
        assert cf2.extraction_version == 1

        # --- 2. Equal observed_at, snapshot_id, extraction_version: Tie-breaker by fact_id ---
        fid_a, fid_b = new_id(), new_id()
        fid_low, fid_high = (fid_a, fid_b) if fid_a < fid_b else (fid_b, fid_a)

        f_model_low = ExtractedFactModel(
            id=fid_low,
            snapshot_id=snap_high.id,
            company_key="cothree",
            product_key="modelgamma",
            field="context_window_tokens",
            value="8000",
            disclosure_status="disclosed",
            extraction_method="deterministic",
            extraction_version=1,
            observed_at=t_eq,
            created_at=datetime.now(UTC),
        )
        f_model_high = ExtractedFactModel(
            id=fid_high,
            snapshot_id=snap_high.id,
            company_key="cothree",
            product_key="modelgamma",
            field="context_window_tokens",
            value="8000",
            disclosure_status="disclosed",
            extraction_method="deterministic",
            extraction_version=1,
            observed_at=t_eq,
            created_at=datetime.now(UTC),
        )

        # Tie-breaker Arrival order 1: Lower fact_id arrives first, higher arrives
        # second -> higher wins
        res_tie_1 = await store._upsert_current_fact(
            company_key="cothree",
            product_key="modelgamma",
            fact=f_model_low,
            now_dt=datetime.now(UTC),
            is_sqlite=True,
        )
        assert res_tie_1 is True

        res_tie_2 = await store._upsert_current_fact(
            company_key="cothree",
            product_key="modelgamma",
            fact=f_model_high,
            now_dt=datetime.now(UTC),
            is_sqlite=True,
        )
        assert res_tie_2 is True  # Higher fact_id wins tie-breaker

        cf3 = await session.get(
            CurrentFactModel, ("cothree", "modelgamma", "context_window_tokens")
        )
        assert cf3 is not None
        assert cf3.fact_id == fid_high

        # Tie-breaker Arrival order 2: Higher fact_id arrives first, lower arrives
        # second -> lower loses
        res_tie_3 = await store._upsert_current_fact(
            company_key="cofour",
            product_key="modeldelta",
            fact=ExtractedFactModel(
                id=fid_high,
                snapshot_id=snap_high.id,
                company_key="cofour",
                product_key="modeldelta",
                field="context_window_tokens",
                value="8000",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=t_eq,
                created_at=datetime.now(UTC),
            ),
            now_dt=datetime.now(UTC),
            is_sqlite=True,
        )
        assert res_tie_3 is True

        res_tie_4 = await store._upsert_current_fact(
            company_key="cofour",
            product_key="modeldelta",
            fact=ExtractedFactModel(
                id=fid_low,
                snapshot_id=snap_high.id,
                company_key="cofour",
                product_key="modeldelta",
                field="context_window_tokens",
                value="8000",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=t_eq,
                created_at=datetime.now(UTC),
            ),
            now_dt=datetime.now(UTC),
            is_sqlite=True,
        )
        assert res_tie_4 is False  # Lower fact_id loses tie-breaker

        cf4 = await session.get(CurrentFactModel, ("cofour", "modeldelta", "context_window_tokens"))
        assert cf4 is not None
        assert cf4.fact_id == fid_high

        # --- 3. Existing outcomes: first-write, newer-wins, older-ignored ---
        subj5 = Subject(company="CoFive", product="ModelEpsilon")
        t_early = BASE_TIME
        t_late = BASE_TIME + timedelta(days=5)
        _, snap_early = await _create_source_and_snapshot(session, fetched_at=t_early)
        _, snap_late = await _create_source_and_snapshot(session, fetched_at=t_late)

        f_early = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_early.id,
            field="input_price_usd",
            value="1.00",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_late = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_late.id,
            field="input_price_usd",
            value="2.00",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        # First write succeeds
        rec_first = await store.record_extracted_facts(
            subj5, [f_early], snapshot_observed_at=t_early, extraction_version=1
        )
        res_first = await store.advance_current_facts(subj5, rec_first)
        assert res_first["input_price_usd"] is True

        # Newer writer wins
        rec_newer = await store.record_extracted_facts(
            subj5, [f_late], snapshot_observed_at=t_late, extraction_version=1
        )
        res_newer = await store.advance_current_facts(subj5, rec_newer)
        assert res_newer["input_price_usd"] is True

        # Older writer ignored
        res_older = await store.advance_current_facts(subj5, rec_first)
        assert res_older["input_price_usd"] is False

        cf5 = await session.get(CurrentFactModel, ("cofive", "modelepsilon", "input_price_usd"))
        assert cf5 is not None
        assert cf5.fact_id == f_late.id
        assert cf5.observed_at == t_late
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
