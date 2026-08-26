# intelligence — Person B

Owns: fact retrieval/comparison state (`facts.py`), subject resolution,
fact extraction, deterministic claim drafting, citation validation, the
evaluation harness (not yet built), and email rendering/sending (not yet
built). See `README.md` in this directory for the one-line upstream
description, and `docs/API_CONTRACT.md` for the real shared contract —
there is no "Entity" model; this module resolves to `Subject`
(company + product) and produces `Change`/`ExtractedFact`/`DigestClaim`.

Read the root `AGENTS.md` and `CLAUDE.md` first for repo-wide rules. This
file is the local rules for everything under
`src/ai_daily_digest/intelligence/` and `tests/{unit,contract}/` paths
that cover it.

## Non-negotiable rules

- **Never let a model infer a fact value it can't see in context.** An
  absent field is simply omitted — never a null placeholder guessed from
  general knowledge.
- **Every `DigestClaim` needs a citation resolvable to a real snapshot
  id.** Enforce this in code (`validate.py`), never rely on the prompt
  alone to behave.
- **`quoted_span` must actually appear in the source text** — checked in
  code (`extract_facts.py`), a fabricated span is silently dropped, not
  silently stored.
- **A false subject merge is worse than a miss.** Assert on it explicitly
  in `tests/unit/test_resolve.py`; low-confidence LLM resolutions are
  never auto-merged (`resolve_llm.py`'s 0.6 threshold).
- Change detection compares against **stored history**, never a
  recomputed value — `facts.py::FactStore` builds `Change.previous` from
  what was actually recorded, and `field_history()` is append-only.
- A `Digest` can only reach `status="published"` via `validate.py::publish_digest`,
  and only when every claim is supported — never set that status by hand
  elsewhere.

## Model choices

| Call site | Model | Notes |
|---|---|---|
| `resolve_llm.py` | Haiku 4.5 | Only runs on the residue after deterministic alias matching fails/is ambiguous. Constrained JSON, temperature 0. Confidence < 0.6 → logged for review, never auto-merged. |
| `extract_facts.py` | Sonnet 5 | Must quote an exact source span, not paraphrase — Haiku tends to paraphrase, which breaks the grounding check. |
| `compare_subjects.py` | Sonnet 5 | Reads the fact table only, never raw article text. Sparse data → abstention, enforced in code (unknown subject/field/snapshot citation all get dropped). |

`facts.py`'s change detection and `draft_claims.py`'s single-field claim
drafting are deliberately **not** LLM calls — see
`docs/ARCHITECTURE.md`: "Prefer deterministic code ... use an LLM only
where deterministic rules are insufficient."

All LLM call sites go through `intelligence/llm.py::call_structured` —
one wrapper, so retries, logging, and model swaps happen in one place.
Don't call the Anthropic SDK directly from elsewhere in `intelligence/`.

## Orchestration

Per-item flow (classify → extract → compare → draft → validate) is a
**LangGraph** `StateGraph` in `intelligence/graph.py::build_graph`, per
`docs/ARCHITECTURE.md`'s technology baseline (still status: Proposed) —
each stage a node, state passed explicitly, every node that calls the
model still routing through `call_structured`. The individual functions
(`resolve_deterministic`, `resolve_via_llm`, `FactStore.update_fact`,
`extract_facts`, `draft_change_claim`, `compare_subjects`,
`validate_digest`/`publish_digest`) were built and tested standalone
first — nodes before the graph shell, so graph wiring only combined
already-proven logic instead of debugging both at once. See
`docs/LLM_AGENT_SPECS.md`'s Orchestration section for the exact node/edge
layout. `compare_subjects` isn't wired into the graph yet — it's a
cross-item, cross-subject step, a different shape from the current
one-item-at-a-time graph.

## Structured output discipline

Request JSON conforming to the relevant response model → validate → on
failure, retry once with the validation error appended to the prompt →
on second failure, fail loudly (`StructuredCallFailedError`). No silent
fallback to prose, ever.

## Prompts

Live in `intelligence/prompts/*.txt`, referenced by filename (not inlined
as strings) so prompt changes show up cleanly in diffs and in eval logs.
Agent-level specs (input/output contract, guardrails, failure mode) for
each call site are in `docs/LLM_AGENT_SPECS.md` — update both together.

## Testing

- `tests/fixtures/contracts/` is the real fixture pack location (per
  `docs/TEAM_WORKFLOW.md`/`docs/ARCHITECTURE.md` Milestone 0) — currently
  a small solo draft, not the team's ratified 20+/malformed/prompt-
  injection pack; see its own README before treating it as canonical.
  Never hand-edit it to make a test pass once it's the real thing — fix
  the code or prompt instead.
- Contract tests in `tests/contract/` (marked `pytest.mark.contract`)
  protect the shared models — anything that changes `shared/schemas.py`
  must keep them passing.
- LLM call sites (`resolve_llm.py`, `extract_facts.py`) take an
  injectable `call_fn` specifically so tests don't need a real API key or
  network call — use that pattern for new call sites too.
- Log the exact context bundle sent to the model (`intelligence/llm.py`
  does this by default) — it's the debugging surface when a claim looks
  wrong days later.
