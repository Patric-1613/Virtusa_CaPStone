from ai_daily_digest.intelligence.validate import publish_digest, validate_claim, validate_digest
from ai_daily_digest.shared.schemas import Digest, DigestClaim

KNOWN_SNAPSHOTS = {"snap_1", "snap_2"}


def _claim(citations, claim_id="claim_1"):
    return DigestClaim(id=claim_id, text="Some claim.", citation_snapshot_ids=citations)


def test_claim_with_valid_citations_is_supported():
    claim = validate_claim(_claim(["snap_1"]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "supported"


def test_claim_with_unknown_snapshot_is_unsupported():
    claim = validate_claim(_claim(["snap_does_not_exist"]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "unsupported"


def test_claim_with_no_citations_is_unsupported():
    claim = validate_claim(_claim([]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "unsupported"


def test_validate_digest_forces_review_on_any_unsupported_claim():
    digest = Digest(
        id="d1",
        digest_date="2026-08-20",
        status="draft",
        title="Test digest",
        claims=[_claim(["snap_1"], "c1"), _claim(["snap_missing"], "c2")],
    )
    validated = validate_digest(digest, KNOWN_SNAPSHOTS)
    assert validated.status == "review"
    statuses = {c.id: c.validation_status for c in validated.claims}
    assert statuses == {"c1": "supported", "c2": "unsupported"}


def test_validate_digest_never_upgrades_status_on_its_own():
    digest = Digest(
        id="d2",
        digest_date="2026-08-20",
        status="draft",
        title="Test digest",
        claims=[_claim(["snap_1"], "c1")],
    )
    validated = validate_digest(digest, KNOWN_SNAPSHOTS)
    assert validated.status == "draft"  # all supported, but validate_digest doesn't publish


def test_publish_digest_publishes_only_when_everything_supported():
    digest = Digest(
        id="d3",
        digest_date="2026-08-20",
        status="draft",
        title="Test digest",
        claims=[_claim(["snap_1"], "c1"), _claim(["snap_2"], "c2")],
    )
    published = publish_digest(digest, KNOWN_SNAPSHOTS)
    assert published.status == "published"


def test_publish_digest_routes_to_review_instead_of_publishing():
    digest = Digest(
        id="d4",
        digest_date="2026-08-20",
        status="draft",
        title="Test digest",
        claims=[_claim([], "c1")],
    )
    result = publish_digest(digest, KNOWN_SNAPSHOTS)
    assert result.status == "review"
