"""Fact extraction — the "Extract typed facts with citations" step in
docs/ARCHITECTURE.md's intelligence workflow diagram. Turns one
DocumentSnapshot's text into zero or more ExtractedFact records against
the closed field list (shared/attributes.py). See
docs/LLM_AGENT_SPECS.md#extract_facts for the full contract.

Four guardrails enforced in code, not just requested in the prompt:
  1. quoted_span must actually appear in the snapshot text (grounding
     check) -- a model that paraphrases instead of quoting produces a
     fact that gets silently dropped, not silently stored.
  2. value must actually be supported by quoted_span itself -- a real,
     grounded quote can still have an invented value attached to it
     (e.g. quoting a real sentence but reporting a different number than
     it states); check #1 alone can't catch that, see grounding.py.
  3. field must be in the closed list -- an invented field is dropped.
  4. a quote shared across two different fields' candidates in the SAME
     extraction response is ambiguous evidence -- e.g. "Input costs 5
     and output costs 15" doesn't prove which number is input_price_usd
     and which is output_price_usd. Per review: this is CONTAINMENT, not
     a complete attribution fix -- see _cross_contaminated_indices'
     own docstring for its documented limits (it can't catch a mix-up
     when the sibling fact is omitted entirely, and can false-positive
     on coincidental shared digits). The real fix needs character-offset
     citations, not a re-quoted substring -- see
     docs/DESIGN_PROPOSAL_comparison_and_grounding.md point (e), not yet
     built. Until then, BOTH candidates sharing ambiguous evidence are
     dropped rather than guessing which one is right.

The accepted quoted_span and confidence are kept on the resulting
ExtractedFact (not discarded) so the evidence a fact was built from can
still be audited later, not just at extraction time -- see
docs/adr/0004-extracted-fact-keeps-evidence.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, Field

from ai_daily_digest.intelligence.facts import normalise_name
from ai_daily_digest.intelligence.grounding import numbers_in, value_supported_by_quote
from ai_daily_digest.intelligence.llm import SONNET, call_structured
from ai_daily_digest.intelligence.prompt_templates import load_prompt, render
from ai_daily_digest.shared.attributes import COMPARABLE_FIELDS
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import Confidence, DocumentSnapshot, ExtractedFact, Subject

logger = logging.getLogger("intelligence.extract_facts")

CONFIDENCE_THRESHOLD = 0.6
PROMPT_VERSION = "extract-facts-v1"


class FactCandidate(BaseModel):
    field: str
    value: str
    quoted_span: str
    # Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    # -- rejects confidence=NaN at parse time (see shared/schemas.py's
    # comment): NaN silently passed every "< CONFIDENCE_THRESHOLD" check
    # below before this was added, since every comparison with NaN is
    # False. A malformed response now fails call_structured's own
    # validation instead, triggering its retry-once-then-fail-loudly path.
    confidence: Confidence


class FactExtractionResponse(BaseModel):
    facts: list[FactCandidate] = Field(default_factory=list)


def _format_fields() -> str:
    return "\n".join(f"- {key}: {label}" for key, label in COMPARABLE_FIELDS.items())


def _cross_contaminated_indices(candidates: list[FactCandidate]) -> set[int]:
    """Indices of candidates whose quoted_span also contains a DIFFERENT
    field's candidate's value, from the same extraction response --
    exactly the "Input costs 5 and output costs 15" case reproduced in
    review: input_price_usd=5's quote also contains "15"
    (output_price_usd's own value), so we can't be confident "5" is
    really input's and not a misattribution. Deliberately narrower than
    "quote contains more than one number" -- that would also flag the
    legitimate, already-tested "increased from 128,000 to 256,000
    tokens" pattern (two numbers, one field, no sibling candidate
    involved), which this does not.

    KNOWN LIMITS (documented per review, not silently assumed complete
    -- this is containment, not a full attribution fix; see
    docs/DESIGN_PROPOSAL_comparison_and_grounding.md point (e) for the
    real fix, character-offset citations, not yet built):
      1. Only catches contamination when BOTH the correct and the
         confused candidate appear in the SAME extraction response. If
         the model returns the wrong number for a field and simply
         omits the sibling fact that would have exposed the mix-up
         (e.g. only reports input_price_usd=15, never mentions
         output_price_usd at all), there is no sibling value to compare
         against and this guard cannot catch it.
      2. Two candidates whose values coincidentally share a digit
         sequence for unrelated reasons (not an actual mix-up) will
         still be flagged and both dropped -- a safe direction to be
         wrong in (an extra fact goes to nothing rather than a wrong
         fact goes to press), but a real, accepted false-positive rate,
         not zero-cost caution."""
    ambiguous: set[int] = set()
    for i, candidate in enumerate(candidates):
        quote_numbers = numbers_in(candidate.quoted_span)
        for other in candidates:
            if other.field == candidate.field:
                continue
            other_value_numbers = numbers_in(other.value)
            if other_value_numbers and other_value_numbers <= quote_numbers:
                ambiguous.add(i)
                break
    return ambiguous


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
    ambiguous_indices = _cross_contaminated_indices(response.facts)
    facts: list[ExtractedFact] = []
    for i, candidate in enumerate(response.facts):
        if i in ambiguous_indices:
            logger.warning(
                "extraction_rejected reason=ambiguous_multi_field_quote snapshot_id=%s "
                "field=%s value=%r quoted_span=%r",
                snapshot.id,
                candidate.field,
                candidate.value,
                candidate.quoted_span,
            )
            continue
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
        # The quote itself is real (checked above), but that doesn't mean
        # the *value* the model reported actually came from it -- a model
        # can quote a real sentence and still attach a fabricated number
        # to it. This is the check that catches that specific case.
        if not value_supported_by_quote(candidate.value, candidate.quoted_span):
            logger.warning(
                "extraction_rejected reason=value_not_in_quote snapshot_id=%s field=%s "
                "value=%r quoted_span=%r",
                snapshot.id,
                candidate.field,
                candidate.value,
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
                quoted_span=candidate.quoted_span,
                confidence=candidate.confidence,
            )
        )
        logger.info(
            "extraction_accepted snapshot_id=%s field=%s confidence=%s",
            snapshot.id,
            candidate.field,
            candidate.confidence,
        )

    return facts
