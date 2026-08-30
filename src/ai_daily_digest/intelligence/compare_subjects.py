"""Cross-subject comparison — ADR 0005 (docs/adr/0005-structured-
comparison-and-snapshot-resolution.md). The LLM proposes structured
`(subject_a, subject_b, field)` triples to compare; code looks up the
real stored values, applies the field's `ComparisonRule`
(shared/attributes.py), and renders the claim text deterministically —
the same pattern draft_claims.py already uses for single-subject
changes. The model never authors comparison prose or numbers at all.

This closes two fabrication classes a free-text-plus-numeric-check
design could not, no matter how many checks got bolted onto it:
  - A claim asserting no number at all ("OpenAI is cheaper") has nothing
    for a numeric check to verify, true or false.
  - A claim can state two real numbers attributed to the WRONG subject
    (swapped) — a check confirming both numbers appear *somewhere* among
    real values can't detect a swap. Deterministic rendering makes this
    class structurally impossible: code alone decides which number goes
    with which subject.

Phase 1 (ADR 0005 point 2): only `context_window_tokens` has a
registered `ComparisonRule` (`shared/attributes.py::COMPARISON_RULES`).
Every other field is excluded from comparison until its own
representation is designed — a deliberate scope limit, not a bug.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.llm import SONNET, call_structured
from ai_daily_digest.intelligence.prompt_templates import load_prompt, render
from ai_daily_digest.shared.attributes import COMPARABLE_FIELDS, COMPARISON_RULES, field_label
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import DigestClaim, Subject

logger = logging.getLogger("intelligence.compare_subjects")

PROMPT_VERSION = "compare-subjects-v2"

SubjectKey = tuple[str, str]


class FactRow(BaseModel):
    """One cell of the fact table handed to the model — the value AND
    its snapshot id, so a resolved comparison has a real citation to
    attach. value=None means "not disclosed", never "unknown"."""

    subject: Subject
    field: str
    value: str | None = None
    snapshot_id: str | None = None


class ComparisonAssertion(BaseModel):
    """What the model actually proposes now: WHICH two subjects to
    compare on WHICH field — never a value, never prose. Code alone
    decides the relation and renders the sentence (see this module's
    docstring)."""

    subject_a: Subject
    subject_b: Subject
    field: str


class ComparisonResponse(BaseModel):
    assertions: list[ComparisonAssertion] = Field(default_factory=list)


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
    by_subject: dict[SubjectKey, list[FactRow]] = {}
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


def _subject_key(subject: Subject) -> SubjectKey:
    return (subject.company, subject.product)


@dataclass
class _TableIndex:
    """Everything an assertion is resolved against, built once per
    compare_subjects() call. Keyed by exactly (subject, field) — a flat
    "every row anywhere in the table" lookup would let an assertion for
    one subject accidentally resolve against a different subject's row."""

    known_subject_pairs: set[SubjectKey]
    row_by_subject_field: dict[tuple[SubjectKey, str], FactRow]


def _index_rows(rows: list[FactRow]) -> _TableIndex:
    return _TableIndex(
        known_subject_pairs={_subject_key(row.subject) for row in rows},
        row_by_subject_field={(_subject_key(row.subject), row.field): row for row in rows},
    )


def _render_claim_text(
    subject_a: Subject, row_a: FactRow, subject_b: Subject, row_b: FactRow, relation: str
) -> str:
    """The only place comparison prose is written — always from real,
    looked-up values, never model output. Mirrors draft_claims.py's
    phrasing style for a single-field, two-subject sentence."""
    label = field_label(row_a.field)
    name_a = f"{subject_a.company}'s {subject_a.product}"
    name_b = f"{subject_b.company}'s {subject_b.product}"
    if relation == "equal":
        return f"{name_a} and {name_b} have the same {label}: {row_a.value}."
    comparative = "a higher" if relation == "higher" else "a lower"
    return f"{name_a} has {comparative} {label} ({row_a.value}) than {name_b} ({row_b.value})."


def _dedupe_key(assertion: ComparisonAssertion) -> tuple[tuple[SubjectKey, SubjectKey], str]:
    """`(sorted((subject_a, subject_b)), field)` — ADR 0005 point 1. The
    field is part of the key deliberately: (A, B, context_window_tokens)
    and (A, B, input_price_usd) are different comparisons and must not
    collapse into one just because the subject pair matches."""
    key_a, key_b = _subject_key(assertion.subject_a), _subject_key(assertion.subject_b)
    ordered_pair = (key_a, key_b) if key_a <= key_b else (key_b, key_a)
    return ordered_pair, assertion.field


def _resolve_assertion(  # pylint: disable=too-many-return-statements
    # One independent guardrail per return, matching this module's
    # previous shape — each a distinct rejection reason surfaced in the
    # log line and easy to test in isolation.
    assertion: ComparisonAssertion,
    index: _TableIndex,
) -> tuple[DigestClaim | None, str | None]:
    """Resolves one assertion to a deterministic DigestClaim, or None
    plus the reason it was rejected. Never trusts a model-authored value
    or relation — only the assertion's choice of WHICH subjects/field."""
    if _subject_key(assertion.subject_a) == _subject_key(assertion.subject_b):
        # A "comparison" naming the same subject twice isn't one --
        # catches e.g. a model given a single-subject table that still
        # fills both slots with it rather than abstaining.
        return None, "subject_compared_to_itself"
    if (
        _subject_key(assertion.subject_a) not in index.known_subject_pairs
        or _subject_key(assertion.subject_b) not in index.known_subject_pairs
    ):
        return None, "unknown_subject"

    rule = COMPARISON_RULES.get(assertion.field)
    if rule is None:
        # Phase 1: only context_window_tokens has a rule. Every other
        # field is excluded from comparison until its own representation
        # is designed (ADR 0005 point 2) -- not a guess, an exclusion.
        return None, "field_not_comparable"

    row_a = index.row_by_subject_field.get((_subject_key(assertion.subject_a), assertion.field))
    row_b = index.row_by_subject_field.get((_subject_key(assertion.subject_b), assertion.field))
    if row_a is None or row_b is None or row_a.value is None or row_b.value is None:
        return None, "value_not_disclosed"
    if row_a.snapshot_id is None or row_b.snapshot_id is None:
        return None, "ungrounded_citation"

    try:
        parsed_a = rule.parse(row_a.value)
        parsed_b = rule.parse(row_b.value)
    except (ValueError, TypeError):
        # Malformed stored value -- drop only this one candidate, never
        # abort the rest of the comparison pass (ADR 0005 point 2).
        logger.warning(
            "comparison_malformed_value field=%s value_a=%r value_b=%r",
            assertion.field,
            row_a.value,
            row_b.value,
        )
        return None, "malformed_value"

    relation = rule.relation(parsed_a, parsed_b)
    text = _render_claim_text(assertion.subject_a, row_a, assertion.subject_b, row_b, relation)
    claim = DigestClaim(
        id=new_id(),
        text=text,
        citation_snapshot_ids=[row_a.snapshot_id, row_b.snapshot_id],
        validation_status="pending",
    )
    return claim, None


def compare_subjects(
    rows: list[FactRow],
    *,
    call_fn: Callable[[str, str], ComparisonResponse] | None = None,
) -> list[DigestClaim]:
    """rows: build with build_fact_table(). The model proposes WHAT to
    compare; every assertion is resolved to a claim from real, looked-up
    values — an assertion that fails any check is dropped and logged,
    never "corrected". Reversed-pair duplicates (same two subjects, same
    field, either order) are deduplicated: only the first occurrence is
    resolved, later ones are dropped and logged without being re-checked
    (their outcome would be identical — resolution depends only on the
    table, not on assertion order)."""
    index = _index_rows(rows)
    system, user_template = load_prompt("compare_subjects")
    prompt = render(user_template, fact_table=_format_table(rows))

    call = call_fn or _default_call
    response = call(system, prompt)

    claims: list[DigestClaim] = []
    seen_keys: set[tuple[tuple[SubjectKey, SubjectKey], str]] = set()
    for assertion in response.assertions:
        key = _dedupe_key(assertion)
        if key in seen_keys:
            logger.info("comparison_duplicate_dropped field=%s pair=%s", assertion.field, key[0])
            continue
        seen_keys.add(key)

        claim, reason = _resolve_assertion(assertion, index)
        if claim is None:
            logger.warning(
                "comparison_rejected reason=%s field=%s subject_a=%r subject_b=%r",
                reason,
                assertion.field,
                assertion.subject_a,
                assertion.subject_b,
            )
            continue

        claims.append(claim)
        logger.info("comparison_accepted text=%r", claim.text)

    return claims
