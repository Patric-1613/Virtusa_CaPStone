# Design proposal — structured comparison output & remaining grounding gaps

Status: **Design accepted by Person A (2026-08-28), pending Person C's confirmation — but
implementation is explicitly out of scope for PR #5.** Person A separated design acceptance from
PR #5's scope to avoid an open-ended review loop: this design is the agreed direction for a
follow-up issue/PR, not something PR #5 claims to have built. See
[ADR 0005](adr/0005-structured-comparison-and-snapshot-resolution.md)'s "MVP scoping decision"
section for the full reasoning, and [ADR 0006](adr/0006-disclosure-status-semantics.md) for the
"unknown" vs. "not disclosed" value-semantics question (remains Proposed, deferred, not needed for
this design's Phase 1 scope).

Two things are **already implemented** (not just proposed), because they were required conditions
for safely deferring the rest, not optional design choices:
- `daily_run.py::_never_auto_publish_comparisons()` — no comparison claim may cause a digest to
  auto-publish, regardless of its own validation status.
- `extract_facts.py`'s cross-field ambiguity guard — a fact whose quote is shared with a
  *different* field's candidate in the same extraction response is dropped, not guessed at (the
  exact "Input costs 5 and output costs 15" case).
- `validate.py`'s content-grounding check is now fail-closed: a citation whose content can't be
  loaded is `unsupported`, never trusted on the strength of the id existing.

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

### (a) Structured subject/field comparison output — with same-subject and duplicate-pair rejection

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

1. **Rejects `subject_a == subject_b`** outright — a subject can't be legitimately compared to
   itself. (An equivalent check already shipped for the current free-text shape — see
   `compare_subjects.py::_candidate_rejection_reason`'s `subject_compared_to_itself` case — as an
   immediate fix, not waiting for this redesign.)
2. **Deduplicates reversed pairs, keyed precisely.** `(A, B, field)` and `(B, A, field)` are the
   same comparison; code normalizes by sorting the pair on `(company, product)` **before**
   deduplicating, and the dedup key is `(sorted_pair, field)`, not `sorted_pair` alone — two
   different fields for the same subject pair (e.g. `(A, B, context_window_tokens)` vs.
   `(A, B, input_price_usd)`) are distinct comparisons and must never collapse into one.
3. Looks up `value_a`/`value_b` from `build_fact_table()`'s real rows for exactly that
   `(subject, field)` pair — no string-matching against prose, direct dictionary lookup.
4. Applies the field-specific comparison rule for that field (see (f), revised below) — or drops
   the candidate if the field has no defined rule yet.

### (a2) "Unknown" vs. "not disclosed" — moved to ADR 0006

**Split out on this round of feedback.** This document previously proposed a
`disclosure_status` field here; that's now [ADR 0006](adr/0006-disclosure-status-semantics.md),
kept separate because it isn't required for Phase 1 (`context_window_tokens`-only comparison) and
shouldn't expand this ADR's already-substantial surface. See that document for the full proposal.

### (b) Deterministic comparison validation and rendering

Once (a) exists, `compare_subjects.py` becomes structurally identical to `draft_claims.py`: the
model never writes prose that reaches a claim. Every word in the final `DigestClaim.text` is
code-generated from verified values and the field's comparison rule. This closes the "cheaper
than" case and the swapped-value case at the root — there's no longer a sentence for the model to
get wrong, only a `(subject, subject, field)` selection for it to get uninteresting.

### (c) Fail-closed handling of LLM-authored non-numeric claims

Once (a)/(b) ship, comparison claims stop being "LLM-authored" in the sense that matters, so the
interim "comparisons never auto-publish" policy should be **lifted as an explicit follow-up step**
when (a)/(b) land — not made permanent, and not an automatic side effect of merging (a)/(b); a
deliberate change with its own review.

### (d) Historical snapshot lookup — typed resolver boundary in `shared/`, required at the final gate

**Revised twice now.** Round one corrected the original "thread an ever-growing dict across days"
proposal into a typed `SnapshotResolver` interface. Round two corrected that further:

1. **Lives in `shared/`, not `intelligence/`.** A protocol ingestion is ever expected to implement
   or provide an instance of cannot live inside an intelligence-private module — that would make
   ingestion depend on an intelligence-owned type, backwards from the intended module boundary.
   Proposed location: `shared/snapshot_resolver.py`, alongside `shared/schemas.py` — a real
   cross-module contract, matching the existing `Loader`/`FixtureLoader`/`StoreLoader` split
   pattern in shape, but the interface itself belongs in `shared/`, that split's own home is
   `intelligence/loaders.py` because `Loader` is intelligence-internal in a way `SnapshotResolver`
   is not.
2. **Synchronous, and explicitly scoped as Phase 1 only.** `get_content(snapshot_id) ->
   DocumentSnapshot | None` is sync. A future database-backed implementation may need to be async;
   that's a separate design decision this document does not make or preclude.
3. **Required, not optional, at the actual final gate.** The version of this proposal reviewed
   previously had `snapshot_resolver: SnapshotResolver | None = None` everywhere, defaulting to
   existence-only checking when omitted. Corrected: at `daily_run.py`'s call into
   `publish_digest`/`validate_digest` — the real, batch-level gate that decides whether a digest can
   ship — the resolver becomes **required**. Passing nothing must not silently fall back to trusting
   citation existence. The one exception: `graph.py`'s per-item `validate` node, which — per its own
   existing docstring — never actually authorizes publication (only the batch-level final gate
   does), may still run resolver-less. That's safe specifically because it isn't the check that
   decides whether something ships.

```python
# shared/snapshot_resolver.py — illustrative, exact shape TBD during implementation
class SnapshotResolver(Protocol):
    def get_content(self, snapshot_id: str) -> DocumentSnapshot | None: ...


class InMemorySnapshotResolver:
    """Interim implementation, backed by a plain dict -- same caveats
    the original proposal had (unbounded growth if the caller keeps
    adding to it), but now isolated behind an interface validate.py
    depends on, not a raw dict type. A real ingestion-store-backed
    resolver plugs in later without changing validate.py's signature."""

    def __init__(self, snapshots_by_id: dict[str, DocumentSnapshot]) -> None:
        self._snapshots_by_id = snapshots_by_id

    def get_content(self, snapshot_id: str) -> DocumentSnapshot | None:
        return self._snapshots_by_id.get(snapshot_id)
```

**The fail-closed rule this replaces (already shipped, unaffected by the above):**
**existence of a snapshot id is never proof its content supports the claim.** If
`_claim_numbers_are_grounded` can't load content for every cited snapshot, the claim is
`unsupported` (routes to review), not trusted. This means routine multi-day claims will need
review more often than ideal until a real resolver can actually retrieve historical content — an
accepted cost of the fail-closed default, not a bug to route around.

### (e) Ambiguous multi-number evidence spans

**Partially addressed — containment, not a fix, and its limits are now documented rather than
assumed away.** The interim guard (already shipped, see
`extract_facts.py::_cross_contaminated_indices`) drops a fact whose quote also contains a
*different field's* real value in the same extraction response — precise enough to catch "Input
costs 5, output costs 15" without also flagging the legitimate "increased from 128,000 to 256,000
tokens" pattern (same field, no sibling candidate involved).

Documented, real limits of this guard (per review — not to be described as a solved problem
anywhere this is referenced):

1. It only catches the mix-up when **both** the correct and the confused candidate appear in the
   **same** extraction response. If the model returns a wrong value while omitting the sibling fact
   that would have exposed it, there's nothing to cross-check against.
2. It can false-positive on **coincidentally** shared digit sequences between genuinely unrelated
   fields — a safe direction to be wrong in (a valid fact is dropped, not a false one published),
   but a real, accepted cost, not free caution.

This guard is a floor, not the real fix, per the review's own note: **character offsets improve
citation precision but do not alone prove which number belongs to which field** — two fields could
still each have their own distinct, non-overlapping quote and still both be wrong if the model
mis-transcribes which number came from where. The real fix needs the model to report the value's
exact character offset within `content_text` (the shape Anthropic's own citations API uses —
`start_char_index`/`end_char_index`) instead of a re-quoted substring, verified against the source
text at exactly that offset — combined with a check that no *other* field's offset range overlaps
or nearly-coincides with it. This is a prompt + response-schema change to `extract_facts.py`
(`FactCandidate` gains offset fields alongside or instead of `quoted_span`), needing its own
contract-change process (the same discipline ADR 0004 established) — proposed as a follow-up ADR
once the comparison redesign here is settled, not bundled into this one.

### (f) The canonical shared value representation — field-specific rules, not a numeric/text split

**Replaced.** The original proposal's `COMPARABLE_FIELD_KINDS: dict[str, Literal["numeric",
"text"]]` was too coarse — correctly flagged. Different fields need genuinely different comparison
semantics:

| Field | What it actually needs |
|---|---|
| `context_window_tokens` | Integer + unit (tokens) — already unambiguous as a bare string today. |
| `input_price_usd` / `output_price_usd` | Currency, unit (per-what — tokens? requests?), and basis (list price vs. discounted) — **not currently captured at extraction time at all**, so comparing two bare price strings today can silently compare incompatible things. |
| `benchmark_scores` | Benchmark name and test conditions — "71.2" means nothing without knowing which benchmark, under what setup. |
| `availability_regions` / `modalities` | Set comparison (overlap/subset/disjoint), not a scalar relation ("higher/lower" doesn't apply to a list of regions). |
| `licence_terms` | Not proposed for automated comparison at all — this is a category judgment ("more permissive"), not comparable, and shouldn't be treated as one. |

Proposed: a per-field comparison-rule registry in `shared/attributes.py`, and — critically — **only
`context_window_tokens` is in scope for Phase 1** (this ADR), because it's the one field whose
current bare-string representation is already sufficient and unambiguous. Every other field is
**excluded from cross-subject comparison** until its own structured representation is designed and
agreed, each via its own follow-up ADR (prices need an extraction-level schema change too, not just
a comparison-rendering change — a bare `"5"` doesn't carry currency/unit/basis today regardless of
how comparison renders it).

```python
# shared/attributes.py (illustrative -- exact shape to be finalized during implementation)
COMPARISON_RULES: dict[str, ComparisonRule] = {
    "context_window_tokens": IntegerComparisonRule(unit="tokens"),
    # input_price_usd, output_price_usd, benchmark_scores, availability_regions,
    # modalities, licence_terms: deliberately absent -- see table above.
}
```

`compare_subjects()` drops (does not attempt) any candidate naming a field not in this registry,
the same way it already drops a field not in `COMPARABLE_FIELDS` at all.

**Malformed stored values fail per-candidate, not per-batch** (added on this round of feedback): if
a stored `context_window_tokens` value can't be parsed as an integer (a data-quality problem
upstream, not something `compare_subjects()` caused), `IntegerComparisonRule` drops and logs only
that one candidate — it must never raise in a way that aborts the rest of the comparison pass for
every other pair being compared that batch. Mirrors the existing per-item/per-comparison
broad-exception handling already in `daily_run.py::run_daily`.

### (g) Should first observations create "new disclosure" claims?

Confirmed as a **separate product decision**, not decided here. If adopted:
- **Scoped via an explicit allowlist** (mirroring `comparison_fields`) — not every field of every
  newly-tracked subject, a deliberate choice of which fields are "disclosure-worthy."
- **Neutral wording required**: "first recorded observation," never "changed from" — there is no
  "from" for a first observation, and implying continuity with a prior state would itself be an
  ungrounded claim.

## Interim safety net (already implemented)

Three independent fail-closed mechanisms are live now, closing the *publishing* risk while the
*detection* mechanisms above remain to be built:

1. `daily_run.py::_never_auto_publish_comparisons()` — no cross-subject comparison claim may cause
   auto-publish.
2. `extract_facts.py::_cross_contaminated_indices()` — a fact whose evidence is ambiguous across
   sibling fields is dropped, never becomes a `Change` or claim at all.
3. `validate.py`'s content-grounding check is fail-closed on missing content — existence is never
   treated as proof.

## What this document is asking for

Persons A and C's final sign-off on this revision — per explicit instruction, **only this document
and ADR 0005 are to change in response to feedback; no part of (a)/(b)/(d)/(f) is to be implemented
until that sign-off is recorded.** (e) is flagged as a genuinely open problem needing its own
follow-up ADR regardless of this document's approval. (g) needs an explicit product decision,
separately, before any implementation. (a2)/ADR 0006 is a related but independent decision, not a
blocker for this document's approval.
