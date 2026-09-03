"""Citation validation — the "Validate" step (schema/citation checks)
before a digest can auto-publish. Runs in code, not the prompt, per
docs/ARCHITECTURE.md: "Unsupported or contradictory claims are never
automatically published." validate_digest() can only ever veto a publish
(force status to "review"); it never sets "published" itself — that's
publish_digest()'s job, and only once nothing was vetoed.

Two independent checks, both required for "supported":
  1. Existence — every cited snapshot id actually exists. This alone is
     what the review flagged as insufficient: a claim can cite a real,
     existing snapshot id that has nothing to do with what the claim
     actually says.
  2. Content grounding (only when `snapshot_resolver` is supplied) — two
     parts, in order: (a) every cited snapshot's content must actually be
     resolvable at all -- fails closed even for a claim with no numbers
     in it, since an unresolvable citation is never distinguishable from
     a fabricated one; (b) for a claim that does assert numbers, every
     one of them must actually appear in the content of the snapshot(s)
     it cites. (b) is what catches a claim citing a real, legitimate
     snapshot id while stating a value that snapshot's text never
     actually contains. See intelligence/grounding.py for what this
     check does and does not prove — it is not a full semantic
     entailment/contradiction check, only a numeric-grounding floor.

ADR 0005: `snapshot_resolver` is a typed `SnapshotResolver`
(`shared/snapshot_resolver.py`), not a raw dict — the seam a real
ingestion-store-backed resolver plugs into later. `validate_claim()`
keeps it optional (default None, existence-only) because not every
caller has one on hand — e.g. graph.py's per-item `validate` node only
has the current item's snapshot, not the full content of every
historical snapshot a Change's `previous` might cite (FactStore only
retains extracted values, not raw snapshot text), and that node never
authorizes publication by itself anyway (see its own docstring).
`validate_digest()`/`publish_digest()` — reached only from daily_run.py,
the actual final gate before a digest can auto-publish — REQUIRE a real
resolver: existence of a snapshot id is never treated as proof its
content supports a claim, and the point that actually authorizes
publication must not be able to silently fall back to the weaker check.

KNOWN LIMITATION: a `SnapshotResolver` only ever covers what its backing
store actually has. The `InMemorySnapshotResolver` daily_run.py builds
only covers the CURRENT batch — a routine multi-day change (e.g.
"increased to X, up from Y") legitimately cites both a current-batch
snapshot AND an earlier one from a previous day's run, which that
resolver can't provide. If content for ANY cited snapshot can't be
resolved, the claim is NOT supported — it routes to "review", not
silently trusted and not silently dropped. This means routine multi-day
claims will need review more often than ideal until a resolver backed by
a real, persistent store can actually retrieve historical content — an
accepted cost of not trusting unverifiable citations, not a bug.
"""

from __future__ import annotations

import uuid

from ai_daily_digest.intelligence.grounding import numbers_in
from ai_daily_digest.shared.schemas import ClaimValidationStatus, Digest, DigestClaim, DigestStatus
from ai_daily_digest.shared.snapshot_resolver import SnapshotResolver


def _claim_numbers_are_grounded(claim: DigestClaim, snapshot_resolver: SnapshotResolver) -> bool:
    """Resolution comes FIRST, before even looking at claim.text: if
    content for ANY cited snapshot can't be resolved, the claim is NOT
    grounded, full stop -- existence of a snapshot id is never treated
    as proof its content supports the claim (see this module's
    docstring), and that must hold for a number-free claim exactly the
    same as a numeric one. A claim like "Anthropic has not disclosed its
    price" citing a snapshot the resolver can't actually produce is not
    "vacuously fine" -- there is no way to confirm that citation is even
    real content, so it fails closed like anything else with an
    unresolvable citation. Only once every citation is confirmed
    resolvable does the number check run at all: a claim asserting no
    numbers has nothing further this check can verify, so it passes from
    there; a numeric claim's asserted numbers must appear in the
    combined resolved content."""
    cited_snapshots = [snapshot_resolver.get_content(sid) for sid in claim.citation_snapshot_ids]
    if any(snapshot is None for snapshot in cited_snapshots):
        return False
    claim_numbers = numbers_in(claim.text)
    if not claim_numbers:
        return True
    combined_text = " ".join(
        snapshot.content_text or "" for snapshot in cited_snapshots if snapshot
    )
    return claim_numbers <= numbers_in(combined_text)


def validate_claim(
    claim: DigestClaim,
    known_snapshot_ids: set[uuid.UUID],
    *,
    snapshot_resolver: SnapshotResolver | None = None,
) -> DigestClaim:
    """A claim is "supported" only if it has at least one citation, every
    cited snapshot id actually exists, and — when `snapshot_resolver` is
    supplied — every number it asserts is grounded in those snapshots'
    actual content. A claim with zero citations is never supported,
    regardless of how the text reads."""
    is_supported = bool(claim.citation_snapshot_ids) and all(
        sid in known_snapshot_ids for sid in claim.citation_snapshot_ids
    )
    if is_supported and snapshot_resolver is not None:
        is_supported = _claim_numbers_are_grounded(claim, snapshot_resolver)
    return claim.model_copy(
        update={
            "validation_status": (
                ClaimValidationStatus.SUPPORTED
                if is_supported
                else ClaimValidationStatus.UNSUPPORTED
            )
        }
    )


def validate_digest(
    digest: Digest,
    known_snapshot_ids: set[uuid.UUID],
    *,
    snapshot_resolver: SnapshotResolver,
) -> Digest:
    """Validates every claim. If any claim is unsupported, the digest's
    status is forced to "review" regardless of what it was — this never
    upgrades a digest to "published" on its own.

    `snapshot_resolver` is REQUIRED here (unlike validate_claim()) — this
    is reached only from the batch-level final gate (daily_run.py), which
    must never silently degrade to existence-only checking just because
    it forgot to pass one."""
    validated_claims = [
        validate_claim(claim, known_snapshot_ids, snapshot_resolver=snapshot_resolver)
        for claim in digest.claims
    ]
    has_unsupported = any(
        c.validation_status == ClaimValidationStatus.UNSUPPORTED for c in validated_claims
    )
    status = DigestStatus.REVIEW if has_unsupported else digest.status
    return digest.model_copy(update={"claims": validated_claims, "status": status})


def publish_digest(
    digest: Digest,
    known_snapshot_ids: set[uuid.UUID],
    *,
    snapshot_resolver: SnapshotResolver,
) -> Digest:
    """The only place a Digest is allowed to become "published". Runs
    validation first; if anything is unsupported, the digest goes to
    "review" instead, per the contract's publish gate. `snapshot_resolver`
    is REQUIRED -- see validate_digest()'s docstring."""
    validated = validate_digest(digest, known_snapshot_ids, snapshot_resolver=snapshot_resolver)
    if any(c.validation_status != ClaimValidationStatus.SUPPORTED for c in validated.claims):
        return validated.model_copy(update={"status": DigestStatus.REVIEW})
    return validated.model_copy(update={"status": DigestStatus.PUBLISHED})
