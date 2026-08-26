"""Cross-subject comparison — the one LLM call site from the original
project design not yet built (see docs/LLM_AGENT_SPECS.md's "Not yet
built" section, now moved here). Reads a structured fact table only,
never raw article text: the architectural decision that stops fabricated
competitive claims. Sparse data must yield abstention (an empty claims
list), never an invented comparison — enforced both in the prompt and,
more importantly, in code below.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, Field

from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.llm import SONNET, call_structured
from ai_daily_digest.intelligence.prompt_templates import load_prompt, render
from ai_daily_digest.shared.attributes import COMPARABLE_FIELDS
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import DigestClaim, Subject

logger = logging.getLogger("intelligence.compare_subjects")

PROMPT_VERSION = "compare-subjects-v1"


class FactRow(BaseModel):
    """One cell of the fact table handed to the model — the value AND
    its snapshot id, so a claim citing this row has a real citation to
    check against. value=None means "not disclosed", never "unknown"."""

    subject: Subject
    field: str
    value: str | None = None
    snapshot_id: str | None = None


class ComparisonClaimCandidate(BaseModel):
    text: str
    subjects: list[Subject]
    fields: list[str]
    snapshot_ids: list[str]


class ComparisonResponse(BaseModel):
    claims: list[ComparisonClaimCandidate] = Field(default_factory=list)


def build_fact_table(store: FactStore, subjects: list[Subject], fields: list[str]) -> list[FactRow]:
    """Read-only view of FactStore's current state for the given subjects
    and fields — the only thing compare_subjects() is allowed to see."""
    rows: list[FactRow] = []
    for subject in subjects:
        for field in fields:
            fact = store.get_current_fact(subject, field)
            rows.append(
                FactRow(
                    subject=subject,
                    field=field,
                    value=fact.value if fact else None,
                    snapshot_id=fact.snapshot_id if fact else None,
                )
            )
    return rows


def _format_table(rows: list[FactRow]) -> str:
    lines: list[str] = []
    by_subject: dict[tuple[str, str], list[FactRow]] = {}
    for row in rows:
        by_subject.setdefault((row.subject.company, row.subject.product), []).append(row)
    for (company, product), subject_rows in by_subject.items():
        lines.append(f"{company}: {product}")
        for row in subject_rows:
            label = COMPARABLE_FIELDS.get(row.field, row.field)
            if row.value is None:
                lines.append(f"  {label}: not disclosed")
            else:
                lines.append(f"  {label}: {row.value} (snapshot {row.snapshot_id})")
    return "\n".join(lines)


def _default_call(system: str, prompt: str) -> ComparisonResponse:
    return call_structured(
        model=SONNET,
        system=system,
        prompt=prompt,
        response_model=ComparisonResponse,
    )


def compare_subjects(
    rows: list[FactRow],
    *,
    call_fn: Callable[[str, str], ComparisonResponse] | None = None,
) -> list[DigestClaim]:
    """rows: build with build_fact_table(). Every candidate is checked
    against the table before becoming a DigestClaim — a candidate that
    fails any check is dropped and logged, never "corrected"."""
    known_fields = {row.field for row in rows}
    known_subject_pairs = {(row.subject.company, row.subject.product) for row in rows}
    # Keyed by exactly which (subject, field) a snapshot id actually
    # supports -- a flat set of "every real id anywhere in the table"
    # would let a claim cite a real id that belongs to a completely
    # different subject/field than the one it's claiming about.
    snapshot_by_subject_field: dict[tuple[tuple[str, str], str], str] = {
        ((row.subject.company, row.subject.product), row.field): row.snapshot_id
        for row in rows
        if row.snapshot_id
    }

    system, user_template = load_prompt("compare_subjects")
    prompt = render(user_template, fact_table=_format_table(rows))

    call = call_fn or _default_call
    response = call(system, prompt)

    claims: list[DigestClaim] = []
    for candidate in response.claims:
        if len(candidate.subjects) != 2:
            logger.warning("comparison_rejected reason=not_two_subjects text=%r", candidate.text)
            continue
        if any((s.company, s.product) not in known_subject_pairs for s in candidate.subjects):
            logger.warning("comparison_rejected reason=unknown_subject text=%r", candidate.text)
            continue
        if not candidate.fields or any(f not in known_fields for f in candidate.fields):
            logger.warning("comparison_rejected reason=unknown_field text=%r", candidate.text)
            continue

        # A citation only counts if it's the real snapshot id for one of
        # THIS candidate's own (subject, field) combinations -- not just
        # any real id anywhere in the table.
        allowed_ids = {
            snapshot_by_subject_field[((s.company, s.product), f)]
            for s in candidate.subjects
            for f in candidate.fields
            if ((s.company, s.product), f) in snapshot_by_subject_field
        }
        if not candidate.snapshot_ids or any(
            sid not in allowed_ids for sid in candidate.snapshot_ids
        ):
            logger.warning("comparison_rejected reason=ungrounded_citation text=%r", candidate.text)
            continue

        claims.append(
            DigestClaim(
                id=new_id(),
                text=candidate.text,
                citation_snapshot_ids=candidate.snapshot_ids,
                validation_status="pending",
            )
        )
        logger.info("comparison_accepted text=%r", candidate.text)

    return claims
