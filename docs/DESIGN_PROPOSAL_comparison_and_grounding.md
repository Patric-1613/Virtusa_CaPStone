# Design proposal — structured comparison output & remaining grounding gaps

Status: **Proposal — not yet implemented, not yet team-approved.** Written in response to the
second review of the intelligence pipeline PR, which correctly found that the current
numeric-only grounding checks (`intelligence/grounding.py`) can't catch a *relational* fabrication
("OpenAI is cheaper than Anthropic" when it isn't) or a *misattributed* one (real numbers, wrong
subject). This document proposes the fix; it deliberately does not implement it — per the review's
own request, this needs sign-off before another implementation pass.

An interim, code-level mitigation is already in place (see "Interim safety net" below) so the gap
this document addresses is contained, not open, while it's under discussion.

## The core problem

`compare_subjects.py`'s `ComparisonClaimCandidate` lets the model write **free-form prose** with a
citation list attached. Code can check that citations are real and that numbers *mentioned in the
prose* match real values — but it can't check a claim that makes no numeric assertion at all
("cheaper", "faster", "not yet available"), and it can't tell *which* number in the prose belongs
to *which* subject, so a claim can state two real numbers, swapped.

The fix is the same principle `draft_claims.py` already uses for single-subject changes:
**stop letting the model author the final claim text.** Have it propose *what's worth comparing*,
have code look up the real values and compute the actual relationship, and have code render the
sentence.

## Proposed design, by the review's numbered points

### (a) Structured subject/field/value/relation comparison output

Replace `ComparisonClaimCandidate`'s free-text `text` field with a structured, verifiable shape:

```python
class ComparisonAssertion(BaseModel):
    subject_a: Subject
    subject_b: Subject
    field: str
    # The model proposes it wants to compare (subject_a, subject_b, field).
    # It does NOT supply values or a relation -- code looks those up.
```

The model's job shrinks to picking interesting `(subject_a, subject_b, field)` triples — a
curation task, not an assertion task. Code then:

1. Looks up `value_a`/`value_b` from `build_fact_table()`'s real rows for exactly that
   `(subject, field)` pair — no string-matching against prose, direct dictionary lookup.
2. If either value is `None` ("not disclosed"), the comparison is either dropped or rendered as
   an explicit "X has not disclosed Y" claim — never a relational claim with a missing side.
3. If both values are present, code decides whether they're numerically comparable (see (f) below)
   and computes the relation itself.

### (b) Deterministic comparison validation and rendering

Once (a) exists, `compare_subjects.py` becomes structurally identical to `draft_claims.py`:

```python
def render_comparison_claim(assertion, value_a, value_b) -> DigestClaim:
    if value_a is None or value_b is None:
        text = f"{subject_a_name}... ; {subject_b_name} has not disclosed {field_label}."
    elif is_numeric_field(assertion.field):
        relation = (
            "lower"
            if float(value_a) < float(value_b)
            else ("higher" if float(value_a) > float(value_b) else "equal")
        )
        text = (
            f"{subject_a_name}'s {field_label} is {value_a}, {relation} than "
            f"{subject_b_name}'s {value_b}."
        )
    else:
        text = f"{subject_a_name}'s {field_label} is {value_a}; {subject_b_name}'s is {value_b}."
    ...
```

The model never writes prose that reaches a claim; every word in the final `DigestClaim.text` is
code-generated from verified values. This closes the "cheaper than" case and the swapped-value case
at the root — there's no longer a sentence for the model to get wrong, only a `(subject, subject,
field)` selection for it to get uninteresting (worst case: a boring or irrelevant comparison, not a
false one).

### (c) Fail-closed handling of LLM-authored non-numeric claims

Once (a)/(b) ship, comparison claims stop being "LLM-authored" in the sense that matters (the
sentence is code-rendered), so the interim "comparisons never auto-publish" policy (see below)
should be **lifted**, not made permanent — it's a stand-in for the real fix, not a replacement for
it. This document proposes making that removal an explicit follow-up step when (a)/(b) land, not
an automatic side effect.

### (d) Historical snapshot lookup at the final gate

Today, `validate.py`'s content-grounding check only has `snapshots_by_id` for the *current batch*
— a routine multi-day change ("increased to X, up from Y") cites a previous-day snapshot that
isn't there, so the interim code (already pushed) falls back to existence-only for that claim
rather than wrongly rejecting it. That's a safe stopgap, not a fix.

Proposed real fix: extend the existing "caller threads state across days" pattern (already used for
`FactStore` and `known_snapshot_ids`) to snapshot content too — `run_daily()`'s caller owns a
`known_snapshots: dict[str, DocumentSnapshot]` that grows across days the same way
`known_snapshot_ids` already does, instead of `daily_run.py` building a batch-only dict internally.
This needs no new storage layer, just a signature/contract change to `run_daily()` (and updates to
every test that calls it) — the same shape of change as `known_snapshot_ids`, not a database. Once
`StoreLoader` (the real DB-backed loader, not yet built) exists, this in-memory dict gets replaced
by a real lookup the same way `FixtureLoader` is expected to be replaced.

### (e) Ambiguous multi-number evidence spans

The reproduced case: "Input costs 5 and output costs 15" lets `input_price_usd=15` pass, because
`value_supported_by_quote()` only checks whether the value's digits appear *anywhere* in the quote,
not whether they're the number actually *associated* with the field being extracted.

This is a real, unsolved gap. Two options, in increasing order of robustness:

1. **Weak, immediate heuristic**: reject a fact if its `quoted_span` contains more than one
   distinct number from `COMBINED_FIELDS`'-relevant digit runs, on the theory that a tight,
   single-fact quote shouldn't need to contain someone else's number. Cheap, but blunt — it would
   also reject legitimate quotes like "increased from 128,000 to 256,000 tokens" (two numbers, one
   fact), which is exactly the accepted pattern in this project's own examples. **Not proposed** —
   the false-positive rate looks too high without more care.
2. **Real fix**: require the model to report the value's exact character offset within
   `content_text` (the same shape Anthropic's own citations API uses — `start_char_index`/
   `end_char_index`) instead of a re-quoted substring, and verify the value against the source
   text at exactly that offset, not a fuzzy re-search. This is the option this document
   recommends, but it's a prompt + response-schema change to `extract_facts.py` (`FactCandidate`
   gains `start_char`/`end_char` instead of, or alongside, `quoted_span`), which itself needs the
   same contract-change discipline ADR 0004 established. Proposed as a follow-up ADR once this
   design is agreed, not bundled into this document's approval.

Until either lands, this gap remains open and disclosed — not fixed by this PR.

### (f) The canonical shared value representation

`ExtractedFact.value`/`FactObservation.value` stay `str` (matches the fixed doc examples) — no
change proposed there; splitting into `str | float` would need a discriminated per-field type,
duplicating information that already exists in `shared/attributes.py::COMPARABLE_FIELDS`. Instead,
propose extending that same table with an explicit kind marker:

```python
# shared/attributes.py
COMPARABLE_FIELD_KINDS: dict[str, Literal["numeric", "text"]] = {
    "context_window_tokens": "numeric",
    "input_price_usd": "numeric",
    "output_price_usd": "numeric",
    "benchmark_scores": "numeric",
    "availability_regions": "text",
    "licence_terms": "text",
    "modalities": "text",
}
```

(b)'s relation computation reads this instead of trying `float(value)` and catching the exception —
explicit intent instead of duck-typing. Small, additive, low-risk; proposed as part of (a)/(b)'s
implementation, not standalone.

### (g) Should first observations create "new disclosure" claims?

Currently a first-ever fact about a subject produces no `Change`, no `ChangeSet`, no `DigestClaim`
— by design at the `FactStore` level (a first observation isn't a *change*), but the project's own
comments used to imply this was "reported elsewhere," which isn't true anywhere in the code today
(corrected in this PR — see `facts.py`/`docs/LLM_AGENT_SPECS.md`).

Whether to build a "new disclosure" claim path is a **product decision**, not a backend detail:

- **For**: a subject's first disclosed price/benchmark/context-window is genuinely newsworthy —
  arguably core to what "AI Daily Digest" is supposed to report.
- **Against**: every field of every newly-tracked subject would generate a claim, which could
  dominate a digest's volume on any day a new subject is first tracked, with no tuning knob today.

Proposed: don't build this silently. If the team wants it, scope it the same way
`comparison_fields` already scopes cross-subject comparisons — an explicit, opt-in list of which
fields are "disclosure-worthy" on first sight, defaulting to none — so digest volume stays a
deliberate choice, not a side effect of whichever fields a prompt happens to extract.

## Interim safety net (already implemented, this PR)

Until (a)–(c) land, `daily_run.py::_never_auto_publish_comparisons()` forces any digest containing
a cross-subject comparison claim to `"review"`, regardless of how that claim scores against every
other check. This closes the *publishing* risk (a false comparison can no longer reach subscribers
automatically) without yet closing the *detection* gap (a human reviewer still has to catch it) —
which is the correct ordering per the review's own request: fail closed now, fix the real mechanism
next.

## What this document is asking for

Feedback/approval on the shape above (particularly (a)/(b), the structural fix, and (d), the
snapshot-content threading) before implementing it — per the review's explicit request not to start
another implementation pass without one. (e) and (g) are flagged as open/deferred regardless of
this document's approval, since they need either more design work or a product decision this PR
can't make unilaterally.
