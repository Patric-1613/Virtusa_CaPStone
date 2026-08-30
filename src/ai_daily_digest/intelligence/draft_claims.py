"""Deterministic claim drafting — the "Draft digest claims" step for
mechanical, single-field changes. docs/ARCHITECTURE.md: "Prefer
deterministic code for URL normalization, hashing, dates, numeric
comparison, citation validation, and deduplication. Use an LLM ... only
where deterministic rules are insufficient." A field going from one value
to another is exactly the case deterministic code should own; free-form
prose comparisons across *different* subjects are a separate, model-
backed step (not built yet — see docs/LLM_AGENT_SPECS.md).
"""

from __future__ import annotations

from ai_daily_digest.intelligence.facts import change_snapshot_ids
from ai_daily_digest.shared.attributes import field_label as _field_label_for
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import Change, DigestClaim


def draft_change_claim(change: Change) -> DigestClaim:
    """One DigestClaim per Change. validation_status starts "pending" —
    intelligence/validate.py is what's allowed to mark it supported."""
    subject_name = f"{change.subject.company}'s {change.subject.product}"
    label = _field_label_for(change.field)

    if change.previous is None:
        text = f"{subject_name}'s {label} is now disclosed as {change.current.value}."
    elif change.change_type == "increased":
        text = (
            f"{subject_name}'s {label} increased to {change.current.value}, "
            f"up from {change.previous.value}."
        )
    elif change.change_type == "decreased":
        text = (
            f"{subject_name}'s {label} decreased to {change.current.value}, "
            f"down from {change.previous.value}."
        )
    else:
        text = (
            f"{subject_name}'s {label} changed from {change.previous.value} "
            f"to {change.current.value}."
        )

    current_id, previous_id = change_snapshot_ids(change)
    citation_ids = [current_id] if current_id else []
    if previous_id:
        citation_ids.append(previous_id)

    return DigestClaim(
        id=new_id(),
        text=text,
        citation_snapshot_ids=citation_ids,
        validation_status="pending",
    )
