from ai_daily_digest.intelligence.assemble_digest import assemble_digest
from ai_daily_digest.shared.schemas import DigestClaim

KNOWN_SNAPSHOTS = {"snap_1", "snap_2"}


def _claim(citations, claim_id="c1"):
    return DigestClaim(id=claim_id, text="Some claim.", citation_snapshot_ids=citations)


def test_empty_claims_stays_draft_not_published():
    digest = assemble_digest("2026-08-20", [], known_snapshot_ids=KNOWN_SNAPSHOTS)
    assert digest.status == "draft"
    assert digest.claims == []


def test_all_supported_claims_publishes():
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"], "c1"), _claim(["snap_2"], "c2")],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
    )
    assert digest.status == "published"
    assert {c.validation_status for c in digest.claims} == {"supported"}


def test_any_unsupported_claim_routes_to_review_but_keeps_all_claims():
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"], "c1"), _claim(["snap_missing"], "c2")],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
    )
    assert digest.status == "review"
    assert len(digest.claims) == 2  # unsupported claim kept, not dropped
    statuses = {c.id: c.validation_status for c in digest.claims}
    assert statuses == {"c1": "supported", "c2": "unsupported"}


def test_default_title_includes_the_date():
    digest = assemble_digest("2026-08-20", [_claim(["snap_1"])], known_snapshot_ids=KNOWN_SNAPSHOTS)
    assert "2026-08-20" in digest.title


def test_custom_title_is_used_verbatim():
    digest = assemble_digest(
        "2026-08-20",
        [_claim(["snap_1"])],
        known_snapshot_ids=KNOWN_SNAPSHOTS,
        title="Custom Title",
    )
    assert digest.title == "Custom Title"


def test_digest_date_and_ids_are_set():
    digest = assemble_digest("2026-08-20", [_claim(["snap_1"])], known_snapshot_ids=KNOWN_SNAPSHOTS)
    assert digest.digest_date == "2026-08-20"
    assert digest.id
