"""Citation validation — the "Validate" step (schema/citation checks)
before a digest can auto-publish. Runs in code, not the prompt, per
docs/ARCHITECTURE.md: "Unsupported or contradictory claims are never
automatically published." validate_digest() can only ever veto a publish
(force status to "review"); it never sets "published" itself — that's
publish_digest()'s job, and only once nothing was vetoed.
"""

from __future__ import annotations

from ai_daily_digest.shared.schemas import Digest, DigestClaim


def validate_claim(claim: DigestClaim, known_snapshot_ids: set[str]) -> DigestClaim:
    """A claim is "supported" only if it has at least one citation and
    every cited snapshot id actually exists — a claim with zero citations
    is never supported, regardless of how the text reads."""
    is_supported = bool(claim.citation_snapshot_ids) and all(
        sid in known_snapshot_ids for sid in claim.citation_snapshot_ids
    )
    return claim.model_copy(
        update={"validation_status": "supported" if is_supported else "unsupported"}
    )


def validate_digest(digest: Digest, known_snapshot_ids: set[str]) -> Digest:
    """Validates every claim. If any claim is unsupported, the digest's
    status is forced to "review" regardless of what it was — this never
    upgrades a digest to "published" on its own."""
    validated_claims = [validate_claim(claim, known_snapshot_ids) for claim in digest.claims]
    has_unsupported = any(c.validation_status == "unsupported" for c in validated_claims)
    status = "review" if has_unsupported else digest.status
    return digest.model_copy(update={"claims": validated_claims, "status": status})


def publish_digest(digest: Digest, known_snapshot_ids: set[str]) -> Digest:
    """The only place a Digest is allowed to become "published". Runs
    validation first; if anything is unsupported, the digest goes to
    "review" instead, per the contract's publish gate."""
    validated = validate_digest(digest, known_snapshot_ids)
    if any(c.validation_status != "supported" for c in validated.claims):
        return validated.model_copy(update={"status": "review"})
    return validated.model_copy(update={"status": "published"})
