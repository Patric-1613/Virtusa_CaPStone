"""Digest assembly — the last intelligence-owned step before delivery
renders/sends a Digest. Collects a day's DigestClaims (from
`graph.py::build_graph` runs over the day's items, plus
`compare_subjects.py` output) into one Digest, then runs it through
`validate.py::publish_digest` — the only place a Digest's status can
become "published".
"""

from __future__ import annotations

from ai_daily_digest.intelligence.validate import publish_digest
from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import Digest, DigestClaim
from ai_daily_digest.shared.snapshot_resolver import SnapshotResolver


def assemble_digest(
    digest_date: str,
    claims: list[DigestClaim],
    *,
    known_snapshot_ids: set[str],
    snapshot_resolver: SnapshotResolver,
    title: str | None = None,
) -> Digest:
    """A digest with zero claims never auto-publishes — there is nothing
    to report, so it stays "draft" rather than becoming a "published"
    digest with an empty claims list. Otherwise the claims are validated
    and the digest published only if every one of them is supported (see
    publish_digest) — unsupported claims are kept in the digest, not
    dropped, so a reviewer can see exactly what failed and why.

    `snapshot_resolver` is REQUIRED (ADR 0005): this is the actual final
    gate before a digest can auto-publish (reached from daily_run.py),
    and existence of a snapshot id must never be silently trusted as
    proof its content supports a claim just because no resolver was
    passed. See validate.py's module docstring.
    """
    digest = Digest(
        id=new_id(),
        digest_date=digest_date,
        status="draft",
        title=title or f"AI Daily Digest — {digest_date}",
        claims=claims,
    )
    if not claims:
        return digest
    return publish_digest(digest, known_snapshot_ids, snapshot_resolver=snapshot_resolver)
