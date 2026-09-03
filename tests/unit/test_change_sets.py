import uuid
from datetime import UTC, datetime

import pytest

from ai_daily_digest.intelligence.change_sets import build_change_sets, get_or_create_change_set_id
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import Change, FactObservation, Subject
from tests.uuid_samples import CHANGE_SET_1

OPENAI_GPT4O = Subject(company="OpenAI", product="GPT-4o")
ANTHROPIC_CLAUDE = Subject(company="Anthropic", product="Claude")

TCS_SNAP_PREV = uuid.UUID("019e85a1-4be8-7b73-bc48-d731901bf400")
TCS_SNAP_CUR = uuid.UUID("01a01c77-afe8-7c32-83b8-8c9a0d1d2d61")
TCS_SNAP_BENCH = uuid.UUID("01a01751-53e8-7df3-92dc-a85214da042c")
TCS_SNAP_PRICE = uuid.UUID("01a01c77-b3d0-7aa1-bc02-68c46d5f5382")
TCS_SNAP_A = uuid.UUID("01a01c77-b7b8-7d21-bcde-e9caadc8d8c0")
TCS_SNAP_B = uuid.UUID("01a01c77-bba0-7cd0-a773-1c8948b3ece7")
TCS_SNAP_C = uuid.UUID("01a01c77-bf88-7c11-be32-9458f9269a2c")
TCS_SNAP_NEW = uuid.UUID("01a01c77-c370-7ec1-871d-c00132333847")

# The orchestrator's injected batch detection time (ADR 0008 section 5.A) --
# .250000 microseconds on purpose, so tests can assert it survives intact.
TCS_DETECTED_AT = datetime(2026, 8, 20, 12, 0, 0, 250000, tzinfo=UTC)


def _change(
    subject: Subject,
    field: str,
    previous_snap: uuid.UUID | None,
    current_snap: uuid.UUID,
    change_set_id: uuid.UUID,
    change_id: uuid.UUID | None = None,
) -> Change:
    """ADR 0007: Change.change_set_id is always a real, valid UUID v7 from
    construction on -- there is no more empty-string placeholder for
    build_change_sets() to backfill. Callers of this test helper supply
    the (already-allocated) change_set_id explicitly, the same way the
    real batch-scoped allocator (get_or_create_change_set_id(), used via
    FactStore.update_fact()'s change_set_id_factory) does in production.
    change_type follows previous_snap -- Change's own invariant validator
    only allows previous=None for change_type="disclosed" (a genuine
    first disclosure); build_change_sets() itself doesn't care which
    change_type it groups, only the snapshot ids."""
    return Change(
        id=change_id if change_id is not None else new_id(),
        change_set_id=change_set_id,
        subject=subject,
        field=field,
        change_type="changed" if previous_snap else "disclosed",
        previous=(
            FactObservation(value="old", snapshot_id=previous_snap) if previous_snap else None
        ),
        current=FactObservation(value="new", snapshot_id=current_snap),
        confidence=0.9,
        detected_at=TCS_DETECTED_AT,
    )


def test_no_changes_yields_no_change_sets() -> None:
    assert build_change_sets([]) == []


def test_one_change_set_per_subject() -> None:
    openai_cs_id = CHANGE_SET_1
    anthropic_cs_id = uuid.UUID("01a01c78-c000-7000-8000-000000000001")
    changes = [
        _change(OPENAI_GPT4O, "context_window_tokens", TCS_SNAP_PREV, TCS_SNAP_CUR, openai_cs_id),
        _change(ANTHROPIC_CLAUDE, "benchmark_scores", None, TCS_SNAP_BENCH, anthropic_cs_id),
    ]
    change_sets = build_change_sets(changes)
    assert len(change_sets) == 2
    subjects = {cs.subject for cs in change_sets}
    assert subjects == {OPENAI_GPT4O, ANTHROPIC_CLAUDE}


def test_changes_for_the_same_subject_are_grouped_into_one_set() -> None:
    cs_id = CHANGE_SET_1
    changes = [
        _change(OPENAI_GPT4O, "context_window_tokens", TCS_SNAP_PREV, TCS_SNAP_CUR, cs_id),
        _change(OPENAI_GPT4O, "input_price_usd", None, TCS_SNAP_PRICE, cs_id),
    ]
    change_sets = build_change_sets(changes)
    assert len(change_sets) == 1
    assert len(change_sets[0].changes) == 2


def test_change_set_id_is_read_from_the_already_present_changes() -> None:
    """ADR 0007: build_change_sets() no longer invents or backfills an
    id -- every Change it receives already carries its final,
    correctly-allocated change_set_id (from FactStore.update_fact()'s
    lazy change_set_id_factory). This proves it reads that value through
    rather than minting a new one."""
    cs_id = CHANGE_SET_1
    changes = [_change(OPENAI_GPT4O, "context_window_tokens", TCS_SNAP_PREV, TCS_SNAP_CUR, cs_id)]
    change_sets = build_change_sets(changes)
    assert change_sets[0].id == cs_id
    assert change_sets[0].changes[0].change_set_id == cs_id
    # the caller's original Change objects are untouched, not mutated
    assert changes[0].change_set_id == cs_id


def test_inconsistent_change_set_ids_for_one_subject_raises() -> None:
    """ADR 0007's ChangeSet consistency invariant: every Change grouped
    under one subject must carry the same change_set_id -- this is a
    construction-time guarantee the real allocator always provides, but
    build_change_sets() must still verify it rather than trust it
    silently, and must never silently pick the first or last id among
    values that disagree. Only a corrupted/hand-built input like this one
    can produce the mismatch; the batch-scoped allocator itself cannot."""
    mismatched_id = uuid.UUID("01a01c78-d000-7000-8000-000000000002")
    changes = [
        _change(OPENAI_GPT4O, "context_window_tokens", TCS_SNAP_PREV, TCS_SNAP_CUR, CHANGE_SET_1),
        _change(OPENAI_GPT4O, "input_price_usd", None, TCS_SNAP_PRICE, mismatched_id),
    ]
    with pytest.raises(ValueError, match="Inconsistent change_set_id"):
        build_change_sets(changes)


def test_snapshot_ids_are_deduped_and_order_preserving() -> None:
    cs_id = CHANGE_SET_1
    changes = [
        _change(OPENAI_GPT4O, "context_window_tokens", TCS_SNAP_A, TCS_SNAP_B, cs_id),
        _change(OPENAI_GPT4O, "input_price_usd", TCS_SNAP_A, TCS_SNAP_C, cs_id),
    ]
    change_set = build_change_sets(changes)[0]
    assert change_set.previous_snapshot_ids == [TCS_SNAP_A]
    assert change_set.current_snapshot_ids == [TCS_SNAP_B, TCS_SNAP_C]


def test_first_disclosure_with_no_previous_snapshot_is_not_added() -> None:
    change_set = build_change_sets(
        [_change(OPENAI_GPT4O, "benchmark_scores", None, TCS_SNAP_NEW, CHANGE_SET_1)]
    )[0]
    assert change_set.previous_snapshot_ids == []
    assert change_set.current_snapshot_ids == [TCS_SNAP_NEW]


# --- get_or_create_change_set_id() -- the batch-scoped allocator itself ---


def test_get_or_create_allocates_once_per_subject() -> None:
    allocator: dict[Subject, uuid.UUID] = {}
    first = get_or_create_change_set_id(allocator, OPENAI_GPT4O)
    second = get_or_create_change_set_id(allocator, OPENAI_GPT4O)
    assert first == second


def test_get_or_create_gives_different_subjects_different_ids() -> None:
    allocator: dict[Subject, uuid.UUID] = {}
    openai_id = get_or_create_change_set_id(allocator, OPENAI_GPT4O)
    anthropic_id = get_or_create_change_set_id(allocator, ANTHROPIC_CLAUDE)
    assert openai_id != anthropic_id


def test_get_or_create_does_not_call_new_id_when_the_subject_already_has_one() -> None:
    """The exact bug `dict.setdefault(subject, new_id())` would have:
    Python evaluates every argument before setdefault() runs, so that
    expression would call new_id() unconditionally on every invocation.
    Proven here by call count, not by re-deriving the value."""
    from unittest.mock import patch

    allocator: dict[Subject, uuid.UUID] = {}
    with patch("ai_daily_digest.intelligence.change_sets.new_id") as mock_new_id:
        mock_new_id.return_value = uuid.UUID("01a01c79-0000-7000-8000-000000000003")
        get_or_create_change_set_id(allocator, OPENAI_GPT4O)
        get_or_create_change_set_id(allocator, OPENAI_GPT4O)
        get_or_create_change_set_id(allocator, OPENAI_GPT4O)
    assert mock_new_id.call_count == 1


def test_a_fresh_allocator_produces_a_different_id_for_the_same_subject() -> None:
    """A second, later batch (a fresh allocator dict, as run_daily()
    builds per call via a new _BatchAccumulator) must not reuse a
    previous run's change_set_id for a recurring subject."""
    first_run: dict[Subject, uuid.UUID] = {}
    second_run: dict[Subject, uuid.UUID] = {}
    first_id = get_or_create_change_set_id(first_run, OPENAI_GPT4O)
    second_id = get_or_create_change_set_id(second_run, OPENAI_GPT4O)
    assert first_id != second_id
