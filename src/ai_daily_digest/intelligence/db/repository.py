"""PostgreSQL FactStore and change persistence repository — ADR 0011 §5."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_daily_digest.intelligence.db.models import (
    ChangeModel,
    ChangeSetModel,
    CurrentFactModel,
    ExtractedFactModel,
    SubjectModel,
)
from ai_daily_digest.intelligence.facts import _infer_change_type, normalise_name
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import (
    Change,
    Confidence,
    ExtractedFact,
    FactObservation,
    Subject,
    normalize_ordering_timestamp,
    validate_change_shape,
)

__all__ = ["PostgresFactStore"]


def _subject_keys(subject: Subject) -> tuple[str, str]:
    """Return normalized canonical (company_key, product_key)."""
    return normalise_name(subject.company), normalise_name(subject.product)


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _hash_lock_key(company_key: str, product_key: str, field: str) -> int:
    """Generate a stable 63-bit signed integer for pg_advisory_xact_lock."""
    raw = f"{company_key}:{product_key}:{field}".encode()
    digest = hashlib.sha256(raw).digest()
    # Use first 8 bytes signed integer (PostgreSQL bigint advisory lock key)
    val = int.from_bytes(digest[:8], byteorder="big", signed=True)
    return val


@dataclass(frozen=True)
class PriorFactState:
    """Immutable snapshot of prior current_fact state before advance."""

    fact_id: uuid.UUID
    snapshot_id: uuid.UUID
    observed_at: datetime
    extraction_version: int
    value: str | None
    disclosure_status: str


class PostgresFactStore:
    """Concrete PostgreSQL persistence repository for intelligence facts and changes.

    Guarantees:
    - Advisory locking on (subject, field) serializes read-compare-advance sequences.
    - 4-part ordering tuple: (observed_at DESC, snapshot_id DESC, extraction_version DESC, id DESC).
    - Idempotent replay with full 10-attribute verification.
    - Corrections on the current snapshot advance pointers without emitting false Changes.
    - ChangeSet citations derived dynamically with MIN(position) ASC order.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_subject(self, subject: Subject) -> SubjectModel:
        """Find or create canonical subject record (first-seen display name wins)."""
        ck, pk = _subject_keys(subject)
        existing = await self._session.get(SubjectModel, (ck, pk))
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        model = SubjectModel(
            company_key=ck,
            product_key=pk,
            company=subject.company,
            product=subject.product,
            created_at=now,
        )
        self._session.add(model)
        await self._session.flush()
        return model

    async def lock_subject_fields(self, subject: Subject, fields: Sequence[str]) -> None:
        """Acquire transaction-scoped advisory locks on (subject, field) in sorted order."""
        bind = self._session.bind
        dialect_name = bind.dialect.name if bind is not None else ""
        if dialect_name != "postgresql":
            # In SQLite or mock engines, row-level advisory lock functions do not exist
            return

        ck, pk = _subject_keys(subject)
        sorted_fields = sorted(fields)
        for field_name in sorted_fields:
            lock_id = _hash_lock_key(ck, pk, field_name)
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )

    async def record_extracted_facts(  # pylint: disable=too-many-branches
        self,
        subject: Subject,
        facts: Sequence[ExtractedFact],
        *,
        snapshot_observed_at: datetime,
        extraction_version: int = 1,
    ) -> list[ExtractedFactModel]:
        """Insert extracted facts idempotently; verify on replay; fail closed on divergence."""
        await self.ensure_subject(subject)
        ck, pk = _subject_keys(subject)

        recorded: list[ExtractedFactModel] = []
        for fact in facts:
            # Check for existing record
            stmt = select(ExtractedFactModel).where(
                ExtractedFactModel.snapshot_id == fact.snapshot_id,
                ExtractedFactModel.company_key == ck,
                ExtractedFactModel.product_key == pk,
                ExtractedFactModel.field == fact.field,
                ExtractedFactModel.extraction_version == extraction_version,
            )
            res = await self._session.execute(stmt)
            existing = res.scalar_one_or_none()

            if existing is not None:
                # Replay verification: all 10 immutable attributes must match
                mismatches: list[str] = []
                if existing.value != fact.value:
                    mismatches.append(f"value: {existing.value} != {fact.value}")
                if existing.disclosure_status != fact.disclosure_status:
                    mismatches.append(
                        f"disclosure_status: {existing.disclosure_status} != "
                        f"{fact.disclosure_status}"
                    )
                if existing.extraction_method != fact.extraction_method:
                    mismatches.append(
                        f"extraction_method: {existing.extraction_method} != "
                        f"{fact.extraction_method}"
                    )
                if existing.extraction_model != fact.extraction_model:
                    mismatches.append(
                        f"extraction_model: {existing.extraction_model} != {fact.extraction_model}"
                    )
                if existing.prompt_version != fact.prompt_version:
                    mismatches.append(
                        f"prompt_version: {existing.prompt_version} != {fact.prompt_version}"
                    )
                if existing.quoted_span != fact.quoted_span:
                    mismatches.append(f"quoted_span: {existing.quoted_span} != {fact.quoted_span}")
                if existing.confidence is not None and fact.confidence is not None:
                    if abs(existing.confidence - fact.confidence) > 1e-6:
                        mismatches.append(f"confidence: {existing.confidence} != {fact.confidence}")
                elif existing.confidence != fact.confidence:
                    mismatches.append(f"confidence: {existing.confidence} != {fact.confidence}")
                if existing.observed_at != snapshot_observed_at:
                    mismatches.append(
                        f"observed_at: {existing.observed_at} != {snapshot_observed_at}"
                    )

                if mismatches:
                    msg = (
                        f"Replay verification failed for fact {fact.field} on "
                        f"snapshot {fact.snapshot_id}: " + "; ".join(mismatches)
                    )
                    raise ValueError(msg)
                recorded.append(existing)
            else:
                model = ExtractedFactModel(
                    id=fact.id,
                    snapshot_id=fact.snapshot_id,
                    company_key=ck,
                    product_key=pk,
                    field=fact.field,
                    value=fact.value,
                    disclosure_status=fact.disclosure_status,
                    extraction_method=fact.extraction_method,
                    extraction_model=fact.extraction_model,
                    prompt_version=fact.prompt_version,
                    extraction_version=extraction_version,
                    quoted_span=fact.quoted_span,
                    confidence=fact.confidence,
                    observed_at=snapshot_observed_at,
                    created_at=datetime.now(UTC),
                )
                self._session.add(model)
                await self._session.flush()
                recorded.append(model)

        return recorded

    async def read_current_facts(
        self, subject: Subject, fields: Sequence[str]
    ) -> dict[str, tuple[CurrentFactModel, ExtractedFactModel]]:
        """Read current confirmed facts and their referenced extracted facts."""
        ck, pk = _subject_keys(subject)
        stmt = (
            select(CurrentFactModel, ExtractedFactModel)
            .join(
                ExtractedFactModel,
                CurrentFactModel.fact_id == ExtractedFactModel.id,
            )
            .where(
                CurrentFactModel.company_key == ck,
                CurrentFactModel.product_key == pk,
                CurrentFactModel.field.in_(fields),
            )
        )
        res = await self._session.execute(stmt)
        return {cf.field: (cf, ef) for cf, ef in res.all()}

    async def _upsert_current_fact(
        self,
        *,
        company_key: str,
        product_key: str,
        fact: ExtractedFactModel,
        now_dt: datetime,
        is_sqlite: bool,
    ) -> bool:
        fid = fact.id.hex if is_sqlite else fact.id
        sid = fact.snapshot_id.hex if is_sqlite else fact.snapshot_id
        obs = (
            fact.observed_at.isoformat()
            if is_sqlite and hasattr(fact.observed_at, "isoformat")
            else fact.observed_at
        )
        upd = now_dt.isoformat() if is_sqlite else now_dt

        stmt = text("""
            INSERT INTO current_facts
                (company_key, product_key, field, fact_id, snapshot_id, observed_at,
                 extraction_version, updated_at)
            VALUES (:company_key, :product_key, :field, :fact_id, :snapshot_id, :observed_at,
                    :extraction_version, :updated_at)
            ON CONFLICT (company_key, product_key, field)
            DO UPDATE SET
                fact_id = EXCLUDED.fact_id,
                snapshot_id = EXCLUDED.snapshot_id,
                observed_at = EXCLUDED.observed_at,
                extraction_version = EXCLUDED.extraction_version,
                updated_at = EXCLUDED.updated_at
            WHERE (EXCLUDED.observed_at, EXCLUDED.snapshot_id, EXCLUDED.extraction_version, EXCLUDED.fact_id)
                > (current_facts.observed_at, current_facts.snapshot_id, current_facts.extraction_version,
                   current_facts.fact_id)
            RETURNING fact_id
        """)
        result = await self._session.execute(
            stmt,
            {
                "company_key": company_key,
                "product_key": product_key,
                "field": fact.field,
                "fact_id": fid,
                "snapshot_id": sid,
                "observed_at": obs,
                "extraction_version": fact.extraction_version,
                "updated_at": upd,
            },
        )
        return result.first() is not None

    async def advance_current_facts(
        self,
        subject: Subject,
        recorded_facts: Sequence[ExtractedFactModel],
    ) -> dict[str, bool]:
        """Conditionally advance current_facts using PostgreSQL atomic upsert.

        Executes atomic INSERT ... ON CONFLICT DO UPDATE
        WHERE EXCLUDED.observed_at > current_facts.observed_at.
        Returns a dict mapping field_name -> bool (True if the pointer advanced).
        """
        ck, pk = _subject_keys(subject)
        advanced_map: dict[str, bool] = {}
        now_dt = datetime.now(UTC)

        bind = self._session.get_bind()
        is_sqlite = bind is not None and bind.dialect.name == "sqlite"

        for fact in recorded_facts:
            advanced = await self._upsert_current_fact(
                company_key=ck,
                product_key=pk,
                fact=fact,
                now_dt=now_dt,
                is_sqlite=is_sqlite,
            )
            advanced_map[fact.field] = advanced

        await self._session.flush()
        return advanced_map

    async def detect_and_persist_changes(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        subject: Subject,
        facts: Sequence[ExtractedFact],
        *,
        snapshot_observed_at: datetime,
        detected_at: datetime,
        extraction_version: int = 1,
        change_set_id: uuid.UUID | None = None,
    ) -> list[Change]:
        """Detect and persist changes atomically under an advisory lock.

        Invariants enforced:
        - Advisory locks on all candidate fields in lexicographical order.
        - Corrections on the current snapshot advance pointers without emitting a Change.
        - Losing or identical candidates emit no Change.
        - Distinct snapshot differences emit valid Changes with explicit positions.
        - Entire batch commits atomically.
        """
        detection_time = normalize_ordering_timestamp(detected_at)
        resolved_change_set_id = change_set_id or new_id()

        ck, pk = _subject_keys(subject)
        fields = [f.field for f in facts]
        await self.lock_subject_fields(subject, fields)

        # Read current state prior to advance into an immutable snapshot
        current_map = await self.read_current_facts(subject, fields)
        prior_state: dict[str, PriorFactState] = {
            field: PriorFactState(
                fact_id=cf.fact_id,
                snapshot_id=cf.snapshot_id,
                observed_at=_ensure_utc(cf.observed_at),
                extraction_version=cf.extraction_version,
                value=ef.value,
                disclosure_status=ef.disclosure_status,
            )
            for field, (cf, ef) in current_map.items()
        }

        # Record facts into immutable extracted_facts
        recorded_facts = await self.record_extracted_facts(
            subject,
            facts,
            snapshot_observed_at=snapshot_observed_at,
            extraction_version=extraction_version,
        )

        # Advance current facts conditionally
        advanced_map = await self.advance_current_facts(subject, recorded_facts)

        candidate_changes: list[Change] = []
        fact_by_field = {f.field: f for f in recorded_facts}

        for field_name, advanced in advanced_map.items():
            if not advanced:
                continue

            candidate_fact = fact_by_field[field_name]
            prior = prior_state.get(field_name)

            if prior is None:
                # Genuine first observation: establishes baseline state
                # without emitting a business Change
                continue

            # INVARIANT: Correction of the same snapshot must NOT emit a business Change!
            if prior.snapshot_id == candidate_fact.snapshot_id:
                continue

            # Check if values actually differ
            if (
                prior.value == candidate_fact.value
                and prior.disclosure_status == candidate_fact.disclosure_status
            ):
                continue

            prev_obs = FactObservation(
                value=prior.value,
                observed_at=prior.observed_at,
                snapshot_id=prior.snapshot_id,
            )
            curr_obs = FactObservation(
                value=candidate_fact.value,
                observed_at=_ensure_utc(candidate_fact.observed_at),
                snapshot_id=candidate_fact.snapshot_id,
            )

            change_type = str(_infer_change_type(prev_obs.value, curr_obs.value))
            conf: Confidence = (
                candidate_fact.confidence if candidate_fact.confidence is not None else 1.0
            )

            validate_change_shape(change_type, prev_obs, curr_obs)

            change_obj = Change(
                id=new_id(),
                change_set_id=resolved_change_set_id,
                detected_at=detection_time,
                subject=subject,
                field=field_name,
                change_type=change_type,
                previous=prev_obs,
                current=curr_obs,
                confidence=conf,
                review_status="pending",
            )
            candidate_changes.append(change_obj)

        if not candidate_changes:
            return []

        # Allocate or use provided ChangeSet ID (consistent for all changes in this detection batch)
        cs_model = ChangeSetModel(
            id=resolved_change_set_id,
            company_key=ck,
            product_key=pk,
            review_status="pending",
            created_at=datetime.now(UTC),
        )
        self._session.add(cs_model)
        await self._session.flush()

        for position, change in enumerate(candidate_changes):
            ch_model = ChangeModel(
                id=change.id,
                detected_at=change.detected_at,
                change_set_id=resolved_change_set_id,
                position=position,
                company_key=ck,
                product_key=pk,
                field=change.field,
                change_type=change.change_type,
                confidence=change.confidence,
                review_status=change.review_status,
                previous_value=change.previous.value if change.previous else None,
                previous_observed_at=change.previous.observed_at if change.previous else None,
                previous_snapshot_id=change.previous.snapshot_id if change.previous else None,
                current_value=change.current.value,
                current_observed_at=change.current.observed_at,
                current_snapshot_id=change.current.snapshot_id,
                created_at=datetime.now(UTC),
            )
            self._session.add(ch_model)

        await self._session.flush()
        return candidate_changes

    async def get_changes_for_changeset(self, change_set_id: uuid.UUID) -> list[ChangeModel]:
        """Fetch changes for a ChangeSet ordered deterministically by position ASC."""
        stmt = (
            select(ChangeModel)
            .where(ChangeModel.change_set_id == change_set_id)
            .order_by(ChangeModel.position.asc())
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def derive_changeset_citations(
        self, change_set_id: uuid.UUID
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        """Derive (current_snapshot_ids, previous_snapshot_ids) ordered by MIN(position) ASC."""
        current_stmt = (
            select(ChangeModel.current_snapshot_id)
            .where(ChangeModel.change_set_id == change_set_id)
            .group_by(ChangeModel.current_snapshot_id)
            .order_by(func.min(ChangeModel.position).asc())
        )
        curr_res = await self._session.execute(current_stmt)
        current_ids = list(curr_res.scalars().all())

        previous_stmt = (
            select(ChangeModel.previous_snapshot_id)
            .where(
                ChangeModel.change_set_id == change_set_id,
                ChangeModel.previous_snapshot_id.is_not(None),
            )
            .group_by(ChangeModel.previous_snapshot_id)
            .order_by(func.min(ChangeModel.position).asc())
        )
        prev_res = await self._session.execute(previous_stmt)
        previous_ids = [pid for pid in prev_res.scalars().all() if pid is not None]

        return current_ids, previous_ids
