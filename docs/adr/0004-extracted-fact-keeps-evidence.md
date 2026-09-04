# 0004 — ExtractedFact keeps its quoted_span and confidence

Status: Accepted. Authored by Person B; accepted by Person A on 2026-08-27 and confirmed by
Person C on 2026-09-04 after the model-level enforcement was strengthened to reject empty or
whitespace-only LLM evidence spans.
Date: 2026-08-26

## Context

Review of the intelligence module's evidence-grounding checks found that `extract_facts.py`
verified a candidate's `quoted_span` actually appears in the source snapshot, but never checked
that the reported `value` was actually supported by that quote — a model could quote a real
sentence and still attach a fabricated value to it. Fixing that check (see
`intelligence/grounding.py::value_supported_by_quote`) only closes the gap at extraction time.
`ExtractedFact` then discarded `quoted_span` and `confidence` entirely, so there was no way to
later re-audit *why* a stored fact was accepted — whether it was well grounded, and how confident
the extractor was — without re-running extraction.

## Decision

Add two optional fields to `ExtractedFact` (`shared/schemas.py`, mirrored in
`docs/API_CONTRACT.md`): `quoted_span: str | None` and `confidence: float | None`. `extract_facts()`
always populates both for LLM-extracted facts. Both are optional (additive, non-breaking per
`API_CONTRACT.md`'s contract-change process) rather than required, because deterministic facts
(`extraction_method: "deterministic"`) don't always have a natural quote to attach.

## Enforcement

**Accepted clarification, added after review**: the requirement that every LLM-extracted fact
(`extraction_method == "llm_structured_output"`) carries both `quoted_span` and `confidence` is
enforced at the model level, not only by `intelligence/extract_facts.py`'s own construction code
and `tests/contract/test_fixture_contract.py`. `ExtractedFact` has a `model_validator(mode="after")`
(`_require_evidence_for_llm_facts` in `shared/schemas.py`) that raises if an LLM-attributed fact is
built without a non-empty `quoted_span` and a set `confidence`, regardless of which code path
constructs it. Deterministic facts
(`extraction_method == "deterministic"`) are unaffected — they don't always have a natural quote to
attach, per this ADR's own Decision above.

## Consequences

- A stored fact's grounding can be audited later — was the value actually backed by real
  evidence text — without needing the original LLM call or its raw response.
- Existing fixtures/tests that construct `ExtractedFact` without these fields keep working
  unchanged (both default to `None`); `tests/contract/test_fixture_contract.py` now also asserts
  that LLM-extracted fixture facts carry a non-empty `quoted_span` and `confidence`, the same way
  it already required `extraction_model`/`prompt_version`.
- Does not by itself change what a Change or DigestClaim carries — the evidence trail currently
  stops at ExtractedFact. Extending it further downstream (e.g. onto Change/DigestClaim) is a
  separate decision, not made here.
