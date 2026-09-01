# 0006 — "Unknown" vs. "not disclosed" are different claims

Status: Proposed
Date: 2026-08-27

## Context

`FactRow.value = None` (in `compare_subjects.py::build_fact_table()`) currently means "not
disclosed" per its own docstring — this conflates two genuinely different states:

- **Unknown**: no extraction has ever found a value for this (subject, field). The default,
  silent absence of information. Nothing should be *claimed* about it.
- **Not disclosed**: the source explicitly states the fact is being withheld (e.g. "pricing has
  not yet been announced"). This is itself a groundable claim — it needs its own citation, the
  same as any other extracted fact.

Today's code effectively treats every missing value as "not disclosed" and lets `compare_subjects`
render sentences like "Anthropic has not disclosed its price" from nothing but an absent row — an
ungrounded claim in exactly the sense the rest of this review has been about, just for an absence
instead of a presence.

Split out of [ADR 0005](0005-structured-comparison-and-snapshot-resolution.md) on review feedback:
this distinction is not required for Phase 1 of that ADR (comparison scoped to
`context_window_tokens` only, where "not disclosed" is a realistic, common case worth handling
correctly, but not urgently blocking) and deserves its own focused decision rather than expanding
0005's scope.

## Decision (proposed, not yet implemented or approved)

"Not disclosed" becomes its own extractable, groundable fact state rather than a default inferred
from silence:

```python
# shared/schemas.py — illustrative; exact shape TBD during implementation
class ExtractedFact(BaseModel):
    ...
    value: str | None  # the disclosed value, when there is one
    disclosure_status: Literal["disclosed", "not_disclosed"] = "disclosed"
```

Invalid states that must be actively prevented, not just documented:

- `disclosure_status="not_disclosed"` **without** grounded evidence (`quoted_span` citing the
  actual non-disclosure statement) — the same evidence requirement ADR 0004 established for a
  disclosed value applies here; "not disclosed" needs a citation too, not a default.
- `disclosure_status="not_disclosed"` **together with** a non-null `value` — contradictory: a
  fact can't simultaneously state a value and claim none was given.

`FactRow.value` in `build_fact_table()` stays `None` for the genuinely-unknown case (no
`ExtractedFact` row exists at all) — comparison/claim rendering must distinguish "no row → say
nothing about this side" from "row says `disclosure_status="not_disclosed"` with real evidence →
render the explicit non-disclosure sentence." Only the latter may ever produce "has not disclosed"
text.

## Consequences (anticipated — this ADR is not yet accepted)

- Requires enumerating every call site that currently treats `value is None` as "not disclosed"
  and updating each to the unknown/not-disclosed distinction — at minimum
  `compare_subjects.py::build_fact_table()`/`_format_table()` and any future comparison-rendering
  code from ADR 0005.
- `ExtractedFact` gains a field (additive, same discipline as ADR 0004) — needs its own contract
  test updates and fixture updates.
- Blocked on this ADR being reviewed and accepted before implementation, per the team's
  contract-change process (`docs/API_CONTRACT.md`) — same gate as ADR 0004 and ADR 0005.
