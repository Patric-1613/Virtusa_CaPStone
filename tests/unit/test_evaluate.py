from ai_daily_digest.intelligence.evaluate import (
    change_recall,
    citation_validity,
    duplicate_rate,
    run_eval,
    unsupported_claim_count,
)
from ai_daily_digest.shared.schemas import Change, Digest, DigestClaim, FactObservation, Subject

KNOWN = {"snap_1", "snap_2"}


def _claim(text, citations, claim_id="c1"):
    return DigestClaim(id=claim_id, text=text, citation_snapshot_ids=citations)


def _digest(claims):
    return Digest(id="d1", digest_date="2026-08-20", status="draft", title="Test", claims=claims)


def _change(company, product, field, snap_id="snap_1"):
    return Change(
        id=f"c-{company}-{product}-{field}",
        change_set_id="cs1",
        subject=Subject(company=company, product=product),
        field=field,
        change_type="changed",
        previous=None,
        current=FactObservation(value="x", snapshot_id=snap_id),
        confidence=0.9,
    )


# --- citation_validity ---


def test_citation_validity_all_supported():
    digest = _digest([_claim("A", ["snap_1"], "c1"), _claim("B", ["snap_2"], "c2")])
    assert citation_validity(digest, KNOWN) == 1.0


def test_citation_validity_partial():
    digest = _digest([_claim("A", ["snap_1"], "c1"), _claim("B", ["snap_missing"], "c2")])
    assert citation_validity(digest, KNOWN) == 0.5


def test_citation_validity_empty_digest_is_vacuously_perfect():
    assert citation_validity(_digest([]), KNOWN) == 1.0


# --- unsupported_claim_count ---


def test_unsupported_claim_count_counts_missing_and_empty_citations():
    digest = _digest(
        [
            _claim("A", ["snap_1"], "c1"),
            _claim("B", [], "c2"),
            _claim("C", ["snap_missing"], "c3"),
        ]
    )
    assert unsupported_claim_count(digest, KNOWN) == 2


# --- duplicate_rate ---


def test_duplicate_rate_no_duplicates():
    digest = _digest([_claim("Unique A", ["snap_1"], "c1"), _claim("Unique B", ["snap_1"], "c2")])
    assert duplicate_rate(digest) == 0.0


def test_duplicate_rate_detects_repeated_text_case_and_whitespace_insensitive():
    digest = _digest(
        [
            _claim("GPT-4o now has 256k context", ["snap_1"], "c1"),
            _claim("  gpt-4o   now has 256k context  ", ["snap_1"], "c2"),
            _claim("Something else entirely", ["snap_1"], "c3"),
        ]
    )
    assert duplicate_rate(digest) == 1 / 3


def test_duplicate_rate_empty_digest_is_zero():
    assert duplicate_rate(_digest([])) == 0.0


# --- change_recall ---


def test_change_recall_full_when_all_expected_are_detected():
    expected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    detected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    assert change_recall(detected, expected) == 1.0


def test_change_recall_partial_when_some_missed():
    expected = [
        _change("OpenAI", "GPT-4o", "context_window_tokens"),
        _change("Anthropic", "Claude", "benchmark_scores"),
    ]
    detected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    assert change_recall(detected, expected) == 0.5


def test_change_recall_zero_when_nothing_detected():
    expected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    assert change_recall([], expected) == 0.0


def test_change_recall_vacuous_when_nothing_expected():
    assert change_recall([], []) == 1.0


# --- run_eval ---


def test_run_eval_combines_all_four_metrics():
    digest = _digest([_claim("A", ["snap_1"], "c1")])
    detected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    expected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    result = run_eval(digest, detected, expected, KNOWN)
    assert result.citation_validity == 1.0
    assert result.unsupported_claims == 0
    assert result.duplicate_rate == 0.0
    assert result.change_recall == 1.0
    assert "100%" in result.as_table_row("test")
