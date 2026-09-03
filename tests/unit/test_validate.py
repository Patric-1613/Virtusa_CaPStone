import uuid
from datetime import UTC, datetime

import pytest

from ai_daily_digest.intelligence.validate import publish_digest, validate_claim, validate_digest
from ai_daily_digest.shared.schemas import Digest, DigestClaim, DigestStatus, DocumentSnapshot
from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver
from tests.uuid_samples import (
    CLAIM_1,
    CLAIM_2,
    DIGEST_1,
    ITEM_1,
    SNAPSHOT_1,
    SNAPSHOT_2,
    SNAPSHOT_MISSING,
)

KNOWN_SNAPSHOTS = {SNAPSHOT_1, SNAPSHOT_2}


def _claim(
    citations: list[uuid.UUID], claim_id: uuid.UUID = CLAIM_1, text: str = "Some claim."
) -> DigestClaim:
    return DigestClaim(id=claim_id, text=text, citation_snapshot_ids=citations)


def _snapshot(snap_id: uuid.UUID, text: str) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id=ITEM_1,
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def _resolver(*snap_ids: uuid.UUID) -> InMemorySnapshotResolver:
    """A resolver that can actually resolve each of the given ids --
    content is irrelevant for these tests (no numbers in the claim text
    to ground), only resolvability itself matters post-blocker-2 (see
    validate.py's fail-closed-on-unresolvable-citations fix)."""
    return InMemorySnapshotResolver({sid: _snapshot(sid, "content") for sid in snap_ids})


def test_claim_with_valid_citations_is_supported() -> None:
    claim = validate_claim(_claim([SNAPSHOT_1]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "supported"


def test_claim_with_unknown_snapshot_is_unsupported() -> None:
    claim = validate_claim(_claim([SNAPSHOT_MISSING]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "unsupported"


def test_claim_with_no_citations_is_unsupported() -> None:
    claim = validate_claim(_claim([]), KNOWN_SNAPSHOTS)
    assert claim.validation_status == "unsupported"


def test_validate_digest_forces_review_on_any_unsupported_claim() -> None:
    digest = Digest(
        id=DIGEST_1,
        digest_date="2026-08-20",
        status=DigestStatus.DRAFT,
        title="Test digest",
        claims=[_claim([SNAPSHOT_1], CLAIM_1), _claim([SNAPSHOT_MISSING], CLAIM_2)],
    )
    validated = validate_digest(digest, KNOWN_SNAPSHOTS, snapshot_resolver=_resolver(SNAPSHOT_1))
    assert validated.status == "review"
    statuses = {c.id: c.validation_status for c in validated.claims}
    assert statuses == {CLAIM_1: "supported", CLAIM_2: "unsupported"}


def test_validate_digest_never_upgrades_status_on_its_own() -> None:
    digest = Digest(
        id=DIGEST_1,
        digest_date="2026-08-20",
        status=DigestStatus.DRAFT,
        title="Test digest",
        claims=[_claim([SNAPSHOT_1], CLAIM_1)],
    )
    validated = validate_digest(digest, KNOWN_SNAPSHOTS, snapshot_resolver=_resolver(SNAPSHOT_1))
    assert validated.status == "draft"  # all supported, but validate_digest doesn't publish


def test_validate_digest_requires_a_snapshot_resolver() -> None:
    """ADR 0005: the final gate must never silently degrade to
    existence-only checking just because a resolver wasn't passed --
    proven here by the call itself failing, not by behavior."""
    digest = Digest(
        id=DIGEST_1,
        digest_date="2026-08-20",
        status=DigestStatus.DRAFT,
        title="Test digest",
        claims=[_claim([SNAPSHOT_1], CLAIM_1)],
    )
    with pytest.raises(TypeError):
        validate_digest(digest, KNOWN_SNAPSHOTS)  # type: ignore[call-arg]


def test_publish_digest_publishes_only_when_everything_supported() -> None:
    digest = Digest(
        id=DIGEST_1,
        digest_date="2026-08-20",
        status=DigestStatus.DRAFT,
        title="Test digest",
        claims=[_claim([SNAPSHOT_1], CLAIM_1), _claim([SNAPSHOT_2], CLAIM_2)],
    )
    published = publish_digest(
        digest, KNOWN_SNAPSHOTS, snapshot_resolver=_resolver(SNAPSHOT_1, SNAPSHOT_2)
    )
    assert published.status == "published"


def test_publish_digest_routes_to_review_instead_of_publishing() -> None:
    digest = Digest(
        id=DIGEST_1,
        digest_date="2026-08-20",
        status=DigestStatus.DRAFT,
        title="Test digest",
        claims=[_claim([], CLAIM_1)],
    )
    result = publish_digest(digest, KNOWN_SNAPSHOTS, snapshot_resolver=InMemorySnapshotResolver())
    assert result.status == "review"


def test_publish_digest_requires_a_snapshot_resolver() -> None:
    """Same requirement as validate_digest() -- publish_digest is the
    actual point that authorizes "published", so it must not be callable
    without a real resolver either."""
    digest = Digest(
        id=DIGEST_1,
        digest_date="2026-08-20",
        status=DigestStatus.DRAFT,
        title="Test digest",
        claims=[_claim([SNAPSHOT_1], CLAIM_1)],
    )
    with pytest.raises(TypeError):
        publish_digest(digest, KNOWN_SNAPSHOTS)  # type: ignore[call-arg]


# --- content grounding (snapshot_resolver) ---
#
# Adversarial case per the review: existence of a citation id was never
# enough on its own -- a claim can cite a real snapshot id that has
# nothing to do with what the claim actually says. These tests only
# exercise that when the caller actually has a SnapshotResolver to check
# against -- the default, content-less behavior above is unchanged for
# callers that don't (validate_claim() only -- graph.py's per-item node).


def test_claim_grounded_in_cited_snapshot_content_is_supported() -> None:
    claim = _claim([SNAPSHOT_1], text="The context window increased to 256000 tokens.")
    resolver = InMemorySnapshotResolver(
        {SNAPSHOT_1: _snapshot(SNAPSHOT_1, "Context window increased to 256,000.")}
    )
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshot_resolver=resolver)
    assert validated.validation_status == "supported"


def test_claim_citing_a_real_but_unrelated_snapshot_is_unsupported() -> None:
    """The exact fabrication case: SNAPSHOT_1 is a real, known snapshot id
    -- the existence-only check alone would call this "supported" -- but
    its actual content never mentions the number the claim asserts."""
    claim = _claim([SNAPSHOT_1], text="The context window increased to 999999 tokens.")
    resolver = InMemorySnapshotResolver(
        {SNAPSHOT_1: _snapshot(SNAPSHOT_1, "Context window increased to 256,000.")}
    )
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshot_resolver=resolver)
    assert validated.validation_status == "unsupported"


def test_claim_with_no_numbers_is_unaffected_by_content_grounding() -> None:
    """Nothing numeric to check -- content grounding has nothing to
    verify, so existence is still all that applies."""
    claim = _claim([SNAPSHOT_1], text="Anthropic has not disclosed this field.")
    resolver = InMemorySnapshotResolver(
        {SNAPSHOT_1: _snapshot(SNAPSHOT_1, "Completely unrelated.")}
    )
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshot_resolver=resolver)
    assert validated.validation_status == "supported"


def test_number_free_claim_with_an_unresolvable_citation_fails_closed() -> None:
    """Fourth review, blocker 2: resolution must be checked BEFORE the
    "no numbers, nothing to verify" shortcut -- a number-free claim
    (e.g. "Anthropic has not disclosed its price") citing a snapshot the
    resolver can't actually produce is not "vacuously fine" just because
    there's no number to check. There is no way to confirm that citation
    is even real content, so it must fail closed exactly like a numeric
    claim with an unresolvable citation would -- an empty resolver here
    means content for SNAPSHOT_1 can't be resolved even though it exists
    in known_snapshot_ids."""
    claim = _claim([SNAPSHOT_1], text="Anthropic has not disclosed its price.")
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshot_resolver=InMemorySnapshotResolver())
    assert validated.validation_status == "unsupported"


def test_citation_unresolvable_by_the_resolver_is_unsupported_not_trusted() -> None:
    """Corrected per the third review: a citation id that exists in
    known_snapshot_ids but the resolver can't provide content for (e.g. a
    Change's previous-value snapshot from an earlier day's run, outside
    the current batch) can't be content-verified here -- existence of the
    id is NOT treated as proof of support. The claim routes to review
    (validation_status="unsupported") rather than being trusted on the
    strength of an id alone. This means routine multi-day claims need
    review more often until a real SnapshotResolver can retrieve
    historical content -- an accepted, deliberate cost, not a bug."""
    claim = _claim([SNAPSHOT_2], text="The price is 5.")
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshot_resolver=InMemorySnapshotResolver())
    assert validated.validation_status == "unsupported"


def test_claim_partially_grounded_and_partially_missing_content_is_unsupported() -> None:
    """One cited snapshot has content available (and would fail the
    content check on its own), the other doesn't -- per the fail-closed
    policy, an incomplete citation set is never enough to call a claim
    supported, whether or not the content we DO have would have passed."""
    claim = _claim([SNAPSHOT_1, SNAPSHOT_2], text="The price is 999999.")
    resolver = InMemorySnapshotResolver({SNAPSHOT_1: _snapshot(SNAPSHOT_1, "The price is 5.")})
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshot_resolver=resolver)
    assert validated.validation_status == "unsupported"


def test_content_grounding_does_not_change_behavior_when_omitted() -> None:
    """Callers that only have ids (e.g. graph.py's per-item validate
    node) keep the existence-only check -- passing no snapshot_resolver
    at all must not be treated as "nothing is grounded"."""
    claim = _claim([SNAPSHOT_1], text="Any number like 999999 here.")
    validated = validate_claim(claim, KNOWN_SNAPSHOTS)
    assert validated.validation_status == "supported"


def test_resolver_returning_none_is_treated_as_unresolvable_not_absent() -> None:
    """A resolver's get_content() returning None means "can't provide
    this right now", never "confirmed this snapshot doesn't exist" --
    validate.py must treat it the same as a missing key, not specially."""

    class _AlwaysNoneResolver:
        def get_content(self, snapshot_id: uuid.UUID) -> DocumentSnapshot | None:
            return None

    claim = _claim([SNAPSHOT_1], text="The price is 5.")
    validated = validate_claim(claim, KNOWN_SNAPSHOTS, snapshot_resolver=_AlwaysNoneResolver())
    assert validated.validation_status == "unsupported"
