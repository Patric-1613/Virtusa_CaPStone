"""Real PostgreSQL integration tests for storage-level enforcement — ADR 0011 §7."""
# pylint: disable=too-many-lines,protected-access

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow
from ai_daily_digest.intelligence.db.models import (
    ChangeModel,
    ChangeSetModel,
    CurrentFactModel,
    DigestClaimCitationModel,
    DigestClaimModel,
    DigestModel,
    ExtractedFactModel,
    SubjectModel,
)
from ai_daily_digest.intelligence.db.repository import PostgresFactStore
from ai_daily_digest.shared.db.engine import create_engine
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import (
    DisclosureStatus,
    ExtractedFact,
    ExtractionMethod,
    Subject,
)

pytestmark = pytest.mark.integration

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("set DATABASE_URL to run PostgreSQL integration tests", allow_module_level=True)

# Normalize postgresql driver prefix if plain postgres:// was supplied
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://") :]
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://") :]

BASE_TIME = datetime(2026, 9, 4, 12, 0, 0, 0, tzinfo=UTC)


def run_async(coro_fn: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """Run async test in event loop."""

    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(coro_fn(*args, **kwargs))

    return wrapper


def _get_engine() -> AsyncEngine:
    return create_engine(str(DATABASE_URL), echo=False)


async def _make_source_and_snapshot(
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
        title="OpenAI Test Item",
        canonical_url=f"https://openai.com/news/{s_id}",
        first_fetched_at=dt,
    )
    snapshot = DocumentSnapshotRow(
        id=snap_id,
        source_item_id=s_id,
        content_hash=f"hash-{snap_id}",
        fetched_at=dt,
        content_text="Sample text for snapshot",
    )
    session.add(item)
    await session.flush()
    session.add(snapshot)
    await session.flush()
    return item, snapshot


async def _ensure_subject(
    session: AsyncSession,
    *,
    company_key: str = "openai",
    product_key: str = "gpt-4",
    company: str = "OpenAI",
    product: str = "GPT-4",
) -> SubjectModel:
    sub = await session.get(SubjectModel, (company_key, product_key))
    if sub is None:
        sub = SubjectModel(
            company_key=company_key,
            product_key=product_key,
            company=company,
            product=product,
            created_at=BASE_TIME,
        )
        session.add(sub)
        await session.flush()
    return sub


# -----------------------------------------------------------------------------
# 1. Reject Update / Safe Rollback / Value Survival / No Repo Mutation Path
# -----------------------------------------------------------------------------
@run_async
async def test_changes_immutability_and_value_survival() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            _, snap = await _make_source_and_snapshot(session)
            cs_id = new_id()
            c_id = new_id()

            await _ensure_subject(session, company_key="openai", product_key="gpt-4")

            cs = ChangeSetModel(
                id=cs_id,
                company_key="openai",
                product_key="gpt-4",
                created_at=BASE_TIME,
            )
            change = ChangeModel(
                id=c_id,
                change_set_id=cs_id,
                position=0,
                company_key="openai",
                product_key="gpt-4",
                field="pricing",
                change_type="disclosed",
                confidence=1.0,
                current_value="$20",
                current_observed_at=snap.fetched_at,
                current_snapshot_id=snap.id,
                detected_at=BASE_TIME,
                created_at=BASE_TIME,
            )
            session.add(cs)
            await session.flush()
            session.add(change)
            await session.commit()

        # Step 1: Reject update on protected column (position)
        async with sessionmaker() as session:
            with pytest.raises(DBAPIError, match="Cannot update immutable columns on changes"):
                await session.execute(
                    text("UPDATE changes SET position = 999 WHERE id = :id"),
                    {"id": c_id},
                )
            # Step 2: Safe rollback
            await session.rollback()

            # Step 3: Value survival
            result = await session.scalar(
                select(ChangeModel.position).where(ChangeModel.id == c_id)
            )
            assert result == 0

            # Step 4: Verify no repository mutation path exists on PostgresFactStore
            store = PostgresFactStore(session)
            assert not hasattr(store, "update_change")
            assert not hasattr(store, "update_position")
    finally:
        await engine.dispose()


@run_async
async def test_digests_immutability_and_value_survival() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        d_id = new_id()
        d_date = date(2026, 9, 4)

        async with sessionmaker() as session:
            digest = DigestModel(
                id=d_id,
                digest_date=d_date,
                title="AI Daily Digest - 2026-09-04",
                status="draft",
                created_at=BASE_TIME,
            )
            session.add(digest)
            await session.commit()

        # Reject update on digest_date
        async with sessionmaker() as session:
            with pytest.raises(
                DBAPIError, match=r"Cannot update immutable column digests\.digest_date"
            ):
                await session.execute(
                    text("UPDATE digests SET digest_date = '2026-09-05' WHERE id = :id"),
                    {"id": d_id},
                )
            await session.rollback()

            # Confirm value survival
            saved_date = await session.scalar(
                select(DigestModel.digest_date).where(DigestModel.id == d_id)
            )
            assert saved_date == d_date

            # Reject update on id
            with pytest.raises(DBAPIError, match=r"Cannot update immutable column digests\.id"):
                await session.execute(
                    text("UPDATE digests SET id = :new_id WHERE id = :id"),
                    {"id": d_id, "new_id": new_id()},
                )
            await session.rollback()
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 2. Statement-Level TRUNCATE Rejection on extracted_facts
# -----------------------------------------------------------------------------
@run_async
async def test_extracted_facts_truncate_rejection() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        f_id = new_id()
        async with sessionmaker() as session:
            _, snap = await _make_source_and_snapshot(session)
            await _ensure_subject(session, company_key="openai", product_key="gpt-4")

            fact = ExtractedFactModel(
                id=f_id,
                snapshot_id=snap.id,
                company_key="openai",
                product_key="gpt-4",
                field="model",
                value="gpt-4o",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=snap.fetched_at,
                created_at=BASE_TIME,
            )
            session.add(fact)
            await session.commit()

        # Attempt TRUNCATE
        async with sessionmaker() as session:
            with pytest.raises(
                DBAPIError, match="Table extracted_facts is append-only: truncate is prohibited"
            ):
                await session.execute(text("TRUNCATE TABLE extracted_facts CASCADE"))
            await session.rollback()

            # Record survived
            survived = await session.scalar(
                select(ExtractedFactModel.id).where(ExtractedFactModel.id == f_id)
            )
            assert survived == f_id
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 3. Canonical Subject Collision & Idempotency
# -----------------------------------------------------------------------------
@run_async
async def test_canonical_subject_collision_and_idempotency() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            store = PostgresFactStore(session)
            s1 = Subject(company="OpenAI", product="ChatGPT")
            s2 = Subject(company="openai", product="chatgpt.")
            s3 = Subject(company="OpenAI.", product="ChatGPT")

            sub1 = await store.ensure_subject(s1)
            sub2 = await store.ensure_subject(s2)
            sub3 = await store.ensure_subject(s3)
            await session.commit()

            assert sub1.company_key == "openai"
            assert sub1.product_key == "chatgpt"
            assert sub2.company_key == "openai"
            assert sub3.company_key == "openai"

            # Check that exactly one subject was stored and display name was preserved
            rows = (
                await session.scalars(
                    select(SubjectModel).where(
                        SubjectModel.company_key == "openai",
                        SubjectModel.product_key == "chatgpt",
                    )
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].company == "OpenAI"
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 4. ChangeSet <-> Change Subject-Consistency Rejection (Composite FK)
# -----------------------------------------------------------------------------
@run_async
async def test_changeset_change_subject_consistency_rejection() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            _, snap = await _make_source_and_snapshot(session)
            await _ensure_subject(session, company_key="openai", product_key="gpt-4")
            await _ensure_subject(
                session,
                company_key="anthropic",
                product_key="claude",
                company="Anthropic",
                product="Claude",
            )

            cs_id = new_id()
            cs = ChangeSetModel(
                id=cs_id,
                company_key="openai",
                product_key="gpt-4",
                created_at=BASE_TIME,
            )
            session.add(cs)
            await session.flush()

            # Attempt to attach Change pointing to cs_id but with subject anthropic/claude
            mismatched_change = ChangeModel(
                id=new_id(),
                change_set_id=cs_id,
                position=0,
                company_key="anthropic",  # MISMATCH!
                product_key="claude",  # MISMATCH!
                field="pricing",
                change_type="disclosed",
                confidence=1.0,
                current_value="$20",
                current_observed_at=snap.fetched_at,
                current_snapshot_id=snap.id,
                detected_at=BASE_TIME,
                created_at=BASE_TIME,
            )
            session.add(mismatched_change)
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 5. Published-Digest Immutability and Publication Gate (including INSERT bypass)
# -----------------------------------------------------------------------------
@run_async
async def test_published_digest_immutability_and_gate() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        # Part A: Direct INSERT with status='published' is rejected by gate trigger
        async with sessionmaker() as session:
            with pytest.raises(DBAPIError, match="Cannot publish digest"):
                await session.execute(
                    text("""
                        INSERT INTO digests (id, digest_date, title, status, created_at)
                        VALUES (:id, :d_date, 'Direct Published', 'published', :now)
                    """),
                    {"id": new_id(), "d_date": date(2026, 9, 10), "now": BASE_TIME},
                )
            await session.rollback()

        # Part B: Proper publication flow (draft -> add supported claims & citations -> publish)
        d_id = new_id()
        c_id = new_id()
        d_date = date(2026, 9, 11)
        async with sessionmaker() as session:
            _, snap = await _make_source_and_snapshot(session)
            digest = DigestModel(
                id=d_id,
                digest_date=d_date,
                title="Digest for 2026-09-11",
                status="draft",
                created_at=BASE_TIME,
            )
            claim = DigestClaimModel(
                id=c_id,
                digest_id=d_id,
                position=0,
                text="Supported Claim",
                validation_status="supported",
                created_at=BASE_TIME,
            )
            citation = DigestClaimCitationModel(
                claim_id=c_id,
                snapshot_id=snap.id,
                position=0,
                created_at=BASE_TIME,
            )
            session.add(digest)
            await session.flush()
            session.add(claim)
            await session.flush()
            session.add(citation)
            await session.flush()

            # Publish transition succeeds
            digest.status = "published"
            await session.commit()

        # Part C: Attempt modifications to published digest
        async with sessionmaker() as session:
            # 1. Update title on published digest fails
            with pytest.raises(
                DBAPIError, match="Cannot update title of an already published digest"
            ):
                await session.execute(
                    text("UPDATE digests SET title = 'Changed Title' WHERE id = :id"),
                    {"id": d_id},
                )
            await session.rollback()

            # 2. Unpublishing fails
            with pytest.raises(DBAPIError, match="Cannot unpublish an already published digest"):
                await session.execute(
                    text("UPDATE digests SET status = 'draft' WHERE id = :id"),
                    {"id": d_id},
                )
            await session.rollback()

            # 3. Inserting new claim directly into published digest fails
            with pytest.raises(
                DBAPIError, match="Cannot insert claims into an already published digest"
            ):
                await session.execute(
                    text("""
                        INSERT INTO digest_claims (id, digest_id, position, text, validation_status, created_at)
                        VALUES (:id, :d_id, 1, 'Late Claim', 'supported', :now)
                    """),
                    {"id": new_id(), "d_id": d_id, "now": BASE_TIME},
                )
            await session.rollback()

            # 4. Deleting claim from published digest fails
            with pytest.raises(
                DBAPIError, match="Cannot delete claims from an already published digest"
            ):
                await session.execute(
                    text("DELETE FROM digest_claims WHERE id = :id"),
                    {"id": c_id},
                )
            await session.rollback()

            # 5. Deleting citation from published digest fails
            with pytest.raises(
                DBAPIError, match="Cannot delete citations from an already published digest"
            ):
                await session.execute(
                    text("DELETE FROM digest_claim_citations WHERE claim_id = :id"),
                    {"id": c_id},
                )
            await session.rollback()
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 6. Publish Idempotency via uq_digests_one_published_per_date
# -----------------------------------------------------------------------------
@run_async
async def test_publish_idempotency_one_published_per_date() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        d1_id = new_id()
        d2_id = new_id()
        d_date = date(2026, 9, 12)

        async with sessionmaker() as session:
            _, snap = await _make_source_and_snapshot(session)

            # Digest 1
            session.add(
                DigestModel(
                    id=d1_id,
                    digest_date=d_date,
                    title="Digest 1",
                    status="draft",
                    created_at=BASE_TIME,
                )
            )
            await session.flush()
            c1_id = new_id()
            session.add(
                DigestClaimModel(
                    id=c1_id,
                    digest_id=d1_id,
                    position=0,
                    text="Claim 1",
                    validation_status="supported",
                    created_at=BASE_TIME,
                )
            )
            await session.flush()
            session.add(
                DigestClaimCitationModel(
                    claim_id=c1_id,
                    snapshot_id=snap.id,
                    position=0,
                    created_at=BASE_TIME,
                )
            )
            await session.flush()

            # Digest 2 for the same date
            session.add(
                DigestModel(
                    id=d2_id,
                    digest_date=d_date,
                    title="Digest 2",
                    status="draft",
                    created_at=BASE_TIME,
                )
            )
            await session.flush()
            c2_id = new_id()
            session.add(
                DigestClaimModel(
                    id=c2_id,
                    digest_id=d2_id,
                    position=0,
                    text="Claim 2",
                    validation_status="supported",
                    created_at=BASE_TIME,
                )
            )
            await session.flush()
            session.add(
                DigestClaimCitationModel(
                    claim_id=c2_id,
                    snapshot_id=snap.id,
                    position=0,
                    created_at=BASE_TIME,
                )
            )
            await session.commit()

        # Publish digest 1
        async with sessionmaker() as session:
            d1 = await session.get(DigestModel, d1_id)
            assert d1 is not None
            d1.status = "published"
            await session.commit()

        # Attempt to publish digest 2 for the same date -> fails with unique violation
        async with sessionmaker() as session:
            d2 = await session.get(DigestModel, d2_id)
            assert d2 is not None
            d2.status = "published"
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 7. Referential Integrity & Deletion Restrictions
# -----------------------------------------------------------------------------
@run_async
async def test_referential_integrity_and_deletion_restrictions() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            _, snap = await _make_source_and_snapshot(session)
            await _ensure_subject(session, company_key="openai", product_key="gpt-4")

            fact = ExtractedFactModel(
                id=new_id(),
                snapshot_id=snap.id,
                company_key="openai",
                product_key="gpt-4",
                field="model",
                value="gpt-4o",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=snap.fetched_at,
                created_at=BASE_TIME,
            )
            session.add(fact)
            await session.commit()

        # Attempt to delete referenced snapshot
        async with sessionmaker() as session:
            # document_snapshots is protected both by trigger and ON DELETE RESTRICT
            with pytest.raises(DBAPIError):
                await session.execute(
                    text("DELETE FROM document_snapshots WHERE id = :id"),
                    {"id": snap.id},
                )
            await session.rollback()
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 8. current_facts Composite-Ownership-FK Consistency Test
# -----------------------------------------------------------------------------
@run_async
async def test_current_facts_composite_foreign_key_consistency() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            _, snap1 = await _make_source_and_snapshot(session)
            _, snap2 = await _make_source_and_snapshot(session)
            await _ensure_subject(session, company_key="openai", product_key="gpt-4")

            f_id = new_id()
            fact = ExtractedFactModel(
                id=f_id,
                snapshot_id=snap1.id,
                company_key="openai",
                product_key="gpt-4",
                field="pricing",
                value="$20",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=snap1.fetched_at,
                created_at=BASE_TIME,
            )
            session.add(fact)
            await session.commit()

        # Attempt to insert into current_facts with fact_id from snap1, but claiming snap2
        async with sessionmaker() as session:
            mismatched_cf = CurrentFactModel(
                company_key="openai",
                product_key="gpt-4",
                field="pricing",
                fact_id=f_id,
                snapshot_id=snap2.id,  # MISMATCH! Does not match fact's snapshot_id
                observed_at=snap1.fetched_at,
                extraction_version=1,
                updated_at=BASE_TIME,
            )
            session.add(mismatched_cf)
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 9. Real Concurrent-Writer Test with pg_advisory_xact_lock
# -----------------------------------------------------------------------------
@run_async
async def test_concurrent_writer_advisory_lock_serialization() -> None:
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        subject = Subject(company="ConcurrentInc", product="ModelX")
        execution_order: list[str] = []

        async def worker1() -> None:
            async with sessionmaker() as s1, s1.begin():
                store1 = PostgresFactStore(s1)
                await store1.ensure_subject(subject)
                await store1.lock_subject_fields(subject, ["capacity"])
                execution_order.append("worker1_acquired_lock")
                # Hold lock briefly to force worker2 to wait
                await asyncio.sleep(0.25)
                execution_order.append("worker1_releasing_lock")

        async def worker2() -> None:
            # Short sleep so worker1 acquires first
            await asyncio.sleep(0.05)
            async with sessionmaker() as s2, s2.begin():
                store2 = PostgresFactStore(s2)
                execution_order.append("worker2_waiting_lock")
                await store2.lock_subject_fields(subject, ["capacity"])
                execution_order.append("worker2_acquired_lock")

        await asyncio.gather(worker1(), worker2())

        assert execution_order == [
            "worker1_acquired_lock",
            "worker2_waiting_lock",
            "worker1_releasing_lock",
            "worker2_acquired_lock",
        ]
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 10. Composite Ownership FK on latest_snapshot_id Rejection
# -----------------------------------------------------------------------------
@run_async
async def test_source_item_latest_snapshot_composite_fk_rejection() -> None:
    """Prove setting latest_snapshot_id to a snapshot of a different source item is rejected."""
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            item1, snap1 = await _make_source_and_snapshot(session)
            _, snap2 = await _make_source_and_snapshot(session)
            await session.commit()

        # Step 1: Reject setting item1.latest_snapshot_id to snap2 (which belongs to item2)
        async with sessionmaker() as session:
            i1 = await session.get(SourceItemRow, item1.id)
            assert i1 is not None
            i1.latest_snapshot_id = snap2.id
            with pytest.raises(DBAPIError):
                await session.flush()
            await session.rollback()

        # Step 2: Valid setting to item1's own snapshot succeeds
        async with sessionmaker() as session:
            i1 = await session.get(SourceItemRow, item1.id)
            assert i1 is not None
            i1.latest_snapshot_id = snap1.id
            await session.commit()

        async with sessionmaker() as session:
            i1 = await session.get(SourceItemRow, item1.id)
            assert i1 is not None
            assert i1.latest_snapshot_id == snap1.id
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 11. Atomic Current-Pointer Advancement: 3 Outcomes Across Separate Transactions
# -----------------------------------------------------------------------------
@run_async
async def test_current_facts_atomic_upsert_concurrency_outcomes() -> None:  # pylint: disable=too-many-locals
    """Prove all 3 outcomes: first-write, newer-writer-wins, older-writer-ignored."""
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        subject = Subject(company="AtomicCo", product="EngineZ")
        t_early = BASE_TIME
        t_mid = BASE_TIME + timedelta(hours=1)
        t_late = BASE_TIME + timedelta(hours=2)

        async with sessionmaker() as setup_session:
            _, snap_early = await _make_source_and_snapshot(setup_session, fetched_at=t_early)
            _, snap_mid = await _make_source_and_snapshot(setup_session, fetched_at=t_mid)
            _, snap_late = await _make_source_and_snapshot(setup_session, fetched_at=t_late)
            await setup_session.commit()

        f_early = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_early.id,
            field="speed",
            value="100mph",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_mid = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_mid.id,
            field="speed",
            value="150mph",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_late = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_late.id,
            field="speed",
            value="200mph",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        # ---------------------------------------------------------------------
        # Outcome 1: First-write (no prior row, insert succeeds)
        # ---------------------------------------------------------------------
        async with sessionmaker() as session1:
            store1 = PostgresFactStore(session1)
            rec_mid = await store1.record_extracted_facts(
                subject, [f_mid], snapshot_observed_at=t_mid, extraction_version=1
            )
            res1 = await store1.advance_current_facts(subject, rec_mid)
            assert res1["speed"] is True
            await session1.commit()

        async with sessionmaker() as session_verify:
            cf1 = await session_verify.get(CurrentFactModel, ("atomicco", "enginez", "speed"))
            assert cf1 is not None
            assert cf1.fact_id == f_mid.id
            assert cf1.observed_at == t_mid

        # ---------------------------------------------------------------------
        # Outcome 2: Older-writer-ignored (an earlier observed_at is dropped)
        # ---------------------------------------------------------------------
        async with sessionmaker() as session2:
            store2 = PostgresFactStore(session2)
            rec_early = await store2.record_extracted_facts(
                subject, [f_early], snapshot_observed_at=t_early, extraction_version=1
            )
            res2 = await store2.advance_current_facts(subject, rec_early)
            # Must be False because t_early < t_mid
            assert res2["speed"] is False
            await session2.commit()

        async with sessionmaker() as session_verify:
            cf2 = await session_verify.get(CurrentFactModel, ("atomicco", "enginez", "speed"))
            assert cf2 is not None
            # Row remains unchanged at mid values
            assert cf2.fact_id == f_mid.id
            assert cf2.observed_at == t_mid

        # ---------------------------------------------------------------------
        # Outcome 3: Newer-writer-wins (a later observed_at overwrites)
        # ---------------------------------------------------------------------
        async with sessionmaker() as session3:
            store3 = PostgresFactStore(session3)
            rec_late = await store3.record_extracted_facts(
                subject, [f_late], snapshot_observed_at=t_late, extraction_version=1
            )
            res3 = await store3.advance_current_facts(subject, rec_late)
            # Must be True because t_late > t_mid
            assert res3["speed"] is True
            await session3.commit()

        async with sessionmaker() as session_verify:
            cf3 = await session_verify.get(CurrentFactModel, ("atomicco", "enginez", "speed"))
            assert cf3 is not None
            # Row overwritten with late values
            assert cf3.fact_id == f_late.id
            assert cf3.observed_at == t_late
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 12. Four-Part Ordering Tuple Regression against Real PostgreSQL
# -----------------------------------------------------------------------------
@run_async
async def test_current_facts_four_part_ordering_tuple_postgres() -> None:  # pylint: disable=too-many-locals,too-many-statements,protected-access
    """Test ADR 0011 4-part ordering tuple against real PostgreSQL with native UUID columns.

    Tuple: (observed_at DESC, snapshot_id DESC, extraction_version DESC, id DESC).
    Tests:
    1. Equal observed_at, distinct snapshot_id: lower snapshot_id with higher
       extraction_version loses to higher snapshot_id (tested in both arrival orders).
    2. Equal observed_at, snapshot_id, extraction_version: final tie-breaker by
       fact_id (tested in both arrival orders).
    3. Existing outcomes: first-write, newer-wins, older-ignored.
    """
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        t_eq = BASE_TIME + timedelta(days=2)
        id_a, id_b = new_id(), new_id()
        snap_id_low, snap_id_high = (id_a, id_b) if id_a < id_b else (id_b, id_a)

        async with sessionmaker() as setup_session:
            _, snap_low = await _make_source_and_snapshot(
                setup_session, snapshot_id=snap_id_low, fetched_at=t_eq
            )
            _, snap_high = await _make_source_and_snapshot(
                setup_session, snapshot_id=snap_id_high, fetched_at=t_eq
            )
            await setup_session.commit()

        # --- 1. Equal observed_at, distinct snapshot_id ---
        # snap_low has higher extraction_version (10), snap_high has lower extraction_version (1).
        # snap_high MUST win because snapshot_id (UUID) is compared before extraction_version.

        # Arrival order 1: Higher snapshot_id arrives first, then lower snapshot_id with
        # higher extraction_version arrives -> lower snapshot_id does NOT win.
        subj1 = Subject(company="PgTupleCo", product="ModelAlpha")
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

        async with sessionmaker() as s1:
            store1 = PostgresFactStore(s1)
            rec_high_1 = await store1.record_extracted_facts(
                subj1, [f_high_1], snapshot_observed_at=t_eq, extraction_version=1
            )
            res_high_1 = await store1.advance_current_facts(subj1, rec_high_1)
            assert res_high_1["benchmark_scores"] is True

            rec_low_1 = await store1.record_extracted_facts(
                subj1, [f_low_1], snapshot_observed_at=t_eq, extraction_version=10
            )
            res_low_1 = await store1.advance_current_facts(subj1, rec_low_1)
            # Must be False: higher extraction_version loses because snap_low < snap_high
            assert res_low_1["benchmark_scores"] is False
            await s1.commit()

        async with sessionmaker() as s_check:
            cf1 = await s_check.get(
                CurrentFactModel, ("pgtupleco", "modelalpha", "benchmark_scores")
            )
            assert cf1 is not None
            assert cf1.fact_id == f_high_1.id
            assert cf1.snapshot_id == snap_high.id
            assert cf1.extraction_version == 1

        # Arrival order 2: Lower snapshot_id with higher extraction_version arrives first,
        # then higher snapshot_id arrives -> higher snapshot_id DOES win and overwrites.
        subj2 = Subject(company="PgTupleCo", product="ModelBeta")
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

        async with sessionmaker() as s2:
            store2 = PostgresFactStore(s2)
            rec_low_2 = await store2.record_extracted_facts(
                subj2, [f_low_2], snapshot_observed_at=t_eq, extraction_version=10
            )
            res_low_2 = await store2.advance_current_facts(subj2, rec_low_2)
            assert res_low_2["benchmark_scores"] is True

            rec_high_2 = await store2.record_extracted_facts(
                subj2, [f_high_2], snapshot_observed_at=t_eq, extraction_version=1
            )
            res_high_2 = await store2.advance_current_facts(subj2, rec_high_2)
            # Must be True: higher snapshot_id wins over lower snapshot_id
            assert res_high_2["benchmark_scores"] is True
            await s2.commit()

        async with sessionmaker() as s_check:
            cf2 = await s_check.get(
                CurrentFactModel, ("pgtupleco", "modelbeta", "benchmark_scores")
            )
            assert cf2 is not None
            assert cf2.fact_id == f_high_2.id
            assert cf2.snapshot_id == snap_high.id
            assert cf2.extraction_version == 1

        # --- 2. Equal observed_at, snapshot_id, extraction_version: Tie-breaker by fact_id ---
        fid_a, fid_b = new_id(), new_id()
        fid_low, fid_high = (fid_a, fid_b) if fid_a < fid_b else (fid_b, fid_a)

        async with sessionmaker() as s_tie:
            await _ensure_subject(
                s_tie,
                company_key="pgtieco",
                product_key="modeltie",
                company="PgTieCo",
                product="ModelTie",
            )
            # Drop composite FK in this transaction so we can test the pure
            # row-comparison predicate on current_facts without conflicting with
            # uq_extracted_facts_attempt
            await s_tie.execute(
                text(
                    "ALTER TABLE current_facts "
                    "DROP CONSTRAINT fk_current_facts_extracted_fact_composite;"
                )
            )

            store_tie = PostgresFactStore(s_tie)

            # Arrival order 1: Lower fact_id arrives first, higher arrives second -> higher wins
            f_low_model = ExtractedFactModel(
                id=fid_low,
                snapshot_id=snap_high.id,
                company_key="pgtieco",
                product_key="modeltie",
                field="context_window_tokens",
                value="8000",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=t_eq,
                created_at=datetime.now(UTC),
            )
            f_high_model = ExtractedFactModel(
                id=fid_high,
                snapshot_id=snap_high.id,
                company_key="pgtieco",
                product_key="modeltie",
                field="context_window_tokens",
                value="8000",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=t_eq,
                created_at=datetime.now(UTC),
            )

            res_tie_1 = await store_tie._upsert_current_fact(
                company_key="pgtieco",
                product_key="modeltie",
                fact=f_low_model,
                now_dt=datetime.now(UTC),
                is_sqlite=False,
            )
            assert res_tie_1 is True

            res_tie_2 = await store_tie._upsert_current_fact(
                company_key="pgtieco",
                product_key="modeltie",
                fact=f_high_model,
                now_dt=datetime.now(UTC),
                is_sqlite=False,
            )
            assert res_tie_2 is True  # Higher UUID wins tie-breaker

            cf_tie_1 = await s_tie.get(
                CurrentFactModel, ("pgtieco", "modeltie", "context_window_tokens")
            )
            assert cf_tie_1 is not None
            assert cf_tie_1.fact_id == fid_high

            # Arrival order 2: Higher fact_id arrives first, lower arrives second -> lower loses
            f_high_model_2 = ExtractedFactModel(
                id=fid_high,
                snapshot_id=snap_high.id,
                company_key="pgtieco",
                product_key="modeltie",
                field="modalities",
                value="text",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=t_eq,
                created_at=datetime.now(UTC),
            )
            f_low_model_2 = ExtractedFactModel(
                id=fid_low,
                snapshot_id=snap_high.id,
                company_key="pgtieco",
                product_key="modeltie",
                field="modalities",
                value="text",
                disclosure_status="disclosed",
                extraction_method="deterministic",
                extraction_version=1,
                observed_at=t_eq,
                created_at=datetime.now(UTC),
            )

            res_tie_3 = await store_tie._upsert_current_fact(
                company_key="pgtieco",
                product_key="modeltie",
                fact=f_high_model_2,
                now_dt=datetime.now(UTC),
                is_sqlite=False,
            )
            assert res_tie_3 is True

            res_tie_4 = await store_tie._upsert_current_fact(
                company_key="pgtieco",
                product_key="modeltie",
                fact=f_low_model_2,
                now_dt=datetime.now(UTC),
                is_sqlite=False,
            )
            assert res_tie_4 is False  # Lower UUID loses tie-breaker

            cf_tie_2 = await s_tie.get(CurrentFactModel, ("pgtieco", "modeltie", "modalities"))
            assert cf_tie_2 is not None
            assert cf_tie_2.fact_id == fid_high

            # Rollback transaction so FK constraint is restored identically
            await s_tie.rollback()
    finally:
        await engine.dispose()


# -----------------------------------------------------------------------------
# 13. Real Two-Connection Concurrency with Overlapping Transactions
# -----------------------------------------------------------------------------
@run_async
async def test_current_facts_real_two_connection_concurrency() -> None:  # pylint: disable=too-many-locals,too-many-statements
    """Test two genuinely overlapping database connections against PostgreSQL.

    One transaction begins a write and holds it before commit, while the second attempts
    a conflicting write on the same (company_key, product_key, field).
    Asserts:
    1. The final state is correct according to the 4-part row-comparison predicate.
    2. Any Change emitted afterward uses the actually-committed prior state (not a stale read).
    """
    engine = _get_engine()
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        subject = Subject(company="ConcurCo", product="EngineAsync")
        t_base = BASE_TIME
        t1 = BASE_TIME + timedelta(hours=1)
        t2 = BASE_TIME + timedelta(hours=2)

        # Baseline: Establish initial committed state
        async with sessionmaker() as setup_session:
            _, snap_base = await _make_source_and_snapshot(setup_session, fetched_at=t_base)
            _, snap1 = await _make_source_and_snapshot(setup_session, fetched_at=t1)
            _, snap2 = await _make_source_and_snapshot(setup_session, fetched_at=t2)
            await setup_session.commit()

        f_base = ExtractedFact(
            id=new_id(),
            snapshot_id=snap_base.id,
            field="pricing",
            value="$100",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f1 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="pricing",
            value="$150",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f2 = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="pricing",
            value="$200",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        async with sessionmaker() as init_session:
            init_store = PostgresFactStore(init_session)
            rec_base = await init_store.record_extracted_facts(
                subject, [f_base], snapshot_observed_at=t_base, extraction_version=1
            )
            await init_store.advance_current_facts(subject, rec_base)
            await init_session.commit()

        # Coordination events
        tx1_wrote = asyncio.Event()
        tx2_started = asyncio.Event()

        change_results: list[Any] = []

        async def _run_tx1() -> None:
            # Transaction 1: Writes f1 ($150 at t1) and holds the row lock before commit
            async with sessionmaker() as s1:
                store1 = PostgresFactStore(s1)
                rec1 = await store1.record_extracted_facts(
                    subject, [f1], snapshot_observed_at=t1, extraction_version=1
                )
                adv1 = await store1.advance_current_facts(subject, rec1)
                assert adv1["pricing"] is True
                # s1 holds uncommitted row write lock in PostgreSQL
                tx1_wrote.set()

                # Wait until tx2 has attempted its operation
                await tx2_started.wait()
                # Yield control briefly so tx2 is actively queued/blocked in DB
                await asyncio.sleep(0.05)
                await s1.commit()

        async def _run_tx2() -> None:
            # Wait until Tx1 has performed its write and holds the lock
            await tx1_wrote.wait()
            async with sessionmaker() as s2:
                store2 = PostgresFactStore(s2)
                tx2_started.set()

                # detect_and_persist_changes takes advisory lock & updates current_facts.
                # Because Tx1 is in-flight, this call serializes behind Tx1.
                changes = await store2.detect_and_persist_changes(
                    subject,
                    [f2],
                    snapshot_observed_at=t2,
                    detected_at=t2,
                    extraction_version=1,
                )
                await s2.commit()
                change_results.extend(changes)

        # Run Tx1 and Tx2 concurrently across separate database connections
        await asyncio.gather(_run_tx1(), _run_tx2())

        # Verify final state in PostgreSQL:
        # Newer write (t2 > t1) must win
        async with sessionmaker() as verify_session:
            cf = await verify_session.get(CurrentFactModel, ("concurco", "engineasync", "pricing"))
            assert cf is not None
            assert cf.fact_id == f2.id
            assert cf.snapshot_id == snap2.id
            assert cf.observed_at == t2

        # Verify change emitted by Tx2 used the actually-committed prior state from Tx1
        # ($150 at snap1), NOT the stale read from before Tx1 committed ($100 at snap_base)!
        assert len(change_results) == 1
        ch = change_results[0]
        assert ch.field == "pricing"
        assert ch.previous.value == "$150"
        assert ch.previous.snapshot_id == snap1.id
        assert ch.current.value == "$200"
        assert ch.current.snapshot_id == snap2.id

        # Also test the reverse race: held transaction has HIGHER precedence (t2),
        # concurrent conflicting transaction has LOWER precedence (t1).
        # When t2 commits, the unblocked t1 write must be IGNORED by the conflict predicate,
        # and emit zero changes.
        f_late_holder = ExtractedFact(
            id=new_id(),
            snapshot_id=snap2.id,
            field="speed",
            value="300mph",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        f_early_challenger = ExtractedFact(
            id=new_id(),
            snapshot_id=snap1.id,
            field="speed",
            value="250mph",
            disclosure_status=DisclosureStatus.DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

        rev_tx1_wrote = asyncio.Event()
        rev_tx2_started = asyncio.Event()
        rev_changes: list[Any] = []

        async def _run_rev_tx1() -> None:
            # Tx1 writes higher precedence (t2) and holds
            async with sessionmaker() as s1:
                store1 = PostgresFactStore(s1)
                rec = await store1.record_extracted_facts(
                    subject, [f_late_holder], snapshot_observed_at=t2, extraction_version=1
                )
                adv = await store1.advance_current_facts(subject, rec)
                assert adv["speed"] is True
                rev_tx1_wrote.set()
                await rev_tx2_started.wait()
                await asyncio.sleep(0.05)
                await s1.commit()

        async def _run_rev_tx2() -> None:
            await rev_tx1_wrote.wait()
            async with sessionmaker() as s2:
                store2 = PostgresFactStore(s2)
                rev_tx2_started.set()
                # Attempts conflicting write with lower precedence (t1)
                changes = await store2.detect_and_persist_changes(
                    subject,
                    [f_early_challenger],
                    snapshot_observed_at=t1,
                    detected_at=t1,
                    extraction_version=1,
                )
                await s2.commit()
                rev_changes.extend(changes)

        await asyncio.gather(_run_rev_tx1(), _run_rev_tx2())

        # Assert final state is still the higher precedence write (t2)
        async with sessionmaker() as verify_session:
            cf_speed = await verify_session.get(
                CurrentFactModel, ("concurco", "engineasync", "speed")
            )
            assert cf_speed is not None
            assert cf_speed.fact_id == f_late_holder.id
            assert cf_speed.observed_at == t2

        # Lower precedence write emitted NO Change because its write was rejected
        # by conflict predicate
        assert len(rev_changes) == 0
    finally:
        await engine.dispose()
