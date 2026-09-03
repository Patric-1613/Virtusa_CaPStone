import uuid
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from ai_daily_digest.intelligence.facts import FactStore, change_snapshot_ids, normalise_name
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import (
    Change,
    DisclosureStatus,
    ExtractedFact,
    ExtractionMethod,
    FactObservation,
    Subject,
)
from tests.uuid_samples import CHANGE_1, CHANGE_SET_1, FACT_1

# The orchestrator's injected batch detection time (ADR 0008 section 5.A) --
# .250000 microseconds on purpose, so tests can assert it survives intact.
TF_DETECTED_AT = datetime(2026, 8, 20, 12, 0, 0, 250000, tzinfo=UTC)

TF_SNAP_1 = uuid.UUID("019e85a1-6358-7050-a64d-ce378b89d87c")
TF_SNAP_2 = uuid.UUID("01a01c77-c758-7680-a111-bfcfe074da96")
TF_SNAP_3 = uuid.UUID("01a05a44-1758-7d51-9edd-a9f4f1c293a4")
TF_SNAP_LAUNCH = uuid.UUID("019e85a1-6740-7b63-9b57-1151ba27045c")
TF_SNAP_256K = uuid.UUID("01a01c77-cb40-7a30-8ccb-3fc4507fce82")
TF_FACT_2 = uuid.UUID("01a01e66-0000-7000-8000-000000000010")
TF_FACT_3 = uuid.UUID("01a01e66-0000-7000-8000-000000000011")
TF_FACT_ND = uuid.UUID("01a01c77-cf28-7c33-bf3b-edd9efb430af")


def _factory(change_set_id: uuid.UUID = CHANGE_SET_1) -> Mock:
    """A change_set_id_factory test double -- most tests here don't care
    which change_set_id ends up on a produced Change, only that
    update_fact() calls the factory at the right time (see the
    call-count tests at the bottom of this file for the ones that do
    care)."""
    return Mock(return_value=change_set_id)


def _fact(
    field: str, value: str, snapshot_id: uuid.UUID, fact_id: uuid.UUID = FACT_1
) -> ExtractedFact:
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=value,
        extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
        extraction_model="claude-sonnet-5",
        prompt_version="fact-extraction-v1",
        quoted_span=f"quote containing {value}",
        confidence=0.9,
    )


def test_normalise_name_strips_case_and_punctuation() -> None:
    assert normalise_name("GPT-4o") == "gpt 4o"
    assert normalise_name("  Model   Alpha ") == "model alpha"


def test_first_observation_is_not_a_change() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1),
        source_url="https://openai.com/news/gpt-4o-launch",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is None
    current = store.get_current_fact(subject, "context_window_tokens")
    assert current is not None
    assert current.value == "128000"


def test_identical_value_is_a_silent_no_op() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_2, TF_FACT_2),
        source_url="https://techdesk.example.com/b",
        observed_at=datetime(2026, 6, 5, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is None
    assert store.field_history(subject, "context_window_tokens") == []
    # not a Change, but the citation should still point at the freshest
    # confirming snapshot, not the original one from June
    current = store.get_current_fact(subject, "context_window_tokens")
    assert current is not None
    assert current.snapshot_id == TF_SNAP_2


def test_change_type_auto_infers_increased_and_decreased() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    increased = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert increased is not None
    assert increased.change_type == "increased"

    decreased = store.update_fact(
        subject,
        _fact("context_window_tokens", "64000", TF_SNAP_3, TF_FACT_3),
        source_url=None,
        observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert decreased is not None
    assert decreased.change_type == "decreased"


def test_change_type_falls_back_to_changed_for_non_numeric_values() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("licence_terms", "MIT", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("licence_terms", "Apache-2.0", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    assert change.change_type == "changed"


def test_explicit_change_type_overrides_auto_inference() -> None:
    """Both sides here are real, disclosed values -- auto-inference would
    say "increased". "changed" is used as the override instead of the
    numerically-accurate one, specifically to prove update_fact() really
    used the caller-supplied value rather than the auto-inferred one
    (identical strings would prove nothing). Not "disclosed" -- Change's
    own invariant validator requires a "disclosed" Change's previous
    side to have value=None (a real not_disclosed -> disclosed
    transition), which this fixture's previous=128000 is not."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_type="changed",
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    assert change.change_type == "changed"


def test_changed_value_emits_a_change_with_correct_previous_and_current() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_LAUNCH, FACT_1),
        source_url="https://openai.com/news/gpt-4o-launch",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_256K, TF_FACT_2),
        source_url="https://openai.com/news/gpt-4o-256k-context",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_type="increased",
        confidence=0.98,
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )

    assert change is not None
    assert change.subject == subject
    assert change.field == "context_window_tokens"
    assert change.change_type == "increased"
    assert change.previous is not None
    assert change.previous.value == "128000"
    assert change.previous.snapshot_id == TF_SNAP_LAUNCH
    assert change.current.value == "256000"
    assert change.current.snapshot_id == TF_SNAP_256K
    assert change.confidence == 0.98

    # history preserves the old fact, untouched
    history = store.field_history(subject, "context_window_tokens")
    assert len(history) == 1
    assert history[0].value == "128000"

    # current state reflects the new value
    current = store.get_current_fact(subject, "context_window_tokens")
    assert current is not None
    assert current.value == "256000"


def test_different_fields_are_tracked_independently() -> None:
    store = FactStore()
    subject = Subject(company="Anthropic", product="Claude")
    store.update_fact(
        subject,
        _fact("benchmark_scores", "71.2", TF_SNAP_1, FACT_1),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    # context_window_tokens was never observed for this subject
    assert store.get_current_fact(subject, "context_window_tokens") is None
    current = store.get_current_fact(subject, "benchmark_scores")
    assert current is not None
    assert current.value == "71.2"


def test_reformatted_but_equivalent_value_is_a_silent_no_op() -> None:
    """Per review: update_fact() previously compared values with strict
    string equality, even though extract_facts.py's own acceptance check
    (value_supported_by_quote) already tolerates formatting differences
    like "$5" vs "5.00" -- causing a spurious Change for a value that
    never actually changed, just got reformatted between two
    extractions."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("input_price_usd", "5", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("input_price_usd", "$5.00", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is None
    assert store.field_history(subject, "input_price_usd") == []
    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.snapshot_id == TF_SNAP_2  # provenance still refreshes


def test_genuinely_different_numeric_value_still_registers_as_a_change() -> None:
    """Sanity check that the new formatting-tolerant comparison doesn't
    over-correct into treating real changes as no-ops."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("input_price_usd", "5", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("input_price_usd", "3", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    assert change.change_type == "decreased"


def _change(
    previous_snap: uuid.UUID | None, current_snap: uuid.UUID | None, change_type: str = "changed"
) -> Change:
    return Change(
        id=CHANGE_1,
        change_set_id=CHANGE_SET_1,
        subject=Subject(company="OpenAI", product="GPT-4o"),
        field="context_window_tokens",
        change_type=change_type,
        previous=(
            FactObservation(value="old", snapshot_id=previous_snap) if previous_snap else None
        ),
        current=FactObservation(value="new", snapshot_id=current_snap),
        confidence=0.9,
        detected_at=TF_DETECTED_AT,
    )


def test_change_snapshot_ids_with_both_present() -> None:
    assert change_snapshot_ids(_change(TF_SNAP_1, TF_SNAP_2)) == (TF_SNAP_2, TF_SNAP_1)


def test_change_snapshot_ids_with_no_previous() -> None:
    # "disclosed" -- the only change_type Change's own invariant
    # validator allows a genuinely absent (not just empty) previous for.
    assert change_snapshot_ids(_change(None, TF_SNAP_2, change_type="disclosed")) == (
        TF_SNAP_2,
        None,
    )


def test_malformed_snapshot_id_is_rejected_at_construction_not_silently_treated_as_absent() -> None:
    """Superseded behavior, ADR 0007: FactObservation.snapshot_id used to
    accept an empty string and treat it the same as absent (None), and
    change_snapshot_ids() used to defensively re-normalise an empty
    string back to None on top of that. Uuid7Id validation now makes an
    empty-string snapshot_id unconstructible in the first place -- a
    malformed value is rejected outright, not silently normalised -- so
    change_snapshot_ids() no longer needs (or has) that defensive
    fallback; see its own docstring in facts.py."""
    with pytest.raises(ValidationError):
        FactObservation(value="old", snapshot_id="")  # type: ignore[arg-type]


def _not_disclosed_fact(
    field: str, snapshot_id: uuid.UUID, fact_id: uuid.UUID = TF_FACT_ND
) -> ExtractedFact:
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=None,
        disclosure_status=DisclosureStatus.NOT_DISCLOSED,
        extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
        extraction_model="claude-sonnet-5",
        prompt_version="fact-extraction-v1",
        quoted_span="pricing has not yet been announced",
        confidence=0.9,
    )


# --- ADR 0006: disclosure-status transitions. A first observation of a
# not_disclosed fact, or two not_disclosed observations in a row, are
# recorded but not a Change (same treatment a first observation/unchanged
# value already gets). A genuine flip across the disclosure boundary
# (disclosed -> not_disclosed or the reverse) IS a real, reportable
# Change (change_type "disclosed"/"not_disclosed") -- see update_fact()'s
# own docstring. ---


def test_first_observation_of_a_not_disclosed_fact_is_not_a_change() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    change = store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_1),
        source_url="https://openai.com/pricing",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is None
    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.value is None
    assert current.disclosure_status == "not_disclosed"


def test_disclosed_to_not_disclosed_transition_emits_change() -> None:
    """A real disclosure-status flip -- previously a real value, now
    explicitly withheld. FactStore records the new state and emits a Change
    with change_type='not_disclosed' citing both snapshots."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("input_price_usd", "5", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    assert change.subject == subject
    assert change.field == "input_price_usd"
    assert change.change_type == "not_disclosed"
    assert change.previous is not None
    assert change.previous.value == "5"
    assert change.previous.snapshot_id == TF_SNAP_1
    assert change.current.value is None
    assert change.current.snapshot_id == TF_SNAP_2

    history = store.field_history(subject, "input_price_usd")
    assert len(history) == 1
    assert history[0].value == "5"

    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.value is None
    assert current.disclosure_status == "not_disclosed"
    assert current.snapshot_id == TF_SNAP_2


def test_not_disclosed_to_disclosed_transition_emits_change() -> None:
    """The reverse flip -- a value now disclosed for the first time after
    an explicit non-disclosure. Emits a Change with change_type='disclosed'."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("input_price_usd", "5", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    assert change.subject == subject
    assert change.field == "input_price_usd"
    assert change.change_type == "disclosed"
    assert change.previous is not None
    assert change.previous.value is None
    assert change.previous.snapshot_id == TF_SNAP_1
    assert change.current.value == "5"
    assert change.current.snapshot_id == TF_SNAP_2

    history = store.field_history(subject, "input_price_usd")
    assert len(history) == 1
    assert history[0].value is None
    assert history[0].disclosure_status == "not_disclosed"

    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.value == "5"
    assert current.disclosure_status == "disclosed"
    assert current.snapshot_id == TF_SNAP_2


def test_repeated_not_disclosed_observation_is_a_silent_no_op_but_refreshes_provenance() -> None:
    """Two not_disclosed observations in a row for the same field are
    equivalent (nothing actually changed) -- provenance still refreshes
    to the newer confirming snapshot, the same as a repeated disclosed
    value already does (test_identical_value_is_a_silent_no_op)."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert change is None
    assert store.field_history(subject, "input_price_usd") == []
    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.snapshot_id == TF_SNAP_2


def test_known_subjects_accumulates_across_updates() -> None:
    store = FactStore()
    a = Subject(company="OpenAI", product="GPT-4o")
    b = Subject(company="Anthropic", product="Claude")
    store.update_fact(
        a,
        _fact("context_window_tokens", "128000", TF_SNAP_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    store.update_fact(
        b,
        _fact("benchmark_scores", "71.2", TF_SNAP_2),
        source_url=None,
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    assert set(store.known_subjects()) == {a, b}


# --- ADR 0007: change_set_id_factory is lazy -- called exactly once,
# only immediately before a real Change is constructed. ---


def test_factory_is_not_called_for_a_first_observation() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    factory = _factory()
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    factory.assert_not_called()


def test_factory_is_not_called_for_an_unchanged_value() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    factory = _factory()
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    factory.assert_not_called()


def test_factory_is_called_exactly_once_for_a_disclosure_status_transition() -> None:
    """A disclosure-status transition IS a real Change (see the ADR 0006
    section above), so -- unlike a first observation or an unchanged
    value -- it DOES consume a change_set_id."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("input_price_usd", "5", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    factory = _factory()
    change = store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    factory.assert_called_once()


def test_factory_is_not_called_for_a_first_observation_of_a_not_disclosed_fact() -> None:
    """A first-ever observation is never a Change, disclosed or not."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    factory = _factory()
    store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    factory.assert_not_called()


def test_factory_is_not_called_for_a_repeated_not_disclosed_observation() -> None:
    """Two not_disclosed observations in a row are equivalent -- no
    Change, so no change_set_id is spent."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    factory = _factory()
    store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    factory.assert_not_called()


def test_same_subject_transitions_share_one_change_set_id_per_batch() -> None:
    """Two Changes for the same subject in the same batch (i.e. sharing
    one caller-owned change_set_id_factory closure) must reuse the same
    change_set_id -- the batch-scoped get-or-create allocator this
    factory represents (change_sets.py::get_or_create_change_set_id),
    not a fresh id per Change."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    change_set_ids: dict[Subject, uuid.UUID] = {}

    def factory() -> uuid.UUID:
        existing = change_set_ids.get(subject)
        if existing is not None:
            return existing
        allocated = CHANGE_SET_1
        change_set_ids[subject] = allocated
        return allocated

    store.update_fact(
        subject,
        _fact("input_price_usd", "5", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    # Baseline for a second field, so the field's own second update below
    # is a real change (not a first observation) too.
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    first_change = store.update_fact(
        subject,
        _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    second_change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_3, TF_FACT_3),
        source_url=None,
        observed_at=datetime(2026, 8, 21, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    assert first_change is not None
    assert second_change is not None
    assert first_change.change_set_id == second_change.change_set_id == CHANGE_SET_1


def test_different_subjects_get_different_change_set_ids() -> None:
    """The reverse: two subjects sharing one batch's allocator dict must
    each get their own change_set_id, not collide on one."""
    store = FactStore()
    subject_a = Subject(company="OpenAI", product="GPT-4o")
    subject_b = Subject(company="Anthropic", product="Claude")
    change_set_ids: dict[Subject, uuid.UUID] = {}

    def factory_for(subject: Subject) -> uuid.UUID:
        existing = change_set_ids.get(subject)
        if existing is not None:
            return existing
        allocated = new_id()  # a real UUID v7, mirroring the production allocator
        change_set_ids[subject] = allocated
        return allocated

    for subject in (subject_a, subject_b):
        store.update_fact(
            subject,
            _fact("input_price_usd", "5", TF_SNAP_1, FACT_1),
            source_url=None,
            observed_at=datetime(2026, 6, 2, tzinfo=UTC),
            change_set_id_factory=_factory(),
            detected_at=TF_DETECTED_AT,
        )
    change_a = store.update_fact(
        subject_a,
        _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=lambda: factory_for(subject_a),
        detected_at=TF_DETECTED_AT,
    )
    change_b = store.update_fact(
        subject_b,
        _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=lambda: factory_for(subject_b),
        detected_at=TF_DETECTED_AT,
    )
    assert change_a is not None
    assert change_b is not None
    assert change_a.change_set_id != change_b.change_set_id


def test_factory_is_called_exactly_once_for_a_real_change() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    factory = _factory()
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=factory,
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    factory.assert_called_once()


def test_no_placeholder_or_temporary_change_set_id_ever_reaches_a_change() -> None:
    """A real Change's change_set_id is always exactly what the factory
    returned -- never an empty string, sentinel, or other temporary
    value at any point."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_set_id_factory=_factory(CHANGE_SET_1),
        detected_at=TF_DETECTED_AT,
    )
    assert change is not None
    assert change.change_set_id == CHANGE_SET_1


# --- ADR 0007's failed-processing rule: if constructing the real Change
# fails validation, update_fact() must spend no id (new_id() and
# change_set_id_factory() both un-called) and must not half-write the
# store -- record.current stays the previous valid fact and history is
# not partially appended. Regression: the earlier implementation
# evaluated new_id()/change_set_id_factory() as Change(...) arguments and
# mutated record.current/history *before* the Change's own validation
# ran, so an invalid current observation left call_count==1 and the
# failed value already "current". ---


def _seed_first_value(store: FactStore, subject: Subject) -> None:
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url="https://openai.com/news/gpt-4o-launch",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )


def test_real_change_with_invalid_current_source_url_spends_no_id_and_preserves_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    _seed_first_value(store, subject)

    new_id_spy = Mock()
    monkeypatch.setattr("ai_daily_digest.intelligence.facts.new_id", new_id_spy)
    factory = _factory()

    with pytest.raises(ValidationError) as exc_info:
        store.update_fact(
            subject,
            _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
            source_url="not a valid url",
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
            change_set_id_factory=factory,
            detected_at=TF_DETECTED_AT,
        )
    assert exc_info.value.errors()[0]["type"] == "url_parsing"

    factory.assert_not_called()
    new_id_spy.assert_not_called()

    current = store.get_current_fact(subject, "context_window_tokens")
    assert current is not None
    assert current.value == "128000"  # previous valid value is still current
    assert current.snapshot_id == TF_SNAP_1
    assert store.field_history(subject, "context_window_tokens") == []  # no partial append


@pytest.mark.parametrize(
    "bad_confidence",
    [float("nan"), float("inf"), 1.5, -0.1],
    ids=["nan", "inf", "above_one", "below_zero"],
)
def test_real_change_with_invalid_confidence_spends_no_id_and_leaves_store_unchanged(
    bad_confidence: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    _seed_first_value(store, subject)

    new_id_spy = Mock()
    monkeypatch.setattr("ai_daily_digest.intelligence.facts.new_id", new_id_spy)
    factory = _factory()

    with pytest.raises(ValidationError):
        store.update_fact(
            subject,
            _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
            source_url="https://openai.com/news/gpt-4o-256k-context",
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
            change_set_id_factory=factory,
            detected_at=TF_DETECTED_AT,
            confidence=bad_confidence,
        )

    factory.assert_not_called()  # validation fails before id allocation
    new_id_spy.assert_not_called()

    current = store.get_current_fact(subject, "context_window_tokens")
    assert current is not None
    assert current.value == "128000"
    assert current.snapshot_id == TF_SNAP_1
    assert store.field_history(subject, "context_window_tokens") == []


# --- An explicit change_type override is the one input update_fact()
# itself can make internally inconsistent with the two observations it
# builds (_infer_change_type's own output always matches by
# construction) -- validate_change_shape() must catch that BEFORE
# new_id()/change_set_id_factory() run, the same way a bad confidence or
# source_url already does above. ---


def _seed_price_value(store: FactStore, subject: Subject) -> None:
    store.update_fact(
        subject,
        _fact("input_price_usd", "5", TF_SNAP_1, FACT_1),
        source_url="https://openai.com/news/pricing",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=TF_DETECTED_AT,
    )


def test_inconsistent_change_type_override_to_not_a_disclosure_shape_spends_no_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real value -> None (a genuine not_disclosed transition) but with
    an inconsistent explicit override of change_type="changed": "changed"
    requires a real value on both sides, so this must be rejected before
    any id is spent, leaving the store exactly as update_fact() found it."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    _seed_price_value(store, subject)

    new_id_spy = Mock()
    monkeypatch.setattr("ai_daily_digest.intelligence.facts.new_id", new_id_spy)
    factory = _factory()

    with pytest.raises(ValueError, match="requires current observation with non-null value"):
        store.update_fact(
            subject,
            _not_disclosed_fact("input_price_usd", TF_SNAP_2, TF_FACT_2),
            source_url=None,
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
            change_type="changed",
            change_set_id_factory=factory,
            detected_at=TF_DETECTED_AT,
        )

    factory.assert_not_called()
    new_id_spy.assert_not_called()

    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.value == "5"
    assert current.snapshot_id == TF_SNAP_1
    assert store.field_history(subject, "input_price_usd") == []


def test_inconsistent_change_type_override_to_a_disclosure_shape_spends_no_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real value -> a different real value (a genuine numeric change)
    but with an inconsistent explicit override of change_type="disclosed":
    "disclosed" requires previous.value=None when previous is present, so
    this must be rejected before any id is spent, leaving the store
    exactly as update_fact() found it."""
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    _seed_price_value(store, subject)

    new_id_spy = Mock()
    monkeypatch.setattr("ai_daily_digest.intelligence.facts.new_id", new_id_spy)
    factory = _factory()

    with pytest.raises(ValueError, match=r"requires previous\.value=None"):
        store.update_fact(
            subject,
            _fact("input_price_usd", "10", TF_SNAP_2, TF_FACT_2),
            source_url=None,
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
            change_type="disclosed",
            change_set_id_factory=factory,
            detected_at=TF_DETECTED_AT,
        )

    factory.assert_not_called()
    new_id_spy.assert_not_called()

    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.value == "5"
    assert current.snapshot_id == TF_SNAP_1
    assert store.field_history(subject, "input_price_usd") == []


# ---------------------------------------------------------------------------
# ADR 0008 section 5.A: FactStore.update_fact() stamps every emitted Change
# with the caller-injected detection time exactly, never the wall clock, and
# validates it before spending a UUID or mutating anything.
# ---------------------------------------------------------------------------


def test_emitted_change_carries_the_injected_detected_at_exactly() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    injected = datetime(2026, 8, 20, 12, 0, 0, 250000, tzinfo=UTC)
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=injected,
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, 9, 5, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=injected,
    )
    assert change is not None
    assert change.detected_at == injected
    assert change.detected_at.microsecond == 250000
    # detected_at is not conflated with either observation's observed_at
    assert change.current.observed_at == datetime(2026, 8, 20, 9, 5, tzinfo=UTC)
    assert change.previous is not None
    assert change.previous.observed_at == datetime(2026, 6, 2, tzinfo=UTC)


def test_non_utc_injected_detected_at_is_normalized_on_the_change() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    ist = timezone(timedelta(hours=5, minutes=30))
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", TF_SNAP_1, FACT_1),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", TF_SNAP_2, TF_FACT_2),
        source_url=None,
        observed_at=datetime(2026, 8, 20, 9, 5, tzinfo=UTC),
        change_set_id_factory=_factory(),
        detected_at=datetime(2026, 8, 20, 17, 30, 0, 123456, tzinfo=ist),
    )
    assert change is not None
    assert change.detected_at.tzinfo == UTC
    assert change.detected_at == datetime(2026, 8, 20, 12, 0, 0, 123456, tzinfo=UTC)
    assert change.detected_at.microsecond == 123456


def test_naive_injected_detected_at_spends_no_id_and_leaves_store_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    _seed_price_value(store, subject)

    new_id_spy = Mock()
    monkeypatch.setattr("ai_daily_digest.intelligence.facts.new_id", new_id_spy)
    factory = _factory()

    with pytest.raises(ValidationError):
        store.update_fact(
            subject,
            _fact("input_price_usd", "10", TF_SNAP_2, TF_FACT_2),
            source_url=None,
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
            change_set_id_factory=factory,
            detected_at=datetime(2026, 8, 20, 12, 0, 0),
        )

    factory.assert_not_called()
    new_id_spy.assert_not_called()
    current = store.get_current_fact(subject, "input_price_usd")
    assert current is not None
    assert current.value == "5"
    assert current.snapshot_id == TF_SNAP_1
    assert store.field_history(subject, "input_price_usd") == []
