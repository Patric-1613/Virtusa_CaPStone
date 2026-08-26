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
  2. Content grounding (only when `snapshots_by_id` is supplied) — every
     number the claim's text asserts must actually appear in the content
     of the snapshot(s) it cites. This is what catches a claim citing a
     real, legitimate snapshot id while stating a value that snapshot's
     text never actually contains. See intelligence/grounding.py for
     what this check does and does not prove — it is not a full semantic
     entailment/contradiction check, only a numeric-grounding floor.

`snapshots_by_id` is optional and defaults to None (existence-only,
the prior behavior) because not every caller has full DocumentSnapshot
content on hand — e.g. graph.py's per-item `validate` node only has the
current item's snapshot, not the full content of every historical
snapshot a Change's `previous` might cite (FactStore only retains
extracted values, not raw snapshot text). daily_run.py/assemble_digest.py
DO have every batch snapshot's content and pass it through, so the
content-grounding check runs at the point that matters most: the final
gate before a digest can auto-publish.

KNOWN LIMITATION, interim behavior (flagged in review, not silently
accepted): `snapshots_by_id` only ever covers the CURRENT batch. A
routine multi-day change (e.g. "increased to X, up from Y") legitimately
cites both a current-batch snapshot AND an earlier one from a previous
day's run — `daily_run.py` doesn't carry snapshot content across days
the way it carries `known_snapshot_ids`. Requiring full content for
every citation would make ordinary historical claims wrongly
"unsupported" the moment a Change spans more than one batch. The interim
policy below only runs the content check when EVERY cited snapshot's
content is actually available; a claim with any citation outside the
current batch falls back to the existence-only check instead of being
punished for a plumbing gap. This is deliberately conservative in the
other direction: it under-checks multi-day claims rather than
over-rejecting legitimate ones. A real fix (a snapshot-content store
that persists across daily runs, not just the current batch) is a
separate, larger change — not made here.
"""

from __future__ import annotations

from ai_daily_digest.intelligence.grounding import numbers_in
from ai_daily_digest.shared.schemas import Digest, DigestClaim, DocumentSnapshot


def _claim_numbers_are_grounded(
    claim: DigestClaim, snapshots_by_id: dict[str, DocumentSnapshot]
) -> bool:
    """Every number claim.text asserts must appear in the combined
    content of its cited snapshots -- but only when content for EVERY
    cited snapshot is actually available (see this module's docstring on
    why a partial/missing citation falls back to trusting it rather than
    failing the claim). A claim asserting no numbers at all (e.g.
    "Anthropic has not disclosed its price") has nothing this check can
    verify either way, so it passes."""
    claim_numbers = numbers_in(claim.text)
    if not claim_numbers:
        return True
    if not all(sid in snapshots_by_id for sid in claim.citation_snapshot_ids):
        return True
    combined_text = " ".join(
        snapshots_by_id[sid].content_text or "" for sid in claim.citation_snapshot_ids
    )
    return claim_numbers <= numbers_in(combined_text)


def validate_claim(
    claim: DigestClaim,
    known_snapshot_ids: set[str],
    *,
    snapshots_by_id: dict[str, DocumentSnapshot] | None = None,
) -> DigestClaim:
    """A claim is "supported" only if it has at least one citation, every
    cited snapshot id actually exists, and — when `snapshots_by_id` is
    supplied — every number it asserts is grounded in those snapshots'
    actual content. A claim with zero citations is never supported,
    regardless of how the text reads."""
    is_supported = bool(claim.citation_snapshot_ids) and all(
        sid in known_snapshot_ids for sid in claim.citation_snapshot_ids
    )
    if is_supported and snapshots_by_id is not None:
        is_supported = _claim_numbers_are_grounded(claim, snapshots_by_id)
    return claim.model_copy(
        update={"validation_status": "supported" if is_supported else "unsupported"}
    )


def validate_digest(
    digest: Digest,
    known_snapshot_ids: set[str],
    *,
    snapshots_by_id: dict[str, DocumentSnapshot] | None = None,
) -> Digest:
    """Validates every claim. If any claim is unsupported, the digest's
    status is forced to "review" regardless of what it was — this never
    upgrades a digest to "published" on its own."""
    validated_claims = [
        validate_claim(claim, known_snapshot_ids, snapshots_by_id=snapshots_by_id)
        for claim in digest.claims
    ]
    has_unsupported = any(c.validation_status == "unsupported" for c in validated_claims)
    status = "review" if has_unsupported else digest.status
    return digest.model_copy(update={"claims": validated_claims, "status": status})


def publish_digest(
    digest: Digest,
    known_snapshot_ids: set[str],
    *,
    snapshots_by_id: dict[str, DocumentSnapshot] | None = None,
) -> Digest:
    """The only place a Digest is allowed to become "published". Runs
    validation first; if anything is unsupported, the digest goes to
    "review" instead, per the contract's publish gate."""
    validated = validate_digest(digest, known_snapshot_ids, snapshots_by_id=snapshots_by_id)
    if any(c.validation_status != "supported" for c in validated.claims):
        return validated.model_copy(update={"status": "review"})
    return validated.model_copy(update={"status": "published"})
