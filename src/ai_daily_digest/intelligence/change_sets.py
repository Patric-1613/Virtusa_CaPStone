"""Groups a batch's Changes into ChangeSet aggregates — the gap the
review flagged: nothing in production code built a ChangeSet, so every
Change left `daily_run.py` with `change_set_id=""` (facts.py's
`update_fact()` deliberately leaves this to "the caller grouping Changes
into a ChangeSet", per its own docstring; this module is that caller).
"""

from __future__ import annotations

from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import Change, ChangeSet, Subject


def build_change_sets(changes: list[Change]) -> list[ChangeSet]:
    """One ChangeSet per subject that had at least one Change in this
    batch. Each Change in the result is a copy of the input with
    `change_set_id` backfilled to point at its new aggregate — the
    caller's original `changes` list (and anything already derived from
    it, e.g. drafted DigestClaims) is left untouched, since DigestClaim
    cites snapshot ids directly and never references change_set_id.

    previous_snapshot_ids/current_snapshot_ids are deduped, order-
    preserving, and skip Changes with no recorded snapshot id (a Change's
    `previous` is None for a first disclosure — nothing to add there).
    """
    grouped: dict[Subject, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.subject, []).append(change)

    change_sets: list[ChangeSet] = []
    for subject, subject_changes in grouped.items():
        change_set_id = new_id()
        previous_snapshot_ids: list[str] = []
        current_snapshot_ids: list[str] = []
        linked_changes: list[Change] = []
        for change in subject_changes:
            linked_changes.append(change.model_copy(update={"change_set_id": change_set_id}))
            prev_id = change.previous.snapshot_id if change.previous else None
            if prev_id and prev_id not in previous_snapshot_ids:
                previous_snapshot_ids.append(prev_id)
            current_id = change.current.snapshot_id
            if current_id and current_id not in current_snapshot_ids:
                current_snapshot_ids.append(current_id)
        change_sets.append(
            ChangeSet(
                id=change_set_id,
                subject=subject,
                changes=linked_changes,
                previous_snapshot_ids=previous_snapshot_ids,
                current_snapshot_ids=current_snapshot_ids,
            )
        )
    return change_sets
