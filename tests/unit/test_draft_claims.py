from datetime import UTC, datetime

from ai_daily_digest.intelligence.draft_claims import draft_change_claim
from ai_daily_digest.shared.attributes import COMPARABLE_FIELDS
from ai_daily_digest.shared.schemas import Change, FactObservation, Subject


def _subject() -> Subject:
    return Subject(company="OpenAI", product="GPT-4o")


def test_first_disclosure_phrasing_and_single_citation() -> None:
    change = Change(
        id="c1",
        change_set_id="cs1",
        subject=_subject(),
        field="benchmark_scores",
        change_type="disclosed",
        previous=None,
        current=FactObservation(
            value="71.2",
            observed_at=datetime(2026, 8, 19, tzinfo=UTC),
            snapshot_id="snap_current",
            source_url="https://openai.com/a",  # type: ignore[arg-type]
        ),
        confidence=0.9,
    )
    claim = draft_change_claim(change)
    assert "now disclosed as 71.2" in claim.text
    assert claim.citation_snapshot_ids == ["snap_current"]
    assert claim.validation_status == "pending"


def test_increased_phrasing_cites_both_snapshots() -> None:
    change = Change(
        id="c2",
        change_set_id="cs1",
        subject=_subject(),
        field="context_window_tokens",
        change_type="increased",
        previous=FactObservation(
            value="128000",
            observed_at=datetime(2026, 6, 2, tzinfo=UTC),
            snapshot_id="snap_prev",
            source_url="https://openai.com/launch",  # type: ignore[arg-type]
        ),
        current=FactObservation(
            value="256000",
            observed_at=datetime(2026, 8, 20, tzinfo=UTC),
            snapshot_id="snap_current",
            source_url="https://openai.com/a",  # type: ignore[arg-type]
        ),
        confidence=0.98,
    )
    claim = draft_change_claim(change)
    assert "increased to 256000" in claim.text
    assert "up from 128000" in claim.text
    assert set(claim.citation_snapshot_ids) == {"snap_current", "snap_prev"}


def test_decreased_phrasing() -> None:
    change = Change(
        id="c3",
        change_set_id="cs1",
        subject=_subject(),
        field="input_price_usd",
        change_type="decreased",
        previous=FactObservation(value="10", snapshot_id="snap_prev"),
        current=FactObservation(value="5", snapshot_id="snap_current"),
        confidence=0.9,
    )
    claim = draft_change_claim(change)
    assert "decreased to 5" in claim.text
    assert "down from 10" in claim.text


def test_generic_change_type_falls_back_to_neutral_phrasing() -> None:
    change = Change(
        id="c4",
        change_set_id="cs1",
        subject=_subject(),
        field="licence_terms",
        change_type="changed",
        previous=FactObservation(value="MIT", snapshot_id="snap_prev"),
        current=FactObservation(value="Apache-2.0", snapshot_id="snap_current"),
        confidence=0.8,
    )
    claim = draft_change_claim(change)
    assert "changed from MIT to Apache-2.0" in claim.text


def test_field_label_matches_the_same_curated_label_compare_subjects_uses() -> None:
    """A field must read identically whether it appears in a drafted
    change claim or a compare_subjects comparison claim -- both need to
    come from the same COMPARABLE_FIELDS source, not two different
    label-generation strategies."""
    change = Change(
        id="c5",
        change_set_id="cs1",
        subject=_subject(),
        field="context_window_tokens",
        change_type="increased",
        previous=FactObservation(value="128000", snapshot_id="snap_prev"),
        current=FactObservation(value="256000", snapshot_id="snap_current"),
        confidence=0.9,
    )
    claim = draft_change_claim(change)
    expected_label = COMPARABLE_FIELDS["context_window_tokens"].lower()
    assert expected_label in claim.text
    assert "context window tokens" not in claim.text  # the old raw-field-key wording
