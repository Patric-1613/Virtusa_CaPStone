"""Tests the code-level guardrails with an injected fake call_fn -- no
network/API key needed. This is the adversarial suite: every test that
matters here is "the model tried something ungrounded and the code
caught it", not just "the happy path works".

ADR 0005: the model proposes only structured ComparisonAssertion
(subject_a, subject_b, field) triples now -- it never writes a value, a
relation, or any prose. compare_subjects() alone looks up real values and
renders the claim text. This is what makes the swapped-value fabrication
class this suite used to just document as a known gap (see git history
of this file, pre-ADR-0005) structurally impossible instead."""

import logging
from datetime import UTC, datetime

import pytest

from ai_daily_digest.intelligence.compare_subjects import (
    ComparisonAssertion,
    ComparisonResponse,
    FactRow,
    build_fact_table,
    compare_subjects,
)
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.shared.schemas import ExtractedFact, Subject

OPENAI_GPT4O = Subject(company="OpenAI", product="GPT-4o")
ANTHROPIC_CLAUDE = Subject(company="Anthropic", product="Claude")
GOOGLE_GEMINI = Subject(company="Google", product="Gemini")


def _fact(field: str, value: str, snapshot_id: str, fact_id: str = "f1") -> ExtractedFact:
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=value,
        extraction_method="llm_structured_output",
        extraction_model="claude-sonnet-5",
        prompt_version="v1",
        quoted_span=f"quote containing {value}",
        confidence=0.9,
    )


def _store_with_data() -> FactStore:
    store = FactStore()
    store.update_fact(
        OPENAI_GPT4O,
        _fact("context_window_tokens", "256000", "snap_openai_ctx"),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    store.update_fact(
        ANTHROPIC_CLAUDE,
        _fact("context_window_tokens", "128000", "snap_anthropic_ctx"),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    store.update_fact(
        ANTHROPIC_CLAUDE,
        _fact("benchmark_scores", "71.2", "snap_anthropic_bench", "f2"),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    return store


def _rows() -> list[FactRow]:
    store = _store_with_data()
    return build_fact_table(
        store,
        [OPENAI_GPT4O, ANTHROPIC_CLAUDE],
        ["context_window_tokens", "benchmark_scores"],
    )


def _one_assertion_response(
    subject_a: Subject, subject_b: Subject, field: str
) -> ComparisonResponse:
    return ComparisonResponse(
        assertions=[ComparisonAssertion(subject_a=subject_a, subject_b=subject_b, field=field)]
    )


def test_build_fact_table_marks_missing_fields_not_disclosed() -> None:
    rows = _rows()
    openai_bench = next(
        r for r in rows if r.subject == OPENAI_GPT4O and r.field == "benchmark_scores"
    )
    assert openai_bench.value is None
    assert openai_bench.snapshot_id is None

    openai_ctx = next(
        r for r in rows if r.subject == OPENAI_GPT4O and r.field == "context_window_tokens"
    )
    assert openai_ctx.value == "256000"
    assert openai_ctx.snapshot_id == "snap_openai_ctx"


def test_well_grounded_comparison_is_accepted_and_deterministically_rendered() -> None:
    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(OPENAI_GPT4O, ANTHROPIC_CLAUDE, "context_window_tokens")

    claims = compare_subjects(_rows(), call_fn=fake_call)
    assert len(claims) == 1
    assert set(claims[0].citation_snapshot_ids) == {"snap_openai_ctx", "snap_anthropic_ctx"}
    assert claims[0].validation_status == "pending"
    # Code renders the text from the real values -- OpenAI's 256000 is
    # actually higher than Anthropic's 128000.
    assert "256000" in claims[0].text
    assert "128000" in claims[0].text
    assert "higher" in claims[0].text


def test_relation_lower_is_rendered_for_the_smaller_side() -> None:
    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(ANTHROPIC_CLAUDE, OPENAI_GPT4O, "context_window_tokens")

    claims = compare_subjects(_rows(), call_fn=fake_call)
    assert len(claims) == 1
    assert "lower" in claims[0].text


def test_equal_values_are_rendered_as_equal_not_higher_or_lower() -> None:
    store = FactStore()
    store.update_fact(
        OPENAI_GPT4O,
        _fact("context_window_tokens", "128000", "snap_openai_ctx"),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    store.update_fact(
        ANTHROPIC_CLAUDE,
        _fact("context_window_tokens", "128000", "snap_anthropic_ctx", "f2"),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    rows = build_fact_table(store, [OPENAI_GPT4O, ANTHROPIC_CLAUDE], ["context_window_tokens"])

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(OPENAI_GPT4O, ANTHROPIC_CLAUDE, "context_window_tokens")

    claims = compare_subjects(rows, call_fn=fake_call)
    assert len(claims) == 1
    assert "same" in claims[0].text
    assert "higher" not in claims[0].text
    assert "lower" not in claims[0].text


def test_swapped_attribution_is_now_structurally_impossible() -> None:
    """This is the exact class ADR 0005 closes: pre-ADR-0005, a model
    could write "OpenAI's price is 3; Anthropic's price is 5" when the
    real values were reversed, and the old numeric-only check couldn't
    catch it (both numbers were real, just attributed backwards -- see
    this file's git history). Now the model can't write a value or an
    attribution at all -- it only names which two subjects/field to
    compare -- so the rendered text is always correct by construction."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        # The model can still name the pair in whichever order it likes
        # -- it just can't say which number belongs to which subject.
        return _one_assertion_response(ANTHROPIC_CLAUDE, OPENAI_GPT4O, "context_window_tokens")

    claims = compare_subjects(_rows(), call_fn=fake_call)
    assert len(claims) == 1
    # Anthropic (128000) really is lower than OpenAI (256000) -- code
    # decided that, the model never got a chance to say it backwards.
    assert claims[0].text == (
        "Anthropic's Claude has a lower context window (128000) than OpenAI's GPT-4o (256000)."
    )


def test_field_with_no_registered_comparison_rule_is_rejected() -> None:
    """benchmark_scores is a real COMPARABLE_FIELDS entry and is present
    in the table, but Phase 1 (ADR 0005 point 2) registers no
    ComparisonRule for it -- excluded from comparison entirely, not
    guessed at."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(OPENAI_GPT4O, ANTHROPIC_CLAUDE, "benchmark_scores")

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_unknown_field_is_rejected() -> None:
    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(OPENAI_GPT4O, ANTHROPIC_CLAUDE, "made_up_field")

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_unknown_subject_is_rejected() -> None:
    intruder = Subject(company="MadeUp Inc", product="Ghost Model")

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(OPENAI_GPT4O, intruder, "context_window_tokens")

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_subject_compared_to_itself_is_rejected() -> None:
    """Per the third review: reject comparisons where subject_a ==
    subject_b -- a subject can't be legitimately compared to itself."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(OPENAI_GPT4O, OPENAI_GPT4O, "context_window_tokens")

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_value_not_disclosed_on_either_side_is_rejected() -> None:
    """The field DOES have a registered ComparisonRule (unlike the
    previous test) but one side's row is genuinely undisclosed -- there
    is no real value to look up, so the assertion is rejected."""
    store = FactStore()
    store.update_fact(
        OPENAI_GPT4O,
        _fact("context_window_tokens", "256000", "snap_openai_ctx"),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    # Anthropic's context_window_tokens is never recorded.
    rows = build_fact_table(store, [OPENAI_GPT4O, ANTHROPIC_CLAUDE], ["context_window_tokens"])

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return _one_assertion_response(OPENAI_GPT4O, ANTHROPIC_CLAUDE, "context_window_tokens")

    assert compare_subjects(rows, call_fn=fake_call) == []


def test_malformed_stored_value_drops_only_that_candidate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADR 0005 point 2: a stored value the field's ComparisonRule can't
    parse must fail per-candidate (dropped, logged), never abort the rest
    of the comparison pass. Proven here with two assertions in one
    response: the malformed one (OpenAI vs. Anthropic) is dropped, and a
    second, otherwise-unrelated, well-formed assertion between two OTHER
    valid subjects (Anthropic vs. Google) still resolves to a real
    DigestClaim -- not just "doesn't raise", but genuinely produces
    correct output for the candidate that was fine all along."""
    store = FactStore()
    store.update_fact(
        OPENAI_GPT4O,
        _fact("context_window_tokens", "not-a-number", "snap_openai_ctx"),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    store.update_fact(
        ANTHROPIC_CLAUDE,
        _fact("context_window_tokens", "128000", "snap_anthropic_ctx", "f2"),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    store.update_fact(
        GOOGLE_GEMINI,
        _fact("context_window_tokens", "256000", "snap_google_ctx", "f3"),
        source_url="https://google.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    rows = build_fact_table(
        store, [OPENAI_GPT4O, ANTHROPIC_CLAUDE, GOOGLE_GEMINI], ["context_window_tokens"]
    )

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            assertions=[
                ComparisonAssertion(
                    subject_a=OPENAI_GPT4O,
                    subject_b=ANTHROPIC_CLAUDE,
                    field="context_window_tokens",
                ),
                # A second, unrelated, well-formed assertion between two
                # OTHER valid subjects -- proves the malformed-value
                # failure above didn't abort the rest of the batch, and
                # that the survivor is genuinely usable, not just absent
                # from an exception.
                ComparisonAssertion(
                    subject_a=ANTHROPIC_CLAUDE,
                    subject_b=GOOGLE_GEMINI,
                    field="context_window_tokens",
                ),
            ]
        )

    with caplog.at_level(logging.WARNING):
        claims = compare_subjects(rows, call_fn=fake_call)

    # OpenAI's malformed value drops that one candidate; Anthropic vs.
    # Google is well-formed throughout and survives as a real claim.
    assert "comparison_malformed_value" in caplog.text
    assert len(claims) == 1
    assert set(claims[0].citation_snapshot_ids) == {"snap_anthropic_ctx", "snap_google_ctx"}
    assert claims[0].text == (
        "Anthropic's Claude has a lower context window (128000) than Google's Gemini (256000)."
    )
    assert claims[0].validation_status == "pending"


def test_reversed_pair_duplicate_is_deduped() -> None:
    """ADR 0005 point 1: (A, B, field) and (B, A, field) are the same
    comparison -- only the first is resolved into a claim."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            assertions=[
                ComparisonAssertion(
                    subject_a=OPENAI_GPT4O,
                    subject_b=ANTHROPIC_CLAUDE,
                    field="context_window_tokens",
                ),
                ComparisonAssertion(
                    subject_a=ANTHROPIC_CLAUDE,
                    subject_b=OPENAI_GPT4O,
                    field="context_window_tokens",
                ),
            ]
        )

    claims = compare_subjects(_rows(), call_fn=fake_call)
    assert len(claims) == 1


def test_different_field_for_the_same_pair_is_not_deduped() -> None:
    """The dedup key includes the field (ADR 0005 point 1) -- a second
    assertion for the same pair but a DIFFERENT field must be resolved
    independently, not dropped as a duplicate of the first. Uses a
    non-comparable field first (rejected on its own terms) to prove the
    second, comparable-field assertion for the same pair still gets a
    chance to resolve -- if field weren't part of the key, the pair alone
    would already be "seen" and the second assertion would never be
    evaluated at all, so this would incorrectly yield zero claims."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            assertions=[
                ComparisonAssertion(
                    subject_a=OPENAI_GPT4O, subject_b=ANTHROPIC_CLAUDE, field="benchmark_scores"
                ),
                ComparisonAssertion(
                    subject_a=OPENAI_GPT4O,
                    subject_b=ANTHROPIC_CLAUDE,
                    field="context_window_tokens",
                ),
            ]
        )

    claims = compare_subjects(_rows(), call_fn=fake_call)
    assert len(claims) == 1


def test_sparse_table_yields_abstention_not_a_fabricated_claim() -> None:
    """Adversarial check per the original design: feed a sparse table and
    confirm the (simulated) model declining to compare is accepted as
    correct output, not treated as a failure."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(assertions=[])

    empty_rows = [FactRow(subject=OPENAI_GPT4O, field="context_window_tokens")]
    assert compare_subjects(empty_rows, call_fn=fake_call) == []
