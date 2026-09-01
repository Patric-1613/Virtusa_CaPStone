# 0006 — "Unknown" vs. "not disclosed" are different claims

Status: Proposed for peer review — revisions requested by Persons A and C on 2026-09-01
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
- Per the team's contract-change process (`docs/API_CONTRACT.md`), this ADR requires all three
  module owners' sign-off — Persons A, B, **and** C — before its status becomes `Accepted by
  Persons A, B, and C`. Person A's first pass raised the revisions below, requested 2026-09-01;
  Person C's initial review is complete but has not yet re-reviewed this revision. None of this
  ADR's implementation is to be treated as unconditionally accepted or as a precedent for skipping
  any reviewer's sign-off on a future ADR.

## Revisions requested by Person A (2026-09-01)

Person A's review of the first implementation found the design sound but flagged four gaps,
addressed in this revision:

1. **Quote existence is not semantic support for a non-disclosure claim.** The original
   implementation accepted any `not_disclosed` candidate whose `quoted_span` merely appeared in the
   snapshot text (the same grounding check every candidate goes through) — that proves the quote is
   real, not that it actually supports "this specific field is being withheld." Fixed with a
   deterministic check (`extract_facts.py::_quote_supports_non_disclosure`) requiring an approved
   explicit-withholding phrase, a field-matching keyword, and no real number in the quote — mirrors
   `grounding.py::value_supported_by_quote()`'s role for a disclosed value's number. Further
   tightened in the next revision round below — see "Revisions requested by Persons A and C".
2. **`value: str | None` must have no default.** A construction site that forgot the field entirely
   previously fell back to `None`, silently indistinguishable from an explicit not-disclosed value.
   Both `ExtractedFact` (`shared/schemas.py`) and `FactCandidate` (`extract_facts.py`) now require
   every caller to state `value` explicitly.
3. **`FactRow`'s three disclosure states need their own enforced invariants,** not just a
   documented convention: `"unknown"` requires `value=None` and `snapshot_id=None`; `"not_disclosed"`
   requires `value=None` and a real `snapshot_id`; `"disclosed"` requires both a real `value` and a
   real `snapshot_id`. Enforced via a `FactRow` model validator (`compare_subjects.py`).
4. **This ADR's status previously overstated its own acceptance.** "Accepted by Persons A and B;
   Person C confirmation pending" read as though implementation proceeding was itself a form of
   acceptance, and understated that Person C's sign-off is required, not optional. Corrected above.

## Revisions requested by Persons A and C (2026-09-01, second round)

The first revision's `_quote_supports_non_disclosure` check was itself found under-specified —
loose substring keyword matching and whole-quote (not clause-scoped) checking let several real
false positives through:

1. **Loose substring matching let a keyword match inside an unrelated word** — `"rate"` (part of
   `input_price_usd`'s pricing concept) matched inside `"corporate"`; `"terms"` alone
   (`licence_terms`) matched an unrelated `"payment terms"` sentence; `"limit"`/`"token"` alone
   (`context_window_tokens`) matched `"Rate limits"`, a pricing/throttling concept, not a context
   window. Fixed by replacing single-word substring checks with whole-word/whole-phrase compiled
   regexes (`_FIELD_CONCEPT_PATTERNS`) per field — `context_window_tokens` now requires a combined
   phrase (`"context window"`, `"token limit"`, `"max context"`, ...), never a bare word alone;
   `licence_terms` requires the word `"licence"`/`"license"`/`"licensing"` itself, `"terms"` alone
   is insufficient.
2. **`input`/`output` were accepted as pricing evidence on their own.** Corrected: both price
   fields now share one pattern requiring an actual pricing concept (`price`, `cost`, `rate`,
   `fee`, `dollar`, `cent`, `currency`, `usd`, or `$`) — `"input"`/`"output"` are optional
   qualifiers only, never sufficient by themselves.
3. **Bare `"available"`/`"public"` over-matched plain feature/service-availability statements** —
   `"The model is not available in Europe"` is a normal availability statement, not a claim that a
   *fact* is being withheld, but the withholding-phrase pattern treated `"not available"` as
   equivalent to `"not disclosed"`. Fixed: `_WITHHOLDING_PHRASE_RE` no longer accepts bare
   `"available"`/`"public"` as verbs — only when preceded by a noun that actually names a withheld
   fact (`"pricing/details/information/scores/terms are/is not available"`).
4. **Checking the withholding phrase and the field concept against the WHOLE quote independently
   let two unrelated clauses satisfy both requirements together** — e.g. `"The model features a
   large context window. Pricing details have not been released."` would satisfy
   `context_window_tokens`'s concept pattern (first clause) and the withholding pattern (second
   clause) even though neither clause is itself a context-window non-disclosure statement. Fixed:
   `_quote_supports_non_disclosure` now splits the quote into clauses (`. ! ? ; \n`) and requires
   both patterns to match the SAME clause.

All four gaps were verified against concrete false-positive/true-positive phrase pairs before
relying on the fix — see `tests/unit/test_extract_facts.py`'s clause-bounded-matching test section.

## Revisions requested by Person C (2026-09-01, third round)

Person C found the second round's clause-bounded check itself still too narrow: it only split
clauses on plain sentence punctuation (`. ! ? ; \n`), missing a COMPOUND sentence that joins two
unrelated facts with a comma+conjunction, a bare contrast conjunction, or a dash and has no
terminal punctuation between the two halves — e.g. `"The model has a large context window, but
pricing details have not been released"` is one sentence, yet its two halves are about different
facts.

Fixed with two changes, `extract_facts.py`:

1. **`_CLAUSE_SPLIT_RE` widened** to also split on `,\s*(?:but|and|while|whereas|although|however
   |yet)\b`, a bare `\b(?:but|while|whereas|although|however)\b`, and an em dash (`--`/`—` — a
   single ASCII hyphen, as in `"GPT-4o"`, deliberately does not match either alternative).
2. **A same-clause cross-field-family guard**, for the case a comma doesn't even separate the two
   halves (a bare `"and"` with no leading comma isn't split, by design — `"Input and output
   pricing have not been announced"` must still pass for either price field). Fields are grouped
   into concept families (`_FIELD_TO_FAMILY`/`_FAMILY_CONCEPT_PATTERNS`) — `input_price_usd` and
   `output_price_usd` share one `"pricing"` family — and a clause only counts as support when its
   set of matching families is EXACTLY the candidate's own family, not a superset. This is what
   rejects `"Benchmark scores are strong and pricing has not been announced"` for
   `benchmark_scores`: that clause's own concept and a withholding phrase are both present, but
   the same clause also names pricing, so which fact is actually being withheld is ambiguous.

Verified against concrete phrase pairs for every field family, including the combined-pricing
positive case and a deliberately multi-family negative case, before relying on the fix — see
`tests/unit/test_extract_facts.py`'s compound-clause test section.
