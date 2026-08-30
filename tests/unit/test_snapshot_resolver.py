"""ADR 0005's SnapshotResolver -- InMemorySnapshotResolver is the interim
implementation daily_run.py/evaluate.py build; these tests cover its
actual contract (hit/miss, add(), and non-leaking construction), not
just that it compiles."""

from datetime import UTC, datetime

import pytest

from ai_daily_digest.shared.schemas import DocumentSnapshot
from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver


def _snapshot(snap_id: str, text: str = "content") -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id="item_1",
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def test_get_content_returns_a_snapshot_passed_in_at_construction() -> None:
    snap = _snapshot("snap_1", "hello world")
    resolver = InMemorySnapshotResolver({"snap_1": snap})
    assert resolver.get_content("snap_1") == snap


def test_get_content_returns_none_for_an_unknown_id() -> None:
    resolver = InMemorySnapshotResolver({"snap_1": _snapshot("snap_1")})
    assert resolver.get_content("snap_does_not_exist") is None


def test_empty_resolver_resolves_nothing() -> None:
    resolver = InMemorySnapshotResolver()
    assert resolver.get_content("snap_1") is None


def test_add_makes_a_snapshot_resolvable() -> None:
    resolver = InMemorySnapshotResolver()
    assert resolver.get_content("snap_1") is None
    snap = _snapshot("snap_1")
    resolver.add(snap)
    assert resolver.get_content("snap_1") == snap


def test_add_keys_by_the_snapshots_own_id_not_a_caller_supplied_key() -> None:
    """add() takes only a DocumentSnapshot -- it must key by snap.id
    itself, not trust anything else, so a caller can't accidentally
    register a snapshot under the wrong id."""
    resolver = InMemorySnapshotResolver()
    resolver.add(_snapshot("snap_real_id"))
    assert resolver.get_content("snap_real_id") is not None


def test_constructor_takes_a_defensive_copy_of_the_dict_passed_in() -> None:
    """Mutating the caller's own dict after construction must not leak
    into the resolver's view -- the only way to add a snapshot afterward
    is through add()."""
    source = {"snap_1": _snapshot("snap_1")}
    resolver = InMemorySnapshotResolver(source)
    source["snap_2"] = _snapshot("snap_2")
    assert resolver.get_content("snap_2") is None


def test_add_does_not_leak_back_into_the_dict_originally_passed_in() -> None:
    source = {"snap_1": _snapshot("snap_1")}
    resolver = InMemorySnapshotResolver(source)
    resolver.add(_snapshot("snap_2"))
    assert "snap_2" not in source


def test_re_adding_the_identical_snapshot_is_an_idempotent_no_op() -> None:
    """DocumentSnapshot is documented as immutable -- re-registering the
    exact same content under the same id (e.g. daily_run.py revisiting
    the same batch item, or two callers sharing a resolver) must not be
    treated as a conflict."""
    resolver = InMemorySnapshotResolver()
    snap = _snapshot("snap_1", "hello world")
    resolver.add(snap)
    resolver.add(_snapshot("snap_1", "hello world"))  # same id, same content
    assert resolver.get_content("snap_1") == snap


def test_adding_a_different_snapshot_under_an_existing_id_raises() -> None:
    """A DIFFERENT snapshot registered under an id already present is a
    real conflict, not a routine update -- silently overwriting would let
    a second snapshot invalidate whatever already cited the first one's
    content under that id, so this must raise rather than replace."""
    resolver = InMemorySnapshotResolver()
    resolver.add(_snapshot("snap_1", "original content"))
    with pytest.raises(ValueError, match="snap_1"):
        resolver.add(_snapshot("snap_1", "different content"))
    # the original registration must survive the rejected conflict
    assert resolver.get_content("snap_1").content_text == "original content"  # type: ignore[union-attr]
