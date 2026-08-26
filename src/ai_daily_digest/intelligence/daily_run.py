"""Daily-run orchestrator — the piece that threads the per-item graph
(`graph.py`), cross-subject comparison (`compare_subjects.py`), and
digest assembly (`assemble_digest.py`) together into one real run over a
batch of new items. Not itself a LangGraph node or graph: it's the loop
that invokes the (already-compiled) per-item graph once per item, then a
single cross-subject comparison pass, then assembles everything into one
Digest.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ai_daily_digest.intelligence.assemble_digest import assemble_digest
from ai_daily_digest.intelligence.compare_subjects import (
    ComparisonResponse,
    build_fact_table,
    compare_subjects,
)
from ai_daily_digest.intelligence.extract_facts import FactExtractionResponse
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.graph import build_graph
from ai_daily_digest.intelligence.resolve import SubjectAlias
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse
from ai_daily_digest.shared.attributes import COMPARABLE_FIELDS
from ai_daily_digest.shared.schemas import (
    Digest,
    DigestClaim,
    DocumentSnapshot,
    SourceItem,
    Subject,
)

logger = logging.getLogger("intelligence.daily_run")


@dataclass
class BatchItem:
    item: SourceItem
    snapshot: DocumentSnapshot


@dataclass
class DailyRunResult:
    digest: Digest
    resolved_subjects: list[Subject] = field(default_factory=list)
    unresolved_item_ids: list[str] = field(default_factory=list)
    failed_item_ids: list[str] = field(default_factory=list)


def run_daily(
    store: FactStore,
    known_snapshot_ids: set[str],
    batch: list[BatchItem],
    digest_date: str,
    *,
    comparison_fields: list[str] | None = None,
    alias_table: list[SubjectAlias] | None = None,
    resolve_llm_call_fn: Callable[[str, str], ResolveLLMResponse] | None = None,
    extract_call_fn: Callable[[str, str], FactExtractionResponse] | None = None,
    compare_call_fn: Callable[[str, str], ComparisonResponse] | None = None,
    title: str | None = None,
) -> DailyRunResult:
    """Runs today's batch through the per-item graph (one `graph.invoke`
    per item, sharing one compiled graph and one `store`), then a single
    cross-subject comparison pass over whichever subjects the batch
    actually touched, then assembles everything into one Digest.

    `store` and `known_snapshot_ids` are both mutated / owned across
    calls the same way — the caller threads the same two objects into
    tomorrow's run so history and citation validity both carry over. An
    item that fails to resolve (even after the LLM fallback) is recorded
    in `unresolved_item_ids`, not silently dropped. An item whose
    processing *raises* (a transient model failure, a malformed response
    that exhausts `call_structured`'s retry, ...) is recorded in
    `failed_item_ids` and the rest of the batch still runs — one item's
    failure must not cost every other item's already-computed claims for
    the day, the same principle ingestion applies to its own sources.
    """
    graph = build_graph(
        store,
        alias_table=alias_table,
        resolve_llm_call_fn=resolve_llm_call_fn,
        extract_call_fn=extract_call_fn,
    )

    claims: list[DigestClaim] = []
    resolved_subjects: list[Subject] = []
    # Subject is hashable (frozen, see shared/schemas.py) -- this set
    # mirrors resolved_subjects purely so membership checks below are
    # O(1) instead of a list scan repeated for every item in the batch.
    seen_subjects: set[Subject] = set()
    unresolved_item_ids: list[str] = []
    failed_item_ids: list[str] = []

    for entry in batch:
        known_snapshot_ids.add(entry.snapshot.id)
        try:
            result = graph.invoke({"item": entry.item, "snapshot": entry.snapshot})
        except Exception:
            # Deliberately broad: whatever failed (network, validation
            # exhaustion, an unexpected bug in a node), this one item's
            # failure must not silently discard the whole batch's
            # already-computed claims. Logged with a traceback so it's
            # still diagnosable, not swallowed.
            logger.exception("daily_run_item_failed item_id=%s", entry.item.id)
            failed_item_ids.append(entry.item.id)
            continue
        subject = result.get("subject")
        if subject is None:
            unresolved_item_ids.append(entry.item.id)
            continue
        if subject not in seen_subjects:
            seen_subjects.add(subject)
            resolved_subjects.append(subject)
        claims.extend(result.get("claims", []))

    fields = comparison_fields if comparison_fields is not None else list(COMPARABLE_FIELDS)
    if fields and len(resolved_subjects) >= 2:
        try:
            rows = build_fact_table(store, resolved_subjects, fields)
            claims.extend(compare_subjects(rows, call_fn=compare_call_fn))
        except Exception:
            # Same principle as the per-item loop: a comparison failure
            # shouldn't cost every per-item claim already gathered above.
            logger.exception("daily_run_comparison_failed")

    digest = assemble_digest(
        digest_date, claims, known_snapshot_ids=known_snapshot_ids, title=title
    )

    return DailyRunResult(
        digest=digest,
        resolved_subjects=resolved_subjects,
        unresolved_item_ids=unresolved_item_ids,
        failed_item_ids=failed_item_ids,
    )
