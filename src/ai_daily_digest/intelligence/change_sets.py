"""Groups a batch's Changes into ChangeSet aggregates.

ADR 0007's "Batch-scoped ChangeSet ID allocation": every `Change` this
module receives already carries its final, correct `change_set_id` --
allocated lazily, once per subject per batch, by
`get_or_create_change_set_id()` below, via the `change_set_id_factory`
callback `facts.py::FactStore.update_fact()` calls immediately before
constructing each Change (see that method's own docstring). This module
no longer mints an id or backfills one via `model_copy` -- it groups by
subject and verifies the invariant construction is supposed to already
guarantee: every Change in one subject's group shares one id.
"""

from __future__ import annotations

import uuid

from ai_daily_digest.intelligence.facts import change_snapshot_ids
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import Change, ChangeSet, Subject


def get_or_create_change_set_id(
    change_set_ids: dict[Subject, uuid.UUID], subject: Subject
) -> uuid.UUID:
    """Batch-scoped get-or-create: the first Change for a subject in this
    batch allocates a fresh UUID v7 change_set_id; every later Change for
    the same subject in the same batch reuses it. `change_set_ids` is
    owned by the caller (daily_run.py's `_BatchAccumulator`) and lives
    only as long as one run -- `FactStore` persists across runs and must
    never own this bookkeeping (ADR 0007).

    Deliberately NOT `change_set_ids.setdefault(subject, new_id())`:
    Python evaluates every argument before `setdefault()` runs, so that
    expression calls `new_id()` unconditionally on every invocation --
    generating and silently discarding a fresh UUID even when `subject`
    already has one."""
    existing = change_set_ids.get(subject)
    if existing is not None:
        return existing
    allocated = new_id()
    change_set_ids[subject] = allocated
    return allocated


def build_change_sets(changes: list[Change]) -> list[ChangeSet]:
    """One ChangeSet per subject that had at least one Change in this
    batch. Every Change already carries its final `change_set_id` (see
    this module's own docstring) -- this function groups by subject and
    uses that value directly, verifying every Change in one group agrees
    rather than trusting it silently.

    previous_snapshot_ids/current_snapshot_ids are deduped, order-
    preserving, and skip Changes with no recorded snapshot id (a Change's
    `previous` is None for a first disclosure — nothing to add there).

    Raises ValueError if a subject's Changes carry inconsistent
    change_set_id values -- a corrupted or hand-built input, not
    something the batch-scoped allocator itself can produce (every real
    Change for one subject in one batch is built via the same
    get_or_create_change_set_id() call). Never silently picks the first
    or last id among values that disagree (ADR 0007).
    """
    grouped: dict[Subject, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.subject, []).append(change)

    change_sets: list[ChangeSet] = []
    for subject, subject_changes in grouped.items():
        change_set_id = subject_changes[0].change_set_id
        for change in subject_changes:
            if change.change_set_id != change_set_id:
                raise ValueError(
                    f"Inconsistent change_set_id for subject {subject!r}: "
                    f"{change_set_id!r} vs. {change.change_set_id!r} on Change "
                    f"{change.id!r} -- every Change for one subject in one batch "
                    "must share the same change_set_id (ADR 0007)"
                )

        previous_snapshot_ids: list[uuid.UUID] = []
        current_snapshot_ids: list[uuid.UUID] = []
        for change in subject_changes:
            current_id, prev_id = change_snapshot_ids(change)
            if prev_id is not None and prev_id not in previous_snapshot_ids:
                previous_snapshot_ids.append(prev_id)
            if current_id is not None and current_id not in current_snapshot_ids:
                current_snapshot_ids.append(current_id)
        change_sets.append(
            ChangeSet(
                id=change_set_id,
                subject=subject,
                changes=subject_changes,
                previous_snapshot_ids=previous_snapshot_ids,
                current_snapshot_ids=current_snapshot_ids,
            )
        )
    return change_sets
