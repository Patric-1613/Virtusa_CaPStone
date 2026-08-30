"""Typed boundary for resolving a snapshot id to its real content — ADR
0005's `SnapshotResolver`. Lives in `shared/`, not `intelligence/`,
deliberately: a protocol ingestion is eventually expected to provide a
real implementation of cannot live inside an intelligence-private
module, or ingestion would end up depending on an intelligence-owned
type, backwards from the intended module boundary. Mirrors the
`Loader`/`FixtureLoader`/`StoreLoader` split already established in
`intelligence/loaders.py` in shape, but the interface itself is a real
cross-module contract, which is why it's here instead.

Synchronous, deliberately, for this phase (ADR 0005 Decision point 3): a
future database-backed implementation may need to be asynchronous --
that is a separate design decision this module does not make or
preclude.

`validate.py`'s final publish gate (`publish_digest`/`validate_digest`,
reached through `daily_run.py`) requires a real `SnapshotResolver`
instance, not `None` -- existence of a snapshot id is never treated as
proof its content supports a claim, and a caller with no resolver at all
must not be able to silently fall back to that weaker check at the point
that actually authorizes publication. `graph.py`'s per-item `validate`
node is the one exception: it never authorizes publication by itself
(see that node's own docstring), so it's allowed to keep running without
a resolver.
"""

from __future__ import annotations

from typing import Protocol

from ai_daily_digest.shared.schemas import DocumentSnapshot


class SnapshotResolver(Protocol):
    """The one method every implementation must provide -- look up a
    snapshot's real content by id, or None if this resolver doesn't have
    it. None deliberately does not mean "the snapshot doesn't exist"; it
    means "this resolver can't provide its content right now" (e.g. it's
    outside the current batch) -- validate.py treats that as "can't
    verify", not "verified absent"."""

    def get_content(self, snapshot_id: str) -> DocumentSnapshot | None: ...


class InMemorySnapshotResolver:
    """Interim implementation, backed by a plain dict -- the seam a real
    ingestion-store-backed resolver plugs into later without changing
    validate.py's dependency shape at all, only which concrete resolver
    gets constructed. Takes a defensive copy of any dict passed in at
    construction, so mutating the caller's own dict afterward doesn't
    leak into this resolver's view -- the only way to add a snapshot
    after construction is through add() below, which is how
    daily_run.py grows the resolver across a batch, one item at a time.
    """

    def __init__(self, snapshots_by_id: dict[str, DocumentSnapshot] | None = None) -> None:
        self._snapshots_by_id: dict[str, DocumentSnapshot] = dict(snapshots_by_id or {})

    def get_content(self, snapshot_id: str) -> DocumentSnapshot | None:
        return self._snapshots_by_id.get(snapshot_id)

    def add(self, snapshot: DocumentSnapshot) -> None:
        """Register one more snapshot's content."""
        self._snapshots_by_id[snapshot.id] = snapshot
