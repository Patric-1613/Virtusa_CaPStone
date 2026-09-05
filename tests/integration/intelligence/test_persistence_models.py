"""Integration tests for intelligence persistence models and PostgresFactStore — ADR 0011."""
# pylint: disable=too-many-locals,protected-access

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow
from ai_daily_digest.intelligence.db.models import (
    ChangeSetModel,
    CurrentFactModel,
    ExtractedFactModel,
)
from ai_daily_digest.intelligence.db.repository import PostgresFactStore
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import (
    DisclosureStatus,
    ExtractedFact,
    ExtractionMethod,
    Subject,
)

pytestmark = pytest.mark.integration

BASE_TIME = datetime(2026, 9, 4, 12, 0, 0, 0, tzinfo=UTC)


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
        canonical_url=f"https://openai.com/news/{s_id}",
        first_fetched_at=dt,
    )
    session.add(item)
    await session.flush()
    snapshot = DocumentSnapshotRow(
        id=snap_id,
        source_item_id=s_id,
        content_hash=f"hash-{snap_id}",
        fetched_at=dt,
        content_text="Sample snapshot text",
    )
    session.add(snapshot)
    await session.flush()
    return item, snapshot


@pytest.mark.asyncio
async def test_subject_canonical_key_resolution(database_session: AsyncSession) -> None:
    store = PostgresFactStore(database_session)
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


@pytest.mark.asyncio
async def test_record_facts_replay_and_verification(database_session: AsyncSession) -> None:
    store = PostgresFactStore(database_session)
    subject = Subject(company="Anthropic", product="Claude")
    _, snap = await _create_source_and_snapshot(database_session)

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


@pytest.mark.asyncio
async def test_current_facts_advance_4_tuple_ordering(database_session: AsyncSession) -> None:
    store = PostgresFactStore(database_session)
    subject = Subject(company="Anthropic", product="Claude")

    t1 = BASE_TIME
    t2 = BASE_TIME + timedelta(hours=1)
    _, snap1 = await _create_source_and_snapshot(database_session, fetched_at=t1)
    _, snap2 = await _create_source_and_snapshot(database_session, fetched_at=t2)

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


@pytest.mark.asyncio
async def test_current_facts_four_part_ordering_regression(  # pylint: disable=too-many-statements
    database_session: AsyncSession,
) -> None:
    """Regression test for ADR 0011 4-part ordering tuple (Issue #56).

    Tuple: (observed_at DESC, snapshot_id DESC, extraction_version DESC, id DESC).
    Tests:
    1. Equal observed_at, distinct snapshot_id: lower snapshot_id with higher
       extraction_version loses to higher snapshot_id (tested in both arrival orders).
    2. Equal observed_at, snapshot_id, extraction_version: final tie-breaker by
       fact_id (tested in both arrival orders).
    3. Existing outcomes: first-write, newer-wins, older-ignored.
    """
    store = PostgresFactStore(database_session)
    t_eq = BASE_TIME + timedelta(days=2)

    # Setup 2 snapshots at the exact same observed_at with deterministically ordered IDs
    id_a, id_b = new_id(), new_id()
    snap_id_low, snap_id_high = (id_a, id_b) if id_a < id_b else (id_b, id_a)

    _, snap_low = await _create_source_and_snapshot(
        database_session, snapshot_id=snap_id_low, fetched_at=t_eq
    )
    _, snap_high = await _create_source_and_snapshot(
        database_session, snapshot_id=snap_id_high, fetched_at=t_eq
    )

    # --- 1. Equal observed_at, distinct snapshot_id ---
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
    assert res_low_1["benchmark_scores"] is False

    cf1 = await database_session.get(CurrentFactModel, ("coone", "modelalpha", "benchmark_scores"))
    assert cf1 is not None
    assert cf1.fact_id == f_high_1.id
    assert cf1.snapshot_id == snap_high.id
    assert cf1.extraction_version == 1

    # Arrival order 2
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
    assert res_low_2["benchmark_scores"] is True

    rec_high_2 = await store.record_extracted_facts(
        subj2, [f_high_2], snapshot_observed_at=t_eq, extraction_version=1
    )
    res_high_2 = await store.advance_current_facts(subj2, rec_high_2)
    assert res_high_2["benchmark_scores"] is True

    cf2 = await database_session.get(CurrentFactModel, ("cotwo", "modelbeta", "benchmark_scores"))
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

    res_tie_1 = await store._upsert_current_fact(
        company_key="cothree",
        product_key="modelgamma",
        fact=f_model_low,
        now_dt=datetime.now(UTC),
        is_sqlite=False,
    )
    assert res_tie_1 is True

    res_tie_2 = await store._upsert_current_fact(
        company_key="cothree",
        product_key="modelgamma",
        fact=f_model_high,
        now_dt=datetime.now(UTC),
        is_sqlite=False,
    )
    assert res_tie_2 is True

    cf3 = await database_session.get(
        CurrentFactModel, ("cothree", "modelgamma", "context_window_tokens")
    )
    assert cf3 is not None
    assert cf3.fact_id == fid_high

    # --- 3. Existing outcomes: first-write, newer-wins, older-ignored ---
    subj5 = Subject(company="CoFive", product="ModelEpsilon")
    t_early = BASE_TIME
    t_late = BASE_TIME + timedelta(days=5)
    _, snap_early = await _create_source_and_snapshot(database_session, fetched_at=t_early)
    _, snap_late = await _create_source_and_snapshot(database_session, fetched_at=t_late)

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

    rec_first = await store.record_extracted_facts(
        subj5, [f_early], snapshot_observed_at=t_early, extraction_version=1
    )
    res_first = await store.advance_current_facts(subj5, rec_first)
    assert res_first["input_price_usd"] is True

    rec_newer = await store.record_extracted_facts(
        subj5, [f_late], snapshot_observed_at=t_late, extraction_version=1
    )
    res_newer = await store.advance_current_facts(subj5, rec_newer)
    assert res_newer["input_price_usd"] is True

    res_older = await store.advance_current_facts(subj5, rec_first)
    assert res_older["input_price_usd"] is False

    cf5 = await database_session.get(
        CurrentFactModel, ("cofive", "modelepsilon", "input_price_usd")
    )
    assert cf5 is not None
    assert cf5.fact_id == f_late.id
    assert cf5.observed_at == t_late


@pytest.mark.asyncio
async def test_detect_and_persist_changes_across_snapshots(
    database_session: AsyncSession,
) -> None:
    store = PostgresFactStore(database_session)
    subject = Subject(company="OpenAI", product="GPT-4")

    t1 = BASE_TIME
    t2 = BASE_TIME + timedelta(days=1)
    _, snap1 = await _create_source_and_snapshot(database_session, fetched_at=t1)
    _, snap2 = await _create_source_and_snapshot(database_session, fetched_at=t2)

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

    # First observation establishes baseline state without emitting Change
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


@pytest.mark.asyncio
async def test_correction_on_current_snapshot_emits_no_change(
    database_session: AsyncSession,
) -> None:
    store = PostgresFactStore(database_session)
    subject = Subject(company="OpenAI", product="GPT-4")

    t1 = BASE_TIME
    _, snap1 = await _create_source_and_snapshot(database_session, fetched_at=t1)

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

    changes1 = await store.detect_and_persist_changes(
        subject, [fact_v1], snapshot_observed_at=t1, detected_at=t1, extraction_version=1
    )
    assert len(changes1) == 0

    changes2 = await store.detect_and_persist_changes(
        subject, [fact_v2], snapshot_observed_at=t1, detected_at=t1, extraction_version=2
    )
    assert len(changes2) == 0

    current_map = await store.read_current_facts(subject, ["pricing"])
    cf, ef = current_map["pricing"]
    assert cf.extraction_version == 2
    assert ef.value == "$20"


@pytest.mark.asyncio
async def test_derive_changeset_citations_first_occurrence_order(
    database_session: AsyncSession,
) -> None:
    store = PostgresFactStore(database_session)
    subject = Subject(company="Google", product="Gemini")

    t1 = BASE_TIME
    t2 = BASE_TIME + timedelta(hours=1)
    t3 = BASE_TIME + timedelta(hours=2)
    _, snap1 = await _create_source_and_snapshot(database_session, fetched_at=t1)
    _, snap2 = await _create_source_and_snapshot(database_session, fetched_at=t2)
    _, snap3 = await _create_source_and_snapshot(database_session, fetched_at=t3)

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

    current_ids, previous_ids = await store.derive_changeset_citations(cs_id)
    assert current_ids == [snap3.id]
    assert previous_ids == [snap2.id, snap1.id]


@pytest.mark.asyncio
async def test_multi_field_batch_shares_single_changeset_id(
    database_session: AsyncSession,
) -> None:
    store = PostgresFactStore(database_session)
    subject = Subject(company="Anthropic", product="Claude")

    t1 = BASE_TIME
    t2 = BASE_TIME + timedelta(hours=1)
    _, snap1 = await _create_source_and_snapshot(database_session, fetched_at=t1)
    _, snap2 = await _create_source_and_snapshot(database_session, fetched_at=t2)

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
    assert changes[0].change_set_id == changes[1].change_set_id
    batch_id = changes[0].change_set_id

    cs = await database_session.get(ChangeSetModel, batch_id)
    assert cs is not None
    assert cs.id == batch_id
