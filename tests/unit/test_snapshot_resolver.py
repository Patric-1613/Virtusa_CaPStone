"""ADR 0005's SnapshotResolver -- InMemorySnapshotResolver is the interim
implementation daily_run.py/evaluate.py build; these tests cover its
actual contract (hit/miss, add(), and non-leaking construction), not
just that it compiles."""

import uuid
from datetime import UTC, datetime

import pytest

from ai_daily_digest.shared.schemas import DocumentSnapshot
from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver
from tests.uuid_samples import ITEM_1, SNAPSHOT_1, SNAPSHOT_2, SNAPSHOT_MISSING

TSR_SNAP_REAL_ID = uuid.UUID("01a01e4a-9740-7e82-a26f-408ca1c5007a")


def _snapshot(snap_id: uuid.UUID, text: str = "content") -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id=ITEM_1,
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def test_get_content_returns_a_snapshot_passed_in_at_construction() -> None:
    snap = _snapshot(SNAPSHOT_1, "hello world")
    resolver = InMemorySnapshotResolver({SNAPSHOT_1: snap})
    assert resolver.get_content(SNAPSHOT_1) == snap


def test_get_content_returns_none_for_an_unknown_id() -> None:
    resolver = InMemorySnapshotResolver({SNAPSHOT_1: _snapshot(SNAPSHOT_1)})
    assert resolver.get_content(SNAPSHOT_MISSING) is None


def test_empty_resolver_resolves_nothing() -> None:
    resolver = InMemorySnapshotResolver()
    assert resolver.get_content(SNAPSHOT_1) is None


def test_add_makes_a_snapshot_resolvable() -> None:
    resolver = InMemorySnapshotResolver()
    assert resolver.get_content(SNAPSHOT_1) is None
    snap = _snapshot(SNAPSHOT_1)
    resolver.add(snap)
    assert resolver.get_content(SNAPSHOT_1) == snap


def test_add_keys_by_the_snapshots_own_id_not_a_caller_supplied_key() -> None:
    """add() takes only a DocumentSnapshot -- it must key by snap.id
    itself, not trust anything else, so a caller can't accidentally
    register a snapshot under the wrong id."""
    resolver = InMemorySnapshotResolver()
    resolver.add(_snapshot(TSR_SNAP_REAL_ID))
    assert resolver.get_content(TSR_SNAP_REAL_ID) is not None


def test_constructor_takes_a_defensive_copy_of_the_dict_passed_in() -> None:
    """Mutating the caller's own dict after construction must not leak
    into the resolver's view -- the only way to add a snapshot afterward
    is through add()."""
    source = {SNAPSHOT_1: _snapshot(SNAPSHOT_1)}
    resolver = InMemorySnapshotResolver(source)
    source[SNAPSHOT_2] = _snapshot(SNAPSHOT_2)
    assert resolver.get_content(SNAPSHOT_2) is None


def test_add_does_not_leak_back_into_the_dict_originally_passed_in() -> None:
    source = {SNAPSHOT_1: _snapshot(SNAPSHOT_1)}
    resolver = InMemorySnapshotResolver(source)
    resolver.add(_snapshot(SNAPSHOT_2))
    assert SNAPSHOT_2 not in source


def test_re_adding_the_identical_snapshot_is_an_idempotent_no_op() -> None:
    """DocumentSnapshot is documented as immutable -- re-registering the
    exact same content under the same id (e.g. daily_run.py revisiting
    the same batch item, or two callers sharing a resolver) must not be
    treated as a conflict."""
    resolver = InMemorySnapshotResolver()
    snap = _snapshot(SNAPSHOT_1, "hello world")
    resolver.add(snap)
    resolver.add(_snapshot(SNAPSHOT_1, "hello world"))  # same id, same content
    assert resolver.get_content(SNAPSHOT_1) == snap


def test_adding_a_different_snapshot_under_an_existing_id_raises() -> None:
    """A DIFFERENT snapshot registered under an id already present is a
    real conflict, not a routine update -- silently overwriting would let
    a second snapshot invalidate whatever already cited the first one's
    content under that id, so this must raise rather than replace."""
    resolver = InMemorySnapshotResolver()
    resolver.add(_snapshot(SNAPSHOT_1, "original content"))
    with pytest.raises(ValueError, match=str(SNAPSHOT_1)):
        resolver.add(_snapshot(SNAPSHOT_1, "different content"))
    # the original registration must survive the rejected conflict
    assert resolver.get_content(SNAPSHOT_1).content_text == "original content"  # type: ignore[union-attr]
