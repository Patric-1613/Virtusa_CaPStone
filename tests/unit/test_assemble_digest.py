from datetime import UTC, datetime

import pytest

from ai_daily_digest.intelligence.assemble_digest import assemble_digest
from ai_daily_digest.shared.schemas import DigestClaim, DocumentSnapshot
from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver

KNOWN_SNAPSHOTS = {"snap_1", "snap_2"}


def _claim(citations: list[str], claim_id: str = "c1") -> DigestClaim:
    return DigestClaim(id=claim_id, text="Some claim.", citation_snapshot_ids=citations)


def _snapshot(snap_id: str) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id="item_1",
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash=f"sha256:{snap_id}",
        content_text="content",
    )


def _resolver(*snap_ids: str) -> InMemorySnapshotResolver:
    """A resolver that can actually resolve each of the given ids --
    content is irrelevant for these tests (no numbers in "Some claim."
    to ground), only resolvability itself matters post-fourth-review
    (validate.py now fails closed on any unresolvable citation, numbers
    or not)."""
    return InMemorySnapshotResolver({sid: _snapshot(sid) for sid in snap_ids})


def test_empty_claims_stays_draft_not_published() -> None:
    digest = assemble_digest(
        "2026-08-20",
        [],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
    )
    assert digest.status == "draft"
    assert digest.claims == []


def test_all_supported_claims_publishes() -> None:
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"], "c1"), _claim(["snap_2"], "c2")],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=_resolver("snap_1", "snap_2"),
    )
    assert digest.status == "published"
    assert {c.validation_status for c in digest.claims} == {"supported"}


def test_any_unsupported_claim_routes_to_review_but_keeps_all_claims() -> None:
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"], "c1"), _claim(["snap_missing"], "c2")],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=_resolver("snap_1"),
    )
    assert digest.status == "review"
    assert len(digest.claims) == 2  # unsupported claim kept, not dropped
    statuses = {c.id: c.validation_status for c in digest.claims}
    assert statuses == {"c1": "supported", "c2": "unsupported"}


def test_default_title_includes_the_date() -> None:
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"])],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
    )
    assert "2026-08-20" in digest.title


def test_custom_title_is_used_verbatim() -> None:
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"])],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
        title="Custom Title",
    )
    assert digest.title == "Custom Title"


def test_digest_date_and_ids_are_set() -> None:
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"])],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
    )
    assert digest.digest_date == "2026-08-20"
    assert digest.id


def test_snapshot_resolver_is_required() -> None:
    """ADR 0005: assemble_digest() is daily_run.py's only path to
    publish_digest -- it must not be callable without a real resolver
    just because the compile-time check alone was skipped."""
    with pytest.raises(TypeError):
        assemble_digest(  # type: ignore[call-arg]
            "2026-08-20", [_claim(["snap_1"])], known_snapshot_ids=KNOWN_SNAPSHOTS
        )
