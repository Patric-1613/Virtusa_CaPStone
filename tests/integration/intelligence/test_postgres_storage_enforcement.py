"""Real PostgreSQL integration tests for storage-level enforcement — ADR 0011 §7."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime
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

            sub = SubjectModel(
                company_key="openai",
                product_key="gpt-4",
                company="OpenAI",
                product="GPT-4",
                created_at=BASE_TIME,
            )
            session.add(sub)
            await session.flush()

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
            sub = SubjectModel(
                company_key="openai",
                product_key="gpt-4",
                company="OpenAI",
                product="GPT-4",
                created_at=BASE_TIME,
            )
            session.add(sub)
            await session.flush()

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
                await session.execute(text("TRUNCATE TABLE extracted_facts"))
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
            sub1 = SubjectModel(
                company_key="openai",
                product_key="gpt-4",
                company="OpenAI",
                product="GPT-4",
                created_at=BASE_TIME,
            )
            sub2 = SubjectModel(
                company_key="anthropic",
                product_key="claude",
                company="Anthropic",
                product="Claude",
                created_at=BASE_TIME,
            )
            session.add(sub1)
            session.add(sub2)
            await session.flush()

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
                claim_text="Supported Claim",
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
                        INSERT INTO digest_claims (id, digest_id, position, claim_text, validation_status, created_at)
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
                    claim_text="Claim 1",
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
                    claim_text="Claim 2",
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
            sub = SubjectModel(
                company_key="openai",
                product_key="gpt-4",
                company="OpenAI",
                product="GPT-4",
                created_at=BASE_TIME,
            )
            session.add(sub)
            await session.flush()

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

            sub = SubjectModel(
                company_key="openai",
                product_key="gpt-4",
                company="OpenAI",
                product="GPT-4",
                created_at=BASE_TIME,
            )
            session.add(sub)
            await session.flush()

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
