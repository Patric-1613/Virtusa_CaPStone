# 0006 — "Unknown" vs. "not disclosed" are different claims

Status: Accepted by Persons A and B; Person C confirmation pending
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

## Decision

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

## Consequences

- Every call site that previously treated `value is None` as "not disclosed" is updated to the
  unknown/not-disclosed distinction — `compare_subjects.py::build_fact_table()`/`_format_table()`/
  `_resolve_assertion()` (ADR 0005's comparison-rendering code). `intelligence/facts.py::FactStore`
  and `draft_claims.py` are deliberately NOT extended with new disclosure-transition wording as
  part of this ADR: a "not disclosed" `ExtractedFact` is now recorded (so `get_current_fact()`/
  `build_fact_table()` can see it), but `FactStore.update_fact()` does not turn a disclosure-status
  transition (either side lacking a value) into a `Change`/`DigestClaim` — the same treatment a
  first observation already gets, and for the same reason: nothing downstream has an agreed wording
  for that sentence yet, and inventing one silently here would be exactly the kind of ungrounded-by-
  default rendering this ADR exists to prevent. That remains open for its own follow-up.
- `ExtractedFact` gains a field (additive, same discipline as ADR 0004) — contract tests, fixtures,
  and `docs/API_CONTRACT.md` updated accordingly.
- Accepted by Persons A and B; Person C's confirmation is still pending, per the team's
  contract-change process (`docs/API_CONTRACT.md`) — same gate as ADR 0004 and ADR 0005. Not
  blocked on that confirmation before implementation starts, matching how ADR 0005 was handled.
