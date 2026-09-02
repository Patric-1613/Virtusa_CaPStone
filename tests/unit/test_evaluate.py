import uuid
from datetime import UTC, datetime

from ai_daily_digest.intelligence.evaluate import (
    EvalResult,
    change_recall,
    citation_validity,
    duplicate_rate,
    run_eval,
    unsupported_claim_count,
)
from ai_daily_digest.shared.schemas import (
    Change,
    Digest,
    DigestClaim,
    DocumentSnapshot,
    FactObservation,
    Subject,
)
from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver
from tests.uuid_samples import (
    CHANGE_1,
    CHANGE_SET_1,
    CLAIM_1,
    CLAIM_2,
    CLAIM_3,
    DIGEST_1,
    ITEM_1,
    SNAPSHOT_1,
    SNAPSHOT_2,
    SNAPSHOT_MISSING,
)

KNOWN = {SNAPSHOT_1, SNAPSHOT_2}


def _claim(text: str, citations: list[uuid.UUID], claim_id: uuid.UUID = CLAIM_1) -> DigestClaim:
    return DigestClaim(id=claim_id, text=text, citation_snapshot_ids=citations)


def _snapshot(snap_id: uuid.UUID, text: str) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id=ITEM_1,
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def _digest(claims: list[DigestClaim]) -> Digest:
    return Digest(
        id=DIGEST_1, digest_date="2026-08-20", status="draft", title="Test", claims=claims
    )


def _change(company: str, product: str, field: str, snap_id: uuid.UUID = SNAPSHOT_1) -> Change:
    return Change(
        id=CHANGE_1,
        change_set_id=CHANGE_SET_1,
        subject=Subject(company=company, product=product),
        field=field,
        change_type="changed",
        previous=None,
        current=FactObservation(value="x", snapshot_id=snap_id),
        confidence=0.9,
    )


# --- citation_validity ---


def test_citation_validity_all_supported() -> None:
    digest = _digest([_claim("A", [SNAPSHOT_1], CLAIM_1), _claim("B", [SNAPSHOT_2], CLAIM_2)])
    assert citation_validity(digest, KNOWN) == 1.0


def test_citation_validity_partial() -> None:
    digest = _digest([_claim("A", [SNAPSHOT_1], CLAIM_1), _claim("B", [SNAPSHOT_MISSING], CLAIM_2)])
    assert citation_validity(digest, KNOWN) == 0.5


def test_citation_validity_empty_digest_is_vacuously_perfect() -> None:
    assert citation_validity(_digest([]), KNOWN) == 1.0


def test_citation_validity_uses_real_content_grounding_when_available() -> None:
    """Per the second review: citation_validity() used to read 100% for
    a claim citing a real, existing snapshot id whose content had
    nothing to do with what the claim asserted -- it now reuses
    validate_claim() directly, so a citation that exists but doesn't
    ground the claim's numbers is NOT counted as valid, the same as the
    real publish-time gate would score it."""
    digest = _digest([_claim("The price increased to 999999.", [SNAPSHOT_1], CLAIM_1)])
    resolver = InMemorySnapshotResolver(
        {SNAPSHOT_1: _snapshot(SNAPSHOT_1, "The price increased to 5.")}
    )
    assert citation_validity(digest, KNOWN, snapshot_resolver=resolver) == 0.0
    assert unsupported_claim_count(digest, KNOWN, snapshot_resolver=resolver) == 1


def test_citation_validity_without_a_snapshot_resolver_stays_existence_only() -> None:
    """Omitting snapshot_resolver keeps the old, weaker existence-only
    behavior -- callers that don't have snapshot content aren't forced to
    provide one."""
    digest = _digest([_claim("The price increased to 999999.", [SNAPSHOT_1], CLAIM_1)])
    assert citation_validity(digest, KNOWN) == 1.0


# --- unsupported_claim_count ---


def test_unsupported_claim_count_counts_missing_and_empty_citations() -> None:
    digest = _digest(
        [
            _claim("A", [SNAPSHOT_1], CLAIM_1),
            _claim("B", [], CLAIM_2),
            _claim("C", [SNAPSHOT_MISSING], CLAIM_3),
        ]
    )
    assert unsupported_claim_count(digest, KNOWN) == 2


# --- duplicate_rate ---


def test_duplicate_rate_no_duplicates() -> None:
    digest = _digest(
        [_claim("Unique A", [SNAPSHOT_1], CLAIM_1), _claim("Unique B", [SNAPSHOT_1], CLAIM_2)]
    )
    assert duplicate_rate(digest) == 0.0


def test_duplicate_rate_detects_repeated_text_case_and_whitespace_insensitive() -> None:
    digest = _digest(
        [
            _claim("GPT-4o now has 256k context", [SNAPSHOT_1], CLAIM_1),
            _claim("  gpt-4o   now has 256k context  ", [SNAPSHOT_1], CLAIM_2),
            _claim("Something else entirely", [SNAPSHOT_1], CLAIM_3),
        ]
    )
    assert duplicate_rate(digest) == 1 / 3


def test_duplicate_rate_empty_digest_is_zero() -> None:
    assert duplicate_rate(_digest([])) == 0.0


# --- change_recall ---


def test_change_recall_full_when_all_expected_are_detected() -> None:
    expected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    detected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    assert change_recall(detected, expected) == 1.0


def test_change_recall_partial_when_some_missed() -> None:
    expected = [
        _change("OpenAI", "GPT-4o", "context_window_tokens"),
        _change("Anthropic", "Claude", "benchmark_scores"),
    ]
    detected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    assert change_recall(detected, expected) == 0.5


def test_change_recall_zero_when_nothing_detected() -> None:
    expected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    assert change_recall([], expected) == 0.0


def test_change_recall_vacuous_when_nothing_expected() -> None:
    assert change_recall([], []) == 1.0


# --- run_eval ---


def test_run_eval_combines_all_four_metrics() -> None:
    digest = _digest([_claim("A", [SNAPSHOT_1], CLAIM_1)])
    detected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    expected = [_change("OpenAI", "GPT-4o", "context_window_tokens")]
    result: EvalResult = run_eval(digest, detected, expected, KNOWN)
    assert result.citation_validity == 1.0
    assert result.unsupported_claims == 0
    assert result.duplicate_rate == 0.0
    assert result.change_recall == 1.0
    assert "100%" in result.as_table_row("test")
