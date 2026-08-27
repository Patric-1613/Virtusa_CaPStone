"""Tests the plumbing (prompt rendering, grounding/confidence gates) with
an injected fake call_fn — no network/API key needed."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_daily_digest.intelligence.extract_facts import (
    FactCandidate,
    FactExtractionResponse,
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
