"""Tests the plumbing (prompt rendering, confidence gating, result
shaping) with an injected fake call_fn — no network/API key needed. The
real call_structured() path is exercised by intelligence/llm.py's own
tests, not here."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_daily_digest.intelligence.prompt_templates import load_prompt, render
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse, resolve_via_llm
from ai_daily_digest.shared.schemas import SourceItem, Subject

TRL_ITEM_TEST = uuid.UUID("01a01e2f-3770-7bc0-967a-19297e60ec0c")


def _item() -> SourceItem:
    return SourceItem(
        id=TRL_ITEM_TEST,
        dedupe_key=f"sha256:{TRL_ITEM_TEST}",
        source_id="test-source",
        publisher="Test Publisher",
        title="Some ambiguous headline",
        canonical_url="https://example.com/a",  # type: ignore[arg-type]
        first_fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def _candidates() -> list[Subject]:
    return [Subject(company="OpenAI", product="GPT-4o")]


def test_prompt_loads_and_renders() -> None:
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


def test_high_confidence_resolution() -> None:
    def fake_call(system: str, prompt: str) -> ResolveLLMResponse:
        return ResolveLLMResponse(company="OpenAI", product="GPT-4o", confidence=0.9)

    result = resolve_via_llm(_item(), _candidates(), call_fn=fake_call)
    assert result.subject == Subject(company="OpenAI", product="GPT-4o")
    assert result.method == "llm_resolved"


def test_low_confidence_is_never_auto_merged() -> None:
    """Even if the model proposes a subject, a low confidence score must
    not resolve it — this is the guardrail against confident-sounding
    wrong merges."""

    def fake_call(system: str, prompt: str) -> ResolveLLMResponse:
        return ResolveLLMResponse(company="OpenAI", product="GPT-4o", confidence=0.3)

    result = resolve_via_llm(_item(), _candidates(), call_fn=fake_call)
    assert result.subject is None
    assert result.method == "llm_low_confidence"


def test_high_confidence_subject_not_in_candidates_is_not_auto_merged() -> None:
    """Adversarial case per the review: high confidence alone must not be
    enough to accept a company/product the model was never actually given
    as a candidate -- a false merge to an invented subject is exactly the
    failure mode this project's "false merge worse than a miss" rule
    exists to prevent."""

    def fake_call(system: str, prompt: str) -> ResolveLLMResponse:
        return ResolveLLMResponse(company="Mistral", product="Le Chat", confidence=0.95)

    result = resolve_via_llm(_item(), _candidates(), call_fn=fake_call)
    assert result.subject is None
    assert result.method == "llm_subject_not_in_candidates"
    assert result.candidate_subjects == _candidates()


def test_nan_confidence_is_rejected_at_parse_time_not_silently_accepted() -> None:
    """Same NaN-bypass case as test_extract_facts.py's -- confidence=NaN
    silently passed "< CONFIDENCE_THRESHOLD" here too before the
    Confidence type existed."""
    with pytest.raises(ValidationError):
        ResolveLLMResponse(company="OpenAI", product="GPT-4o", confidence=float("nan"))


def test_new_subject_proposal_with_no_existing_match() -> None:
    def fake_call(system: str, prompt: str) -> ResolveLLMResponse:
        return ResolveLLMResponse(new_subject_proposal="Mistral: Le Chat", confidence=0.8)

    result = resolve_via_llm(_item(), _candidates(), call_fn=fake_call)
    assert result.subject is None
    assert result.method == "llm_new_subject_proposal"
    assert result.matched_text == "Mistral: Le Chat"
