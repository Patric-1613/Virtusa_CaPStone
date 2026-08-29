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
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ai_daily_digest.intelligence.assemble_digest import assemble_digest
from ai_daily_digest.intelligence.change_sets import build_change_sets
from ai_daily_digest.intelligence.compare_subjects import (
    ComparisonResponse,
    build_fact_table,
    compare_subjects,
)
from ai_daily_digest.intelligence.extract_facts import FactExtractionResponse
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.graph import PipelineState, build_graph
from ai_daily_digest.intelligence.resolve import SubjectAlias
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse
from ai_daily_digest.shared.attributes import COMPARABLE_FIELDS
from ai_daily_digest.shared.schemas import (
    Change,
    ChangeSet,
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
    # ChangeSet aggregates built from this run's Changes (change_sets.py)
    # -- ready for whatever persistence layer picks them up next; nothing
    # downstream of run_daily() currently persists them, the same way
    # FixtureLoader/StoreLoader's load_change_sets() implies some other
    # layer owns storage, not intelligence.
    change_sets: list[ChangeSet] = field(default_factory=list)


@dataclass
class _BatchAccumulator:
    """Everything the per-item loop in run_daily() builds up, one place
    instead of seven separate local variables — what keeps run_daily()
    itself readable at a glance. See _process_item() for how one item's
    result is folded in."""

    claims: list[DigestClaim] = field(default_factory=list)
    all_changes: list[Change] = field(default_factory=list)
    resolved_subjects: list[Subject] = field(default_factory=list)
    # Subject is hashable (frozen, see shared/schemas.py) -- this set
    # mirrors resolved_subjects purely so membership checks below are
    # O(1) instead of a list scan repeated for every item in the batch.
    seen_subjects: set[Subject] = field(default_factory=set)
    unresolved_item_ids: list[str] = field(default_factory=list)
    failed_item_ids: list[str] = field(default_factory=list)
    # Real DocumentSnapshot content for this batch, keyed by id -- passed
    # to assemble_digest()/validate.py so the final publish gate can
    # check that a claim's asserted numbers are actually grounded in the
    # snapshot content it cites, not just that the citation id exists.
    # See validate.py's module docstring for why this is only available
    # for the current batch, not every historical snapshot.
    snapshots_by_id: dict[str, DocumentSnapshot] = field(default_factory=dict)


def _never_auto_publish_comparisons(digest: Digest, comparison_claim_ids: set[str]) -> Digest:
    """INTERIM SAFETY POLICY (per review): compare_subjects()'s numeric
    grounding check only catches a claim that states a specific WRONG
    number -- it does nothing for a qualitative/relational claim like
    "OpenAI is cheaper than Anthropic" (no number to check at all) or a
    claim that swaps which number belongs to which subject (both numbers
    are real, just attributed backwards -- see compare_subjects.py's own
    docstring for the exact gap). Until a structured, relation-aware
    comparison check exists, no cross-subject comparison claim may cause
    a digest to auto-publish -- it can still be part of a "review"
    digest, same as any other unsupported claim, but never nudge a
    digest into "published" on its own. Mirrors validate.py's own rule
    that a check can only ever veto a publish, never grant one."""
    if comparison_claim_ids and digest.status == "published":
        return digest.model_copy(update={"status": "review"})
    return digest


def _process_item(
    entry: BatchItem,
    graph: CompiledStateGraph[PipelineState, Any, PipelineState, PipelineState],
    known_snapshot_ids: set[str],
    acc: _BatchAccumulator,
) -> None:
    """Runs the per-item graph for one BatchItem and folds its result
    into `acc`. A raising graph.invoke() (a transient model failure, a
    malformed response that exhausts call_structured's retry, ...) is
    caught deliberately broadly: whatever failed, this one item's
    failure must not silently discard the whole batch's already-computed
    claims -- recorded in acc.failed_item_ids and logged with a
    traceback instead, so it's still diagnosable, not swallowed."""
    known_snapshot_ids.add(entry.snapshot.id)
    acc.snapshots_by_id[entry.snapshot.id] = entry.snapshot
    try:
        result = graph.invoke({"item": entry.item, "snapshot": entry.snapshot})
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("daily_run_item_failed item_id=%s", entry.item.id)
        acc.failed_item_ids.append(entry.item.id)
        return

    subject = result.get("subject")
    if subject is None:
        acc.unresolved_item_ids.append(entry.item.id)
        return
    if subject not in acc.seen_subjects:
        acc.seen_subjects.add(subject)
        acc.resolved_subjects.append(subject)
    acc.claims.extend(result.get("claims", []))
    acc.all_changes.extend(result.get("changes", []))


def run_daily(  # pylint: disable=too-many-arguments,too-many-locals
    # 4 required (the batch to run and where to accumulate state) + 6
    # optional keyword-only DI/config params -- this is the orchestrator's
    # public entry point, and the *_call_fn params exist specifically so
    # every LLM call site downstream stays independently testable (see
    # this file's other tests' pattern); collapsing them into one options
    # object would just move the same surface area, not reduce it. The
    # local-variable count pylint also flags here is dominated by these
    # same 10 parameters (they count toward it too), not by hidden
    # complexity in the function body -- see _process_item/_BatchAccumulator
    # just above for where the actual per-item logic was already
    # extracted out to keep this function itself short.
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

    Every Change produced by the batch is also grouped into ChangeSet
    aggregates (see change_sets.py) and returned on the result —
    previously nothing did this, and every Change left with an empty,
    never-assigned `change_set_id`.

    INTERIM SAFETY POLICY: no cross-subject comparison claim (from
    compare_subjects()) may cause the digest to auto-publish, regardless
    of its own validation status -- see _never_auto_publish_comparisons().
    """
    graph = build_graph(
        store,
        alias_table=alias_table,
        resolve_llm_call_fn=resolve_llm_call_fn,
        extract_call_fn=extract_call_fn,
    )

    acc = _BatchAccumulator()
    for entry in batch:
        _process_item(entry, graph, known_snapshot_ids, acc)

    comparison_claim_ids: set[str] = set()
    fields = comparison_fields if comparison_fields is not None else list(COMPARABLE_FIELDS)
    if fields and len(acc.resolved_subjects) >= 2:
        try:
            rows = build_fact_table(store, acc.resolved_subjects, fields)
            comparison_claims = compare_subjects(rows, call_fn=compare_call_fn)
            comparison_claim_ids = {c.id for c in comparison_claims}
            acc.claims.extend(comparison_claims)
        except Exception:  # pylint: disable=broad-exception-caught
            # Same principle as _process_item: a comparison failure
            # shouldn't cost every per-item claim already gathered above.
            logger.exception("daily_run_comparison_failed")

    digest = assemble_digest(
        digest_date,
        acc.claims,
        known_snapshot_ids=known_snapshot_ids,
        snapshots_by_id=acc.snapshots_by_id,
        title=title,
    )
    digest = _never_auto_publish_comparisons(digest, comparison_claim_ids)

    return DailyRunResult(
        digest=digest,
        resolved_subjects=acc.resolved_subjects,
        unresolved_item_ids=acc.unresolved_item_ids,
        failed_item_ids=acc.failed_item_ids,
        change_sets=build_change_sets(acc.all_changes),
    )
