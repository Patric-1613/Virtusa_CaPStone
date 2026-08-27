"""Cross-subject comparison — the one LLM call site from the original
project design not yet built (see docs/LLM_AGENT_SPECS.md's "Not yet
built" section, now moved here). Reads a structured fact table only,
never raw article text: the architectural decision that stops fabricated
competitive claims. Sparse data must yield abstention (an empty claims
list), never an invented comparison — enforced both in the prompt and,
more importantly, in code below.

Three checks run on every candidate, all required: (1) subjects/fields
are real, (2) citation ids are real AND owned by the right (subject,
field), (3) every number the candidate's own PROSE asserts actually
matches the real value of a row it's comparing — (2) alone lets a
candidate cite a real, correctly-owned row while still stating the wrong
number for it; (3) is what catches that.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from pydantic import BaseModel, Field

from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.grounding import numbers_in
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


@dataclass
class _TableIndex:
    """Everything a candidate is checked against, built once per
    compare_subjects() call rather than re-derived per candidate. Keeping
    this as one object (instead of four separate local variables) is what
    keeps compare_subjects() itself small — see _candidate_rejection_reason
    for how it's used."""

    known_fields: set[str]
    known_subject_pairs: set[tuple[str, str]]
    # Keyed by exactly which (subject, field) a snapshot id actually
    # supports -- a flat set of "every real id anywhere in the table"
    # would let a claim cite a real id that belongs to a completely
    # different subject/field than the one it's claiming about.
    snapshot_by_subject_field: dict[tuple[tuple[str, str], str], str] = dataclass_field(
        default_factory=dict
    )
    # Keyed the same way, but by the row's actual VALUE -- this is what
    # catches false comparison prose supplied with real, correctly-owned
    # citation ids: the citation-ownership check above only proves the id
    # belongs to the right (subject, field), not that the claim's text
    # states the right number for it.
    value_by_subject_field: dict[tuple[tuple[str, str], str], str] = dataclass_field(
        default_factory=dict
    )


def _index_rows(rows: list[FactRow]) -> _TableIndex:
    return _TableIndex(
        known_fields={row.field for row in rows},
        known_subject_pairs={(row.subject.company, row.subject.product) for row in rows},
        snapshot_by_subject_field={
            ((row.subject.company, row.subject.product), row.field): row.snapshot_id
            for row in rows
            if row.snapshot_id
        },
        value_by_subject_field={
            ((row.subject.company, row.subject.product), row.field): row.value
            for row in rows
            if row.value is not None
        },
    )


def _candidate_rejection_reason(  # pylint: disable=too-many-return-statements
    # One independent guardrail per return, each a distinct rejection
    # reason surfaced in the log line and easy to test in isolation --
    # collapsing these into fewer returns would trade that clarity for
    # nothing; the seven checks are the seven guardrails this module's
    # docstring documents, not incidental complexity.
    candidate: ComparisonClaimCandidate,
    index: _TableIndex,
) -> str | None:
    """None means the candidate passes every check. Otherwise, the reason
    it was rejected -- used both for the log message and to short-circuit
    compare_subjects()'s loop with a single condition."""
    if len(candidate.subjects) != 2:
        return "not_two_subjects"
    if candidate.subjects[0] == candidate.subjects[1]:
        # A "comparison" naming the same subject twice isn't a
        # comparison -- catches e.g. a model given a single-subject
        # candidate set that still fills both slots with it rather than
        # abstaining. Subject equality is company+product (frozen model,
        # see shared/schemas.py), not identity.
        return "subject_compared_to_itself"
    if any((s.company, s.product) not in index.known_subject_pairs for s in candidate.subjects):
        return "unknown_subject"
    if not candidate.fields or any(f not in index.known_fields for f in candidate.fields):
        return "unknown_field"

    # A citation only counts if it's the real snapshot id for one of THIS
    # candidate's own (subject, field) combinations -- not just any real
    # id anywhere in the table.
    allowed_ids = {
        index.snapshot_by_subject_field[((s.company, s.product), f)]
        for s in candidate.subjects
        for f in candidate.fields
        if ((s.company, s.product), f) in index.snapshot_by_subject_field
    }
    if not candidate.snapshot_ids or any(sid not in allowed_ids for sid in candidate.snapshot_ids):
        return "ungrounded_citation"

    # The citation ids are real and correctly owned (checked above), but
    # that doesn't prove the claim's PROSE states the right numbers -- a
    # model could cite a legitimate row while asserting a different value
    # for it. Every number the claim text asserts must actually appear
    # among the real values of the rows it's comparing.
    allowed_value_numbers: set[str] = set()
    for s in candidate.subjects:
        for f in candidate.fields:
            row_value = index.value_by_subject_field.get(((s.company, s.product), f))
            if row_value is not None:
                allowed_value_numbers |= numbers_in(row_value)
    if numbers_in(candidate.text) - allowed_value_numbers:
        return "fabricated_value"

    return None


def compare_subjects(
    rows: list[FactRow],
    *,
    call_fn: Callable[[str, str], ComparisonResponse] | None = None,
) -> list[DigestClaim]:
    """rows: build with build_fact_table(). Every candidate is checked
    against the table before becoming a DigestClaim — a candidate that
    fails any check is dropped and logged, never "corrected"."""
    index = _index_rows(rows)
    system, user_template = load_prompt("compare_subjects")
    prompt = render(user_template, fact_table=_format_table(rows))

    call = call_fn or _default_call
    response = call(system, prompt)

    claims: list[DigestClaim] = []
    for candidate in response.claims:
        reason = _candidate_rejection_reason(candidate, index)
        if reason is not None:
            logger.warning("comparison_rejected reason=%s text=%r", reason, candidate.text)
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
