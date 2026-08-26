from datetime import UTC, datetime

from ai_daily_digest.intelligence.facts import FactStore, normalise_name
from ai_daily_digest.shared.schemas import ExtractedFact, Subject


def _fact(field: str, value: str, snapshot_id: str, fact_id: str = "fact_1") -> ExtractedFact:
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=value,
        extraction_method="llm_structured_output",
        extraction_model="claude-sonnet-5",
        prompt_version="fact-extraction-v1",
    )


def test_normalise_name_strips_case_and_punctuation() -> None:
    assert normalise_name("GPT-4o") == "gpt 4o"
    assert normalise_name("  Model   Alpha ") == "model alpha"


def test_first_observation_is_not_a_change() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", "snap_1"),
        source_url="https://openai.com/news/gpt-4o-launch",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
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
        _fact("context_window_tokens", "128000", "snap_1", "fact_1"),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", "snap_2", "fact_2"),
        source_url="https://techdesk.example.com/b",
        observed_at=datetime(2026, 6, 5, tzinfo=UTC),
    )
    assert change is None
    assert store.field_history(subject, "context_window_tokens") == []
    # not a Change, but the citation should still point at the freshest
    # confirming snapshot, not the original one from June
    current = store.get_current_fact(subject, "context_window_tokens")
    assert current is not None
    assert current.snapshot_id == "snap_2"


def test_change_type_auto_infers_increased_and_decreased() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", "snap_1", "fact_1"),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    increased = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", "snap_2", "fact_2"),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert increased is not None
    assert increased.change_type == "increased"

    decreased = store.update_fact(
        subject,
        _fact("context_window_tokens", "64000", "snap_3", "fact_3"),
        source_url=None,
        observed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert decreased is not None
    assert decreased.change_type == "decreased"


def test_change_type_falls_back_to_changed_for_non_numeric_values() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("licence_terms", "MIT", "snap_1", "fact_1"),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    change = store.update_fact(
        subject,
        _fact("licence_terms", "Apache-2.0", "snap_2", "fact_2"),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert change is not None
    assert change.change_type == "changed"


def test_explicit_change_type_overrides_auto_inference() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", "snap_1", "fact_1"),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", "snap_2", "fact_2"),
        source_url=None,
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_type="disclosed",
    )
    assert change is not None
    assert change.change_type == "disclosed"


def test_changed_value_emits_a_change_with_correct_previous_and_current() -> None:
    store = FactStore()
    subject = Subject(company="OpenAI", product="GPT-4o")
    store.update_fact(
        subject,
        _fact("context_window_tokens", "128000", "snap_launch", "fact_1"),
        source_url="https://openai.com/news/gpt-4o-launch",
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    change = store.update_fact(
        subject,
        _fact("context_window_tokens", "256000", "snap_256k", "fact_2"),
        source_url="https://openai.com/news/gpt-4o-256k-context",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        change_type="increased",
        confidence=0.98,
    )

    assert change is not None
    assert change.subject == subject
    assert change.field == "context_window_tokens"
    assert change.change_type == "increased"
    assert change.previous is not None
    assert change.previous.value == "128000"
    assert change.previous.snapshot_id == "snap_launch"
    assert change.current.value == "256000"
    assert change.current.snapshot_id == "snap_256k"
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
        _fact("benchmark_scores", "71.2", "snap_1", "fact_1"),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    # context_window_tokens was never observed for this subject
    assert store.get_current_fact(subject, "context_window_tokens") is None
    current = store.get_current_fact(subject, "benchmark_scores")
    assert current is not None
    assert current.value == "71.2"


def test_known_subjects_accumulates_across_updates() -> None:
    store = FactStore()
    a = Subject(company="OpenAI", product="GPT-4o")
    b = Subject(company="Anthropic", product="Claude")
    store.update_fact(
        a,
        _fact("context_window_tokens", "128000", "s1"),
        source_url=None,
        observed_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    store.update_fact(
        b,
        _fact("benchmark_scores", "71.2", "s2"),
        source_url=None,
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert set(store.known_subjects()) == {a, b}
