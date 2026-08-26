"""Fact extraction — the "Extract typed facts with citations" step in
docs/ARCHITECTURE.md's intelligence workflow diagram. Turns one
DocumentSnapshot's text into zero or more ExtractedFact records against
the closed field list (shared/attributes.py). See
docs/LLM_AGENT_SPECS.md#extract_facts for the full contract.

Two guardrails enforced in code, not just requested in the prompt:
  1. quoted_span must actually appear in the snapshot text (grounding
     check) -- a model that paraphrases instead of quoting produces a
     fact that gets silently dropped, not silently stored.
  2. field must be in the closed list -- an invented field is dropped.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, Field

from ai_daily_digest.intelligence.facts import normalise_name
from ai_daily_digest.intelligence.llm import SONNET, call_structured
from ai_daily_digest.intelligence.prompt_templates import load_prompt, render
from ai_daily_digest.shared.attributes import COMPARABLE_FIELDS
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import DocumentSnapshot, ExtractedFact, Subject

logger = logging.getLogger("intelligence.extract_facts")

CONFIDENCE_THRESHOLD = 0.6
PROMPT_VERSION = "extract-facts-v1"


class FactCandidate(BaseModel):
    field: str
    value: str
    quoted_span: str
    confidence: float


class FactExtractionResponse(BaseModel):
    facts: list[FactCandidate] = Field(default_factory=list)


def _format_fields() -> str:
    return "\n".join(f"- {key}: {label}" for key, label in COMPARABLE_FIELDS.items())


def _default_call(system: str, prompt: str) -> FactExtractionResponse:
    return call_structured(
        model=SONNET,
        system=system,
        prompt=prompt,
        response_model=FactExtractionResponse,
    )


def extract_facts(
    subject: Subject,
    snapshot: DocumentSnapshot,
    *,
    call_fn: Callable[[str, str], FactExtractionResponse] | None = None,
) -> list[ExtractedFact]:
    """call_fn is injectable for testing — defaults to the real Anthropic
    call via intelligence/llm.py."""
    system, user_template = load_prompt("extract_facts")
    prompt = render(
        user_template,
        subject_company=subject.company,
        subject_product=subject.product,
        snapshot_id=snapshot.id,
        comparable_fields=_format_fields(),
        snapshot_text=snapshot.content_text or "",
    )

    call = call_fn or _default_call
    response = call(system, prompt)

    haystack = normalise_name(snapshot.content_text or "")
    facts: list[ExtractedFact] = []
    for candidate in response.facts:
        if candidate.field not in COMPARABLE_FIELDS:
            logger.warning(
                "extraction_rejected reason=unknown_field snapshot_id=%s field=%s",
                snapshot.id,
                candidate.field,
            )
            continue
        if candidate.confidence < CONFIDENCE_THRESHOLD:
            logger.warning(
                "extraction_rejected reason=low_confidence snapshot_id=%s field=%s confidence=%s",
                snapshot.id,
                candidate.field,
                candidate.confidence,
            )
            continue
        # normalise_name("") == "" and "" is a substring of everything,
        # so an empty/punctuation-only quoted_span would otherwise sail
        # straight through this check -- reject it explicitly rather than
        # relying on the substring test alone.
        normalised_span = normalise_name(candidate.quoted_span)
        if not normalised_span or normalised_span not in haystack:
            logger.warning(
                "extraction_rejected reason=ungrounded_span snapshot_id=%s field=%s quoted_span=%r",
                snapshot.id,
                candidate.field,
                candidate.quoted_span,
            )
            continue

        facts.append(
            ExtractedFact(
                id=new_id(),
                snapshot_id=snapshot.id,
                field=candidate.field,
                value=candidate.value,
                extraction_method="llm_structured_output",
                extraction_model=SONNET,
                prompt_version=PROMPT_VERSION,
            )
        )
        logger.info(
            "extraction_accepted snapshot_id=%s field=%s confidence=%s",
            snapshot.id,
            candidate.field,
            candidate.confidence,
        )

    return facts
