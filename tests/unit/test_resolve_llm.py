"""Tests the plumbing (prompt rendering, confidence gating, result
shaping) with an injected fake call_fn — no network/API key needed. The
real call_structured() path is exercised by intelligence/llm.py's own
tests, not here."""

from datetime import UTC, datetime

from ai_daily_digest.intelligence.prompt_templates import load_prompt, render
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse, resolve_via_llm
from ai_daily_digest.shared.schemas import SourceItem, Subject


def _item():
    return SourceItem(
        id="item_test",
        dedupe_key="sha256:item_test",
        source_id="test-source",
        publisher="Test Publisher",
        title="Some ambiguous headline",
        canonical_url="https://example.com/a",
        first_fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def _candidates():
    return [Subject(company="OpenAI", product="GPT-4o")]


def test_prompt_loads_and_renders():
    system, user_template = load_prompt("resolve")
    assert "Output JSON only" in system
    rendered = render(
        user_template,
        item_title="Test Title",
        item_body_excerpt="Test body",
        candidate_subjects="- OpenAI: GPT-4o",
    )
    assert "Test Title" in rendered
    assert "{{item_title}}" not in rendered


def test_high_confidence_resolution():
    def fake_call(system, prompt):
        return ResolveLLMResponse(company="OpenAI", product="GPT-4o", confidence=0.9)

    result = resolve_via_llm(_item(), _candidates(), call_fn=fake_call)
    assert result.subject == Subject(company="OpenAI", product="GPT-4o")
    assert result.method == "llm_resolved"


def test_low_confidence_is_never_auto_merged():
    """Even if the model proposes a subject, a low confidence score must
    not resolve it — this is the guardrail against confident-sounding
    wrong merges."""

    def fake_call(system, prompt):
        return ResolveLLMResponse(company="OpenAI", product="GPT-4o", confidence=0.3)

    result = resolve_via_llm(_item(), _candidates(), call_fn=fake_call)
    assert result.subject is None
    assert result.method == "llm_low_confidence"


def test_new_subject_proposal_with_no_existing_match():
    def fake_call(system, prompt):
        return ResolveLLMResponse(new_subject_proposal="Mistral: Le Chat", confidence=0.8)

    result = resolve_via_llm(_item(), _candidates(), call_fn=fake_call)
    assert result.subject is None
    assert result.method == "llm_new_subject_proposal"
    assert result.matched_text == "Mistral: Le Chat"
