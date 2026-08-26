from ai_daily_digest.intelligence.change_sets import build_change_sets
from ai_daily_digest.shared.schemas import Change, FactObservation, Subject

OPENAI_GPT4O = Subject(company="OpenAI", product="GPT-4o")
ANTHROPIC_CLAUDE = Subject(company="Anthropic", product="Claude")


def _change(subject: Subject, field: str, previous_snap: str | None, current_snap: str) -> Change:
    return Change(
        id=f"change-{field}-{current_snap}",
        change_set_id="",  # the exact placeholder build_change_sets() must overwrite
        subject=subject,
        field=field,
        change_type="changed",
        previous=(
            FactObservation(value="old", snapshot_id=previous_snap) if previous_snap else None
        ),
        current=FactObservation(value="new", snapshot_id=current_snap),
        confidence=0.9,
    )


def test_no_changes_yields_no_change_sets() -> None:
    assert build_change_sets([]) == []


def test_one_change_set_per_subject() -> None:
    changes = [
        _change(OPENAI_GPT4O, "context_window_tokens", "snap_prev", "snap_cur"),
        _change(ANTHROPIC_CLAUDE, "benchmark_scores", None, "snap_bench"),
    ]
    change_sets = build_change_sets(changes)
    assert len(change_sets) == 2
    subjects = {cs.subject for cs in change_sets}
    assert subjects == {OPENAI_GPT4O, ANTHROPIC_CLAUDE}


def test_changes_for_the_same_subject_are_grouped_into_one_set() -> None:
    changes = [
        _change(OPENAI_GPT4O, "context_window_tokens", "snap_prev", "snap_cur"),
        _change(OPENAI_GPT4O, "input_price_usd", None, "snap_price"),
    ]
    change_sets = build_change_sets(changes)
    assert len(change_sets) == 1
    assert len(change_sets[0].changes) == 2


def test_change_set_id_is_backfilled_onto_every_grouped_change() -> None:
    """The exact gap the review flagged: Changes must not leave this
    function still carrying an empty change_set_id."""
    changes = [_change(OPENAI_GPT4O, "context_window_tokens", "snap_prev", "snap_cur")]
    change_sets = build_change_sets(changes)
    assert change_sets[0].id != ""
    assert change_sets[0].changes[0].change_set_id == change_sets[0].id
    # the caller's original Change objects are untouched, not mutated
    assert changes[0].change_set_id == ""


def test_snapshot_ids_are_deduped_and_order_preserving() -> None:
    changes = [
        _change(OPENAI_GPT4O, "context_window_tokens", "snap_a", "snap_b"),
        _change(OPENAI_GPT4O, "input_price_usd", "snap_a", "snap_c"),
    ]
    change_set = build_change_sets(changes)[0]
    assert change_set.previous_snapshot_ids == ["snap_a"]
    assert change_set.current_snapshot_ids == ["snap_b", "snap_c"]


def test_first_disclosure_with_no_previous_snapshot_is_not_added() -> None:
    change_set = build_change_sets([_change(OPENAI_GPT4O, "benchmark_scores", None, "snap_new")])[0]
    assert change_set.previous_snapshot_ids == []
    assert change_set.current_snapshot_ids == ["snap_new"]
