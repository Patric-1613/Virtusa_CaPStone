"""Tests the plumbing (prompt rendering, grounding/confidence gates) with
an injected fake call_fn — no network/API key needed."""

from datetime import UTC, datetime

from ai_daily_digest.intelligence.extract_facts import (
    FactCandidate,
    FactExtractionResponse,
    extract_facts,
)
from ai_daily_digest.shared.schemas import DocumentSnapshot, Subject


def _snapshot(text):
    return DocumentSnapshot(
        id="snap_1",
        source_item_id="item_1",
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash="sha256:x",
        content_text=text,
    )


def _subject():
    return Subject(company="OpenAI", product="GPT-4o")


def test_well_grounded_high_confidence_fact_is_accepted():
    text = "GPT-4o's context window has been increased to 256,000 tokens."

    def fake_call(system, prompt):
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


def test_unknown_field_is_rejected():
    def fake_call(system, prompt):
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


def test_low_confidence_is_rejected():
    text = "GPT-4o's context window is roughly large, maybe 256k tokens."

    def fake_call(system, prompt):
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


def test_ungrounded_quoted_span_is_rejected():
    """The model claims a value but the quoted span doesn't actually
    appear in the snapshot text -- this is the fabrication case the
    grounding check exists to catch."""
    text = "GPT-4o now ships with a 256,000 token context window."

    def fake_call(system, prompt):
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


def test_empty_response_yields_empty_facts():
    def fake_call(system, prompt):
        return FactExtractionResponse(facts=[])

    facts = extract_facts(_subject(), _snapshot("nothing comparable here"), call_fn=fake_call)
    assert facts == []
