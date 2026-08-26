from datetime import UTC, datetime

from ai_daily_digest.intelligence.validate import publish_digest, validate_claim, validate_digest
from ai_daily_digest.shared.schemas import Digest, DigestClaim, DocumentSnapshot

KNOWN_SNAPSHOTS = {"snap_1", "snap_2"}


def _claim(
    citations: list[str], claim_id: str = "claim_1", text: str = "Some claim."
) -> DigestClaim:
    return DigestClaim(id=claim_id, text=text, citation_snapshot_ids=citations)


def _snapshot(snap_id: str, text: str) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id="item_1",
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def test_claim_with_valid_citations_is_supported() -> None:
    claim = validate_claim(_claim(["snap_1"]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "supported"


def test_claim_with_unknown_snapshot_is_unsupported() -> None:
    claim = validate_claim(_claim(["snap_does_not_exist"]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "unsupported"


def test_claim_with_no_citations_is_unsupported() -> None:
    claim = validate_claim(_claim([]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "unsupported"


def test_validate_digest_forces_review_on_any_unsupported_claim() -> None:
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


def test_validate_digest_never_upgrades_status_on_its_own() -> None:
    digest = Digest(
        id="d2",
        digest_date="2026-08-20",
        status="draft",
        title="Test digest",
        claims=[_claim(["snap_1"], "c1")],
    )
    validated = validate_digest(digest, KNOWN_SNAPSHOTS)
    assert validated.status == "draft"  # all supported, but validate_digest doesn't publish


def test_publish_digest_publishes_only_when_everything_supported() -> None:
    digest = Digest(
        id="d3",
        digest_date="2026-08-20",
        status="draft",
        title="Test digest",
        claims=[_claim(["snap_1"], "c1"), _claim(["snap_2"], "c2")],
    )
    published = publish_digest(digest, KNOWN_SNAPSHOTS)
    assert published.status == "published"


def test_publish_digest_routes_to_review_instead_of_publishing() -> None:
    digest = Digest(
        id="d4",
        digest_date="2026-08-20",
        status="draft",
        title="Test digest",
        claims=[_claim([], "c1")],
    )
    result = publish_digest(digest, KNOWN_SNAPSHOTS)
    assert result.status == "review"


# --- content grounding (snapshots_by_id) ---
#
# Adversarial case per the review: existence of a citation id was never
# enough on its own -- a claim can cite a real snapshot id that has
# nothing to do with what the claim actually says. These tests only
# exercise that when the caller actually has snapshot content to check
# against (snapshots_by_id) -- the default, content-less behavior above
# is unchanged for callers that don't.


def test_claim_grounded_in_cited_snapshot_content_is_supported() -> None:
    claim = _claim(["snap_1"], text="The context window increased to 256000 tokens.")
    snapshots_by_id = {"snap_1": _snapshot("snap_1", "Context window increased to 256,000.")}
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshots_by_id=snapshots_by_id)
    assert validated.validation_status == "supported"


def test_claim_citing_a_real_but_unrelated_snapshot_is_unsupported() -> None:
    """The exact fabrication case: snap_1 is a real, known snapshot id --
    the existence-only check alone would call this "supported" -- but its
    actual content never mentions the number the claim asserts."""
    claim = _claim(["snap_1"], text="The context window increased to 999999 tokens.")
    snapshots_by_id = {"snap_1": _snapshot("snap_1", "Context window increased to 256,000.")}
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshots_by_id=snapshots_by_id)
    assert validated.validation_status == "unsupported"


def test_claim_with_no_numbers_is_unaffected_by_content_grounding() -> None:
    """Nothing numeric to check -- content grounding has nothing to
    verify, so existence is still all that applies."""
    claim = _claim(["snap_1"], text="Anthropic has not disclosed this field.")
    snapshots_by_id = {"snap_1": _snapshot("snap_1", "Completely unrelated content.")}
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshots_by_id=snapshots_by_id)
    assert validated.validation_status == "supported"


def test_citation_missing_from_snapshots_by_id_falls_back_to_existence_only() -> None:
    """A citation id that exists in known_snapshot_ids but isn't in
    snapshots_by_id (e.g. a Change's previous-value snapshot from an
    earlier day's run, outside the current batch) can't be
    content-verified here -- rather than punishing every routine
    multi-day claim as "unsupported", it falls back to the existence-only
    check per this module's documented interim policy. See
    test_claim_citing_a_real_but_unrelated_snapshot_is_unsupported for
    the case this does NOT weaken: when content IS available, it's still
    enforced."""
    claim = _claim(["snap_2"], text="The price is 5.")
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshots_by_id={})
    assert validated.validation_status == "supported"


def test_claim_partially_grounded_and_partially_missing_content_falls_back_too() -> None:
    """One cited snapshot has content available (and would fail the
    content check on its own), the other doesn't -- per the "every cited
    snapshot's content must be available, or don't run the content check
    at all" policy, this whole claim falls back to existence-only rather
    than a partial check that could be gamed by pairing a bad citation
    with an out-of-batch one."""
    claim = _claim(["snap_1", "snap_2"], text="The price is 999999.")
    snapshots_by_id = {"snap_1": _snapshot("snap_1", "The price is 5.")}
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshots_by_id=snapshots_by_id)
    assert validated.validation_status == "supported"


def test_content_grounding_does_not_change_behavior_when_omitted() -> None:
    """Callers that only have ids (e.g. graph.py's per-item validate
    node) keep the existence-only check -- passing no snapshots_by_id at
    all must not be treated as "nothing is grounded"."""
    claim = _claim(["snap_1"], text="Any number like 999999 here.")
    validated = validate_claim(claim, KNOWN_SNAPSHOTS)
    assert validated.validation_status == "supported"
