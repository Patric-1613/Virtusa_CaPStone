import uuid
from datetime import UTC, date, datetime

import pytest

from ai_daily_digest.intelligence.assemble_digest import assemble_digest
from ai_daily_digest.shared.schemas import DigestClaim, DocumentSnapshot
from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver
from tests.uuid_samples import CLAIM_1, CLAIM_2, ITEM_1, SNAPSHOT_1, SNAPSHOT_2, SNAPSHOT_MISSING

KNOWN_SNAPSHOTS = {SNAPSHOT_1, SNAPSHOT_2}


def _claim(citations: list[uuid.UUID], claim_id: uuid.UUID = CLAIM_1) -> DigestClaim:
    return DigestClaim(id=claim_id, text="Some claim.", citation_snapshot_ids=citations)


def _snapshot(snap_id: uuid.UUID) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id=ITEM_1,
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash=f"sha256:{snap_id}",
        content_text="content",
    )


def _resolver(*snap_ids: uuid.UUID) -> InMemorySnapshotResolver:
    """A resolver that can actually resolve each of the given ids --
    content is irrelevant for these tests (no numbers in "Some claim."
    to ground), only resolvability itself matters post-fourth-review
    (validate.py now fails closed on any unresolvable citation, numbers
    or not)."""
    return InMemorySnapshotResolver({sid: _snapshot(sid) for sid in snap_ids})


def test_empty_claims_stays_draft_not_published() -> None:
    digest = assemble_digest(
        date(2026, 8, 20),
        [],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
    )
    assert digest.status == "draft"
    assert digest.claims == []


def test_all_supported_claims_publishes() -> None:
    digest = assemble_digest(
        date(2026, 8, 20),
        [_claim([SNAPSHOT_1], CLAIM_1), _claim([SNAPSHOT_2], CLAIM_2)],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=_resolver(SNAPSHOT_1, SNAPSHOT_2),
    )
    assert digest.status == "published"
    assert {c.validation_status for c in digest.claims} == {"supported"}


def test_any_unsupported_claim_routes_to_review_but_keeps_all_claims() -> None:
    digest = assemble_digest(
        date(2026, 8, 20),
        [_claim([SNAPSHOT_1], CLAIM_1), _claim([SNAPSHOT_MISSING], CLAIM_2)],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=_resolver(SNAPSHOT_1),
    )
    assert digest.status == "review"
    assert len(digest.claims) == 2  # unsupported claim kept, not dropped
    statuses = {c.id: c.validation_status for c in digest.claims}
    assert statuses == {CLAIM_1: "supported", CLAIM_2: "unsupported"}


def test_default_title_includes_the_date() -> None:
    digest = assemble_digest(
        date(2026, 8, 20),
        [_claim([SNAPSHOT_1])],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
    )
    assert "2026-08-20" in digest.title


def test_custom_title_is_used_verbatim() -> None:
    digest = assemble_digest(
        date(2026, 8, 20),
        [_claim([SNAPSHOT_1])],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
        title="Custom Title",
    )
    assert digest.title == "Custom Title"


def test_digest_date_and_ids_are_set() -> None:
    digest = assemble_digest(
        date(2026, 8, 20),
        [_claim([SNAPSHOT_1])],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        snapshot_resolver=InMemorySnapshotResolver(),
    )
    assert digest.digest_date == date(2026, 8, 20)
    assert digest.id


def test_snapshot_resolver_is_required() -> None:
    """ADR 0005: assemble_digest() is daily_run.py's only path to
    publish_digest -- it must not be callable without a real resolver
    just because the compile-time check alone was skipped."""
    with pytest.raises(TypeError):
        assemble_digest(  # type: ignore[call-arg]
            date(2026, 8, 20), [_claim([SNAPSHOT_1])], known_snapshot_ids=KNOWN_SNAPSHOTS
        )
