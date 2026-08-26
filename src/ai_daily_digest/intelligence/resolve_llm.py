"""LLM fallback resolver — runs only on the residue deterministic
matching (resolve.py) left unresolved or ambiguous. Resolves to a Subject
(company + product), not an "Entity" — see shared/schemas.py. See
docs/LLM_AGENT_SPECS.md#resolve_llm for the full contract.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel

from ai_daily_digest.intelligence.llm import HAIKU, call_structured
from ai_daily_digest.intelligence.prompt_templates import load_prompt, render
from ai_daily_digest.intelligence.resolve import ResolutionResult
from ai_daily_digest.shared.schemas import SourceItem, Subject

logger = logging.getLogger("intelligence.resolve_llm")

CONFIDENCE_THRESHOLD = 0.6


class ResolveLLMResponse(BaseModel):
    company: str | None = None
    product: str | None = None
    new_subject_proposal: str | None = None
    confidence: float


def _format_candidates(subjects: list[Subject]) -> str:
    if not subjects:
        return "(no candidate subjects)"
    return "\n".join(f"- {s.company}: {s.product}" for s in subjects)


def _default_call(system: str, prompt: str) -> ResolveLLMResponse:
    return call_structured(
        model=HAIKU,
        system=system,
        prompt=prompt,
        response_model=ResolveLLMResponse,
    )


def resolve_via_llm(
    item: SourceItem,
    candidate_subjects: list[Subject],
    *,
    item_text: str = "",
    call_fn: Callable[[str, str], ResolveLLMResponse] | None = None,
) -> ResolutionResult:
    """call_fn is injectable for testing — defaults to the real Anthropic
    call via intelligence/llm.py. Confidence below CONFIDENCE_THRESHOLD is
    never auto-merged, regardless of what the model proposed — it's
    logged for manual review instead (see intelligence/CLAUDE.md)."""
    system, user_template = load_prompt("resolve")
    prompt = render(
        user_template,
        item_title=item.title,
        item_body_excerpt=item_text[:500],
        candidate_subjects=_format_candidates(candidate_subjects),
    )

    call = call_fn or _default_call
    response = call(system, prompt)

    proposed_subject = (
        Subject(company=response.company, product=response.product)
        if response.company and response.product
        else None
    )

    if response.confidence < CONFIDENCE_THRESHOLD:
        logger.warning(
            "llm_resolution_low_confidence item_id=%s proposed_subject=%s "
            "confidence=%s -- flagged for manual review, not auto-merged",
            item.id,
            proposed_subject,
            response.confidence,
        )
        result = ResolutionResult(
            item_id=item.id,
            subject=None,
            method="llm_low_confidence",
            confidence=response.confidence,
            matched_text=response.new_subject_proposal,
            candidate_subjects=candidate_subjects,
        )
    elif proposed_subject is not None:
        result = ResolutionResult(
            item_id=item.id,
            subject=proposed_subject,
            method="llm_resolved",
            confidence=response.confidence,
        )
    else:
        result = ResolutionResult(
            item_id=item.id,
            subject=None,
            method="llm_new_subject_proposal",
            confidence=response.confidence,
            matched_text=response.new_subject_proposal,
        )

    logger.info(
        "resolution item_id=%s subject=%s method=%s confidence=%s",
        result.item_id,
        result.subject,
        result.method,
        result.confidence,
    )
    return result
