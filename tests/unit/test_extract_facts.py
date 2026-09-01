"""Tests the plumbing (prompt rendering, grounding/confidence gates) with
an injected fake call_fn — no network/API key needed."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_daily_digest.intelligence.extract_facts import (
    FactCandidate,
    FactExtractionResponse,
    _quote_supports_non_disclosure,
    extract_facts,
)
from ai_daily_digest.shared.schemas import DocumentSnapshot, Subject


def _snapshot(text: str) -> DocumentSnapshot:
    return DocumentSnapshot(
        id="snap_1",
        source_item_id="item_1",
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash="sha256:x",
        content_text=text,
    )


def _subject() -> Subject:
    return Subject(company="OpenAI", product="GPT-4o")


def test_well_grounded_high_confidence_fact_is_accepted() -> None:
    text = "GPT-4o's context window has been increased to 256,000 tokens."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="256000",
                    quoted_span="context window has been increased to 256,000 tokens",
                    confidence=0.95,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert len(facts) == 1
    assert facts[0].field == "context_window_tokens"
    assert facts[0].value == "256000"
    assert facts[0].extraction_method == "llm_structured_output"
    assert facts[0].extraction_model
    assert facts[0].prompt_version
    # ADR 0004: the evidence a fact was built from is kept, not discarded.
    assert facts[0].quoted_span == "context window has been increased to 256,000 tokens"
    assert facts[0].confidence == 0.95


def test_unknown_field_is_rejected() -> None:
    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="totally_made_up_field",
                    value="x",
                    quoted_span="x",
                    confidence=0.99,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot("some text"), call_fn=fake_call)
    assert facts == []


def test_low_confidence_is_rejected() -> None:
    text = "GPT-4o's context window is roughly large, maybe 256k tokens."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="256000",
                    quoted_span="roughly large, maybe 256k tokens",
                    confidence=0.3,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []


def test_ungrounded_quoted_span_is_rejected() -> None:
    """The model claims a value but the quoted span doesn't actually
    appear in the snapshot text -- this is the fabrication case the
    grounding check exists to catch."""
    text = "GPT-4o now ships with a 256,000 token context window."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="256000",
                    quoted_span="this exact sentence does not appear anywhere",
                    confidence=0.95,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []


def test_grounded_quote_with_fabricated_value_is_rejected() -> None:
    """Adversarial case per the review: the quoted_span is real (it does
    appear in the source), but the value the model reports doesn't
    actually match what that quote says -- a fabricated number hiding
    behind a legitimate-looking quote."""
    text = "GPT-4o's context window has been increased to 256,000 tokens."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="999999",
                    quoted_span="context window has been increased to 256,000 tokens",
                    confidence=0.95,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []


def test_ambiguous_multi_field_quote_drops_both_candidates() -> None:
    """The exact reproduced case from the third review: 'Input costs 5
    and output costs 15' -- if both input_price_usd and output_price_usd
    candidates share this quote, neither value can be confidently
    attributed (input_price_usd=15 would otherwise pass, since 15 does
    appear somewhere in the quote). Both are dropped rather than
    guessing which one is right."""
    text = "Input costs 5 and output costs 15 per million tokens."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="input_price_usd",
                    value="5",
                    quoted_span="Input costs 5 and output costs 15",
                    confidence=0.9,
                ),
                FactCandidate(
                    field="output_price_usd",
                    value="15",
                    quoted_span="Input costs 5 and output costs 15",
                    confidence=0.9,
                ),
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []


def test_shared_quote_for_the_same_field_is_not_flagged_as_ambiguous() -> None:
    """Sanity check that the ambiguity guard is scoped to DIFFERENT
    fields sharing a quote -- the legitimate "increased from X to Y"
    pattern (two numbers, one field, no sibling candidate) must still be
    accepted, same as test_well_grounded_high_confidence_fact_is_accepted."""
    text = "GPT-4o's context window has been increased from 128,000 to 256,000 tokens."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="256000",
                    quoted_span="increased from 128,000 to 256,000 tokens",
                    confidence=0.95,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert len(facts) == 1
    assert facts[0].value == "256000"


def test_nan_confidence_is_rejected_at_parse_time_not_silently_accepted() -> None:
    """Adversarial case per the review: confidence=NaN made every
    "confidence < CONFIDENCE_THRESHOLD" check in the codebase silently
    False (NaN compares False against everything), bypassing the
    low-confidence gate entirely. The Confidence type now rejects it
    before extract_facts() ever sees it."""
    with pytest.raises(ValidationError):
        FactCandidate(
            field="context_window_tokens",
            value="256000",
            quoted_span="256,000 tokens",
            confidence=float("nan"),
        )


def test_empty_response_yields_empty_facts() -> None:
    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(facts=[])

    facts = extract_facts(_subject(), _snapshot("nothing comparable here"), call_fn=fake_call)
    assert facts == []


# --- ADR 0006: "unknown" vs. "not disclosed" are different claims. ---


def test_well_grounded_not_disclosed_candidate_is_accepted() -> None:
    text = "OpenAI has not yet announced pricing for GPT-4o."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="input_price_usd",
                    value=None,
                    disclosure_status="not_disclosed",
                    quoted_span="has not yet announced pricing",
                    confidence=0.9,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert len(facts) == 1
    assert facts[0].value is None
    assert facts[0].disclosure_status == "not_disclosed"
    assert facts[0].quoted_span == "has not yet announced pricing"


def test_not_disclosed_candidate_skips_the_value_support_check() -> None:
    """There is no value to check support for -- value_supported_by_quote()
    never runs for a not_disclosed candidate (nothing to check it
    against). Proven by a quote that contains no number at all -- it
    would have nothing to match if that check ran, but this candidate is
    still accepted because the check that DOES run for a not_disclosed
    candidate (_quote_supports_non_disclosure) only requires an approved
    withholding phrase, a field-matching keyword, and no real number --
    all satisfied here without needing a value at all."""
    text = "Pricing details for GPT-4o have not been announced."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="input_price_usd",
                    value=None,
                    disclosure_status="not_disclosed",
                    quoted_span="Pricing details for GPT-4o have not been announced",
                    confidence=0.9,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert len(facts) == 1
    assert facts[0].value is None


def test_ungrounded_not_disclosed_quoted_span_is_rejected() -> None:
    """A not_disclosed candidate still needs a real citation -- its
    quoted_span must actually appear in the snapshot text, the same
    grounding check every other candidate goes through."""
    text = "GPT-4o's context window has been increased to 256,000 tokens."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="input_price_usd",
                    value=None,
                    disclosure_status="not_disclosed",
                    quoted_span="this exact non-disclosure sentence does not appear anywhere",
                    confidence=0.9,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []


def test_low_confidence_not_disclosed_candidate_is_rejected() -> None:
    text = "Pricing has not yet been announced."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="input_price_usd",
                    value=None,
                    disclosure_status="not_disclosed",
                    quoted_span="has not yet been announced",
                    confidence=0.3,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []


def test_disclosed_and_not_disclosed_candidates_coexist_in_one_response() -> None:
    """A single extraction response can legitimately report one field as
    disclosed and another as explicitly not disclosed -- both are
    accepted independently."""
    text = (
        "GPT-4o's context window has been increased to 256,000 tokens. "
        "Pricing has not yet been announced."
    )

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="256000",
                    quoted_span="context window has been increased to 256,000 tokens",
                    confidence=0.95,
                ),
                FactCandidate(
                    field="input_price_usd",
                    value=None,
                    disclosure_status="not_disclosed",
                    quoted_span="Pricing has not yet been announced",
                    confidence=0.9,
                ),
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert len(facts) == 2
    by_field = {f.field: f for f in facts}
    assert by_field["context_window_tokens"].value == "256000"
    assert by_field["context_window_tokens"].disclosure_status == "disclosed"
    assert by_field["input_price_usd"].value is None
    assert by_field["input_price_usd"].disclosure_status == "not_disclosed"


def test_not_disclosed_candidate_with_a_value_is_rejected_at_construction() -> None:
    """FactCandidate's own model validator (ADR 0006) -- the same
    contradiction ExtractedFact itself rejects, caught here on the raw
    model response instead."""
    with pytest.raises(ValidationError, match="not_disclosed"):
        FactCandidate(
            field="input_price_usd",
            value="5",
            disclosure_status="not_disclosed",
            quoted_span="pricing has not yet been announced",
            confidence=0.9,
        )


def test_disclosed_candidate_with_explicit_none_value_is_rejected_at_construction() -> None:
    with pytest.raises(ValidationError, match="disclosed"):
        FactCandidate(
            field="input_price_usd",
            value=None,
            quoted_span="some quote",
            confidence=0.9,
        )


# --- value has no default on either model (ADR 0006 revision) -- a
# construction site that omits it entirely must be rejected, never
# silently fall back to a value that means something specific
# (previously None, i.e. "not disclosed"). ---


def test_factcandidate_omitting_value_entirely_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FactCandidate(  # type: ignore[call-arg]
            field="input_price_usd",
            disclosure_status="not_disclosed",
            quoted_span="pricing has not been announced",
            confidence=0.9,
        )


# --- _quote_supports_non_disclosure: deterministic semantic-support
# check for a not_disclosed candidate's quote (ADR 0006 revision
# requested by Person A) -- quote existence alone is not proof the quote
# actually supports a non-disclosure claim about the right field. Tested
# directly against the phrases the review specifies. ---


def test_disclosed_value_mislabeled_not_disclosed_is_rejected() -> None:
    """The quote states a real value ("$5 per million tokens") -- this
    is a disclosed fact mislabeled not_disclosed, not a genuine
    non-disclosure. Fails closed rather than trusting the label."""
    assert not _quote_supports_non_disclosure(
        "input_price_usd",
        "pricing has not been announced, though early testers report $5 per million tokens",
    )


def test_genuine_non_disclosure_quote_assigned_to_the_wrong_field_is_rejected() -> None:
    """A real pricing non-disclosure statement, but reported against
    context_window_tokens -- the field the quote actually supports and
    the field the candidate claims don't match."""
    assert not _quote_supports_non_disclosure(
        "context_window_tokens", "Pricing has not yet been announced"
    )


def test_vague_absence_wording_without_explicit_withholding_is_rejected() -> None:
    """No approved withholding phrase at all -- vague absence must not
    be read as an explicit non-disclosure statement."""
    assert not _quote_supports_non_disclosure(
        "context_window_tokens", "We tested multiple models across tasks"
    )


def test_accepted_explicit_withholding_phrases_are_supported() -> None:
    assert _quote_supports_non_disclosure("input_price_usd", "pricing has not been announced")
    assert _quote_supports_non_disclosure(
        "context_window_tokens", "context window details are not published"
    )


def test_end_to_end_disclosed_value_mislabeled_not_disclosed_is_rejected() -> None:
    """Integration proof that _quote_supports_non_disclosure is actually
    wired into extract_facts(), not just correct in isolation."""
    text = (
        "GPT-4o pricing has not been announced, though early testers report $5 per million tokens."
    )

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="input_price_usd",
                    value=None,
                    disclosure_status="not_disclosed",
                    quoted_span=(
                        "pricing has not been announced, though early testers "
                        "report $5 per million tokens"
                    ),
                    confidence=0.9,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []


# --- Clause-bounded field-concept matching (ADR 0006 revision requested
# by Persons A and C, second round) -- tightens the field-concept
# patterns to whole PHRASES, not single words ("rate limit"/"context
# window", never bare "limit"/"token"/"terms"/"available"), and requires
# the withholding phrase and the field concept to appear in the SAME
# clause of the quote, not just somewhere in the whole quote. Tested
# directly against every phrase the review specifies. ---


def test_input_price_rejects_modalities_quote() -> None:
    assert not _quote_supports_non_disclosure(
        "input_price_usd", "Input modalities have not been announced"
    )


def test_output_price_rejects_modalities_quote() -> None:
    assert not _quote_supports_non_disclosure(
        "output_price_usd", "Output modalities have not been published"
    )


def test_input_price_rejects_rate_hiding_inside_corporate() -> None:
    """ "rate" must not match as a substring inside "corporate" -- whole-
    word/whole-phrase matching, not a loose substring check."""
    assert not _quote_supports_non_disclosure(
        "input_price_usd", "Corporate benchmark results have not been published"
    )


def test_context_window_rejects_rate_limits_quote() -> None:
    """ "limit" alone must not stand in for the context-window concept --
    "Rate limits" is a pricing/throttling concept, not a context window."""
    assert not _quote_supports_non_disclosure(
        "context_window_tokens", "Rate limits have not been announced"
    )


def test_licence_terms_rejects_payment_terms_quote() -> None:
    """ "terms" alone must not stand in for the licence_terms concept --
    "Payment terms" has nothing to do with licensing."""
    assert not _quote_supports_non_disclosure(
        "licence_terms", "Payment terms have not been published"
    )


def test_availability_regions_rejects_plain_service_availability_quote() -> None:
    """ "is not available" alone is a plain feature/service-availability
    statement, not a claim that REGIONS specifically are being withheld
    -- must not be read as a non-disclosure claim at all."""
    assert not _quote_supports_non_disclosure(
        "availability_regions", "The model is not available in Europe"
    )


def test_availability_regions_rejects_benchmark_availability_quote() -> None:
    assert not _quote_supports_non_disclosure(
        "availability_regions", "Benchmark results are not available"
    )


def test_context_window_rejects_field_concept_and_withholding_in_different_clauses() -> None:
    """The withholding phrase and the field concept both appear
    SOMEWHERE in the quote, but about different clauses/topics -- must
    be rejected, not accepted just because both patterns match the whole
    quote independently."""
    assert not _quote_supports_non_disclosure(
        "context_window_tokens",
        "The model features a large context window. Pricing details have not been released.",
    )


def test_input_price_accepts_pricing_quote() -> None:
    assert _quote_supports_non_disclosure("input_price_usd", "Input pricing has not been announced")


def test_output_price_accepts_cost_quote() -> None:
    assert _quote_supports_non_disclosure(
        "output_price_usd", "Cost per output token is undisclosed"
    )


def test_context_window_accepts_context_window_quote() -> None:
    assert _quote_supports_non_disclosure(
        "context_window_tokens", "Context window details are not published"
    )


def test_benchmark_scores_accepts_benchmark_scores_quote() -> None:
    assert _quote_supports_non_disclosure(
        "benchmark_scores", "Benchmark scores have not yet been released"
    )


def test_availability_regions_accepts_countries_quote() -> None:
    assert _quote_supports_non_disclosure(
        "availability_regions", "Supported countries have not been announced"
    )


def test_licence_terms_accepts_licensing_terms_quote() -> None:
    assert _quote_supports_non_disclosure(
        "licence_terms", "Commercial licensing terms are undisclosed"
    )


def test_modalities_accepts_input_output_modalities_quote() -> None:
    assert _quote_supports_non_disclosure(
        "modalities", "Supported input and output modalities have not been revealed"
    )


# --- Compound-clause false positives (ADR 0006 revision requested by
# Person C, third round) -- a comma+conjunction or a bare contrast
# conjunction can join two unrelated facts into one grammatical sentence
# with no terminal punctuation between them; splitting only on
# `. ! ? ; \n` (round 2) missed this. Fixed with a wider clause split
# (_CLAUSE_SPLIT_RE) plus a same-clause cross-field-family guard
# (_clause_names_a_different_family) for the case a comma doesn't even
# separate (a bare "and" with no leading comma). ---


def test_context_window_rejects_comma_but_compound_clause() -> None:
    """The withholding phrase and the context-window concept are each
    real, but about different halves of a comma+"but"-joined compound
    sentence -- the clause split must separate them, same as two
    sentences would."""
    assert not _quote_supports_non_disclosure(
        "context_window_tokens",
        "The model has a large context window, but pricing details have not been released",
    )


def test_benchmark_scores_rejects_bare_and_compound_clause() -> None:
    """No comma before "and" here -- the clause split alone does not
    separate this into two clauses, so the cross-field-family guard is
    what catches it: the one remaining clause satisfies
    benchmark_scores's own concept and a withholding phrase, but also
    plainly names pricing in the same clause."""
    assert not _quote_supports_non_disclosure(
        "benchmark_scores", "Benchmark scores are strong and pricing has not been announced"
    )


def test_context_window_rejects_two_families_in_one_clause() -> None:
    """Both context_window_tokens's own concept AND a different family
    (benchmarks) appear in the SAME clause -- the matching-family set is
    {"context_window", "benchmarks"}, not exactly {"context_window"}, so
    this must fail closed even though the candidate's own field concept
    really is present."""
    assert not _quote_supports_non_disclosure(
        "context_window_tokens", "Context window and benchmark scores have not been disclosed"
    )


def test_input_price_accepts_combined_input_output_pricing_quote() -> None:
    """input_price_usd and output_price_usd share one "pricing" family
    -- a clause naming both sides of the pricing story is still exactly
    one matching family, not treated as cross-contaminated."""
    assert _quote_supports_non_disclosure(
        "input_price_usd", "Input and output pricing have not been announced"
    )


def test_output_price_accepts_combined_input_output_pricing_quote() -> None:
    assert _quote_supports_non_disclosure(
        "output_price_usd", "Input and output pricing have not been announced"
    )


def test_end_to_end_cross_family_clause_candidate_is_dropped() -> None:
    """Integration proof that the cross-family guard is actually wired
    into extract_facts(), not just correct in isolation."""
    text = "Context window and benchmark scores have not been disclosed for this release."

    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value=None,
                    disclosure_status="not_disclosed",
                    quoted_span="Context window and benchmark scores have not been disclosed",
                    confidence=0.9,
                )
            ]
        )

    facts = extract_facts(_subject(), _snapshot(text), call_fn=fake_call)
    assert facts == []
