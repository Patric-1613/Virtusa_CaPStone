"""LangGraph wiring for the per-item intelligence pipeline: Classify →
Extract → Compare → Draft → Validate, per docs/ARCHITECTURE.md's
intelligence workflow diagram. Every node below is a thin wrapper around
an already-built, already-tested function (resolve.py, resolve_llm.py,
facts.py, extract_facts.py, draft_claims.py, validate.py) — this file
only owns how state passes between them, never business logic of its
own. That ordering (nodes built and tested standalone first, graph
wiring last) is deliberate — see intelligence/CLAUDE.md's Orchestration
section.

Note on "Retrieve related history": the workflow diagram shows it as a
separate step, but here it's folded into classify (reads
`store.known_subjects()`) and compare (`FactStore.update_fact` reads the
stored prior value) rather than getting its own node — `FactStore` *is*
the retrieval mechanism, so a standalone "retrieve" node would just be
reading the same store a second time for nothing.

Scope: this graph processes ONE new SourceItem + its DocumentSnapshot at
a time, producing zero or more validated DigestClaims. Assembling a full
day's Digest from many items' claims, and the cross-subject
compare_subjects() step, are not wired into this graph yet — see
docs/LLM_AGENT_SPECS.md's "Not yet built" section.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai_daily_digest.intelligence.draft_claims import draft_change_claim
from ai_daily_digest.intelligence.extract_facts import FactExtractionResponse, extract_facts
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.resolve import (
    ResolutionResult,
    SubjectAlias,
    load_alias_table,
    resolve_deterministic,
)
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse, resolve_via_llm
from ai_daily_digest.intelligence.validate import validate_claim
from ai_daily_digest.shared.schemas import (
    Change,
    DigestClaim,
    DocumentSnapshot,
    ExtractedFact,
    SourceItem,
    Subject,
)


class PipelineState(TypedDict, total=False):
    item: SourceItem
    snapshot: DocumentSnapshot
    resolution: ResolutionResult
    subject: Subject | None
    facts: list[ExtractedFact]
    changes: list[Change]
    claims: list[DigestClaim]


def build_graph(
    store: FactStore,
    *,
    alias_table: list[SubjectAlias] | None = None,
    resolve_llm_call_fn: Callable[[str, str], ResolveLLMResponse] | None = None,
    extract_call_fn: Callable[[str, str], FactExtractionResponse] | None = None,
) -> CompiledStateGraph[PipelineState, Any, PipelineState, PipelineState]:
    """store: the FactStore this pipeline reads from and writes to —
    shared across invocations so history accumulates run over run, the
    same way the real system will use one long-lived store (or, later,
    the real database via StoreLoader). alias_table/*_call_fn are
    injectable, primarily for tests — see tests/unit/test_graph.py for
    running this without hitting the real Anthropic API.
    """
    resolved_alias_table = alias_table if alias_table is not None else load_alias_table()

    def classify_deterministic(state: PipelineState) -> PipelineState:
        """Entry node. Tries the cheap, auditable path first — candidates
        come from store.known_subjects(), i.e. what this store has
        already seen, plus the checked-in alias table."""
        item = state["item"]
        text = state["snapshot"].content_text or ""
        result = resolve_deterministic(
            item, store.known_subjects(), resolved_alias_table, item_text=text
        )
        return {"resolution": result, "subject": result.subject}

    def classify_llm(state: PipelineState) -> PipelineState:
        """Only reached when classify_deterministic didn't get a clean
        single match (see route_after_classify) — resolves against
        whichever candidates the deterministic pass narrowed it to."""
        prior = state["resolution"]
        result = resolve_via_llm(
            state["item"],
            prior.candidate_subjects,
            item_text=state["snapshot"].content_text or "",
            call_fn=resolve_llm_call_fn,
        )
        return {"resolution": result, "subject": result.subject}

    def route_after_classify(state: PipelineState) -> str:
        """The only branch point before the LLM fallback: a clean
        "alias_match" skips straight to extraction; "no_match" or
        "ambiguous" both need the LLM to adjudicate."""
        return "extract" if state["resolution"].method == "alias_match" else "classify_llm"

    def route_after_llm(state: PipelineState) -> str:
        """If even the LLM fallback couldn't resolve a subject, there is
        nothing further this item can contribute — end the run for it
        here rather than letting extract/compare/draft run on subject=None."""
        return "extract" if state["subject"] is not None else "end"

    def extract(state: PipelineState) -> PipelineState:
        """Runs the LLM fact extractor against this item's snapshot text.
        The RuntimeError below should be unreachable — route_after_llm
        guarantees a resolved subject before this node runs — but fails
        loudly instead of silently extracting nothing if that guarantee
        is ever broken by a future edit."""
        subject = state["subject"]
        if subject is None:
            raise RuntimeError("extract reached with no resolved subject — routing bug")
        facts = extract_facts(subject, state["snapshot"], call_fn=extract_call_fn)
        return {"facts": facts}

    def compare(state: PipelineState) -> PipelineState:
        """Deterministic — no LLM call. Feeds each extracted fact through
        FactStore.update_fact(), which is where "did this actually
        change" gets decided; only real changes are collected here."""
        subject = state["subject"]
        if subject is None:
            raise RuntimeError("compare reached with no resolved subject — routing bug")
        snapshot = state["snapshot"]
        changes: list[Change] = []
        for fact in state.get("facts", []):
            change = store.update_fact(
                subject,
                fact,
                source_url=str(state["item"].canonical_url),
                observed_at=snapshot.fetched_at,
            )
            if change is not None:
                changes.append(change)
        return {"changes": changes}

    def draft(state: PipelineState) -> PipelineState:
        """Deterministic — no LLM call. One DigestClaim per Change; a run
        with no changes produces no claims, which is correct, not empty
        output to worry about."""
        claims = [draft_change_claim(change) for change in state.get("changes", [])]
        return {"claims": claims}

    def validate(state: PipelineState) -> PipelineState:
        """Deterministic — no LLM call. known_snapshot_ids here is scoped
        to just this one item's run (its own snapshot plus whatever
        snapshot ids its changes cite) — daily_run.py's known_snapshot_ids
        is the caller-owned superset spanning every run, used again at
        the final assemble_digest() step."""
        known_snapshot_ids = {state["snapshot"].id}
        for change in state.get("changes", []):
            if change.previous is not None and change.previous.snapshot_id:
                known_snapshot_ids.add(change.previous.snapshot_id)
            if change.current.snapshot_id:
                known_snapshot_ids.add(change.current.snapshot_id)
        validated = [validate_claim(c, known_snapshot_ids) for c in state.get("claims", [])]
        return {"claims": validated}

    graph = StateGraph(PipelineState)
    graph.add_node("classify_deterministic", classify_deterministic)
    graph.add_node("classify_llm", classify_llm)
    graph.add_node("extract", extract)
    graph.add_node("compare", compare)
    graph.add_node("draft", draft)
    graph.add_node("validate", validate)

    graph.set_entry_point("classify_deterministic")
    graph.add_conditional_edges(
        "classify_deterministic",
        route_after_classify,
        {"extract": "extract", "classify_llm": "classify_llm"},
    )
    graph.add_conditional_edges("classify_llm", route_after_llm, {"extract": "extract", "end": END})
    graph.add_edge("extract", "compare")
    graph.add_edge("compare", "draft")
    graph.add_edge("draft", "validate")
    graph.add_edge("validate", END)

    return graph.compile()
