# 0005 — Structured cross-subject comparisons, snapshot content resolution, and disclosure semantics

Status: Proposed
Date: 2026-08-27

## Context

The intelligence pipeline's cross-subject comparison step (`compare_subjects.py`) lets an LLM write
free-form prose for a comparison claim, with code checking only that its citations are real and
that any numbers *mentioned in the prose* match real stored values. Two independent adversarial
reviews found this insufficient in ways a numeric-only check structurally cannot catch:

- A claim with no numeric assertion at all ("OpenAI is cheaper than Anthropic") has nothing for the
  numeric check to verify, true or false.
- A claim can state two real numbers attributed to the wrong subject (swapped), and the check —
  which only confirms both numbers are *somewhere* among the real values — can't detect the swap.
- Separately, `validate.py`'s content-grounding check and `extract_facts.py`'s value extraction had
  related gaps: unverifiable historical citations were being trusted rather than treated as
  unproven, and a value could be extracted from a quote shared with a different field's value with
  no attribution check.

This affects more than `intelligence/`: the fix touches `shared/attributes.py` (field comparison
rules), the boundary intelligence needs into snapshot content (today served by an ad hoc dict, with
the real version eventually backed by ingestion's storage), and the shape of `DigestClaim`/
`ExtractedFact` that delivery and any future API consumers read.

## Decision

Adopt the design in
[`docs/DESIGN_PROPOSAL_comparison_and_grounding.md`](../DESIGN_PROPOSAL_comparison_and_grounding.md),
summarized:

1. **Comparisons stop being LLM-authored prose.** The model proposes structured
   `(subject_a, subject_b, field)` triples to compare; code looks up real values and renders the
   claim text deterministically — the same pattern `draft_claims.py` already uses for single-subject
   changes. Same-subject comparisons are rejected; reversed-pair duplicates are deduplicated.
2. **"Unknown" and "not disclosed" are distinct, and only "not disclosed" is a claim.** A missing
   value defaults to silence (no claim about that side), not an inferred "has not disclosed"
   sentence. "Not disclosed" becomes its own extractable, evidence-backed fact state
   (`ExtractedFact.disclosure_status`), not a default read from an absent row.
3. **Comparison rules are field-specific, not a numeric/text split.** Each comparable field needs
   its own defined representation (currency/unit/basis for prices, benchmark name/conditions for
   scores, set semantics for regions/modalities). Phase 1 of this decision scopes comparison to
   `context_window_tokens` only — the one field already unambiguous as a bare string. Every other
   field is excluded from comparison until its own representation is designed, each via a follow-up
   ADR.
4. **Snapshot content is resolved through a typed `SnapshotResolver` interface**, not a raw,
   caller-owned, ever-growing dict — mirroring the existing `Loader`/`FixtureLoader`/`StoreLoader`
   split. An in-memory implementation is the interim backing; a real ingestion-store-backed one
   plugs in later without changing `validate.py`'s call signature.
5. **A citation whose content can't be resolved is never treated as proof of support.** Existence of
   a snapshot id is not evidence its content backs a claim; an unresolvable citation routes the
   claim to review, not "supported."
6. **Multi-number attribution ambiguity in extraction is deferred, conditionally.** A full fix
   (character-offset citations) is out of scope for this ADR; deferring it is only acceptable
   because a fact whose evidence quote is shared with a different field's value is dropped outright
   rather than guessed at — already implemented as an interim guard, independent of this ADR's
   approval.
7. **First-ever disclosures remain a separate, undecided product question.** Not built here. If
   adopted later, scoped via an explicit allowlist, with neutral wording ("first recorded
   observation") that doesn't imply a prior state that never existed.

## Consequences

- Cross-subject comparison claims become verifiable by construction for the fields they cover,
  closing the "cheaper than" and swapped-value fabrication classes at the root rather than trying to
  detect them after the fact in prose.
- Initial comparison coverage is deliberately narrow (`context_window_tokens` only) — prices,
  benchmarks, regions, modalities, and licence terms are NOT compared until their own representation
  is designed. This is a real capability reduction from what a free-text `compare_subjects` could
  attempt (however unreliably) — accepted as the cost of correctness.
- `ExtractedFact` gains a `disclosure_status` field (additive, same discipline as ADR 0004) —
  requires updating fixtures and contract tests.
- `validate.py`'s dependency shifts from `dict[str, DocumentSnapshot]` to a `SnapshotResolver`
  protocol — a signature change for every caller, test double, and the interim in-memory
  implementation.
- Multi-day/historical claims will need human review more often until a real `SnapshotResolver` can
  retrieve content beyond the current batch — an accepted, deliberate cost of the fail-closed
  default in item 5, not treated as a defect to route around.
- The character-offset citation fix for extraction attribution (item 6's real fix) is explicitly
  out of scope here and needs its own follow-up ADR before implementation.
- The "new disclosure claims" question (item 7) stays open pending a product decision separate from
  this ADR.

This ADR requires review from Persons A and C before implementation begins, per the team's
contract-change process (`docs/API_CONTRACT.md`).
