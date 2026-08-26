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
from ai_daily_digest.shared.schemas import Confidence, SourceItem, Subject

logger = logging.getLogger("intelligence.resolve_llm")

CONFIDENCE_THRESHOLD = 0.6


class ResolveLLMResponse(BaseModel):
    company: str | None = None
    product: str | None = None
    new_subject_proposal: str | None = None
    # Confidence rejects NaN at parse time -- see shared/schemas.py's
    # comment. Without this, confidence=NaN silently passed the
    # "< CONFIDENCE_THRESHOLD" check below (NaN < 0.6 is False).
    confidence: Confidence


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
    call via intelligence/llm.py. Two independent guardrails, both
    required for an auto-merge: confidence below CONFIDENCE_THRESHOLD is
    never auto-merged, and neither is a company/product the model
    proposed that isn't actually one of `candidate_subjects` — both are
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
    elif proposed_subject is not None and proposed_subject in candidate_subjects:
        result = ResolutionResult(
            item_id=item.id,
            subject=proposed_subject,
            method="llm_resolved",
            confidence=response.confidence,
        )
    elif proposed_subject is not None:
        # High confidence, but the model proposed a company/product that
        # isn't even one of the candidates it was given -- accepting this
        # would let the model invent a subject out of thin air. Treated
        # the same as a new-subject proposal (flagged, not auto-merged),
        # not silently coerced into one of the real candidates.
        logger.warning(
            "llm_resolution_subject_not_in_candidates item_id=%s proposed_subject=%s "
            "candidates=%s -- flagged for manual review, not auto-merged",
            item.id,
            proposed_subject,
            candidate_subjects,
        )
        result = ResolutionResult(
            item_id=item.id,
            subject=None,
            method="llm_subject_not_in_candidates",
            confidence=response.confidence,
            matched_text=f"{proposed_subject.company}: {proposed_subject.product}",
            candidate_subjects=candidate_subjects,
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
