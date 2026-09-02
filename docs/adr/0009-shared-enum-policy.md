# 0009 — Project-wide Enum policy for lifecycle statuses and controlled vocabularies

Status: Proposed
Date: 2026-09-02

## Context

Several shared data models in `src/ai_daily_digest/shared/schemas.py` and API responses use string
literals or `Literal[...]` types to represent discrete lifecycle states and controlled vocabularies
(e.g. `disclosure_status`, `extraction_method`, `validation_status`, and `status`).

Currently, these strings are validated ad-hoc or via Pydantic type annotations. However:

1. Pure string annotations allow typos and case mismatches if not strictly guarded by runtime
   validation.
2. Inconsistent string values can leak across module boundaries (e.g. Ingestion -> Intelligence ->
   Delivery).
3. Delivery will soon expose OpenAPI documentation via FastAPI; explicit Enum definitions provide
   structured enum schemas in generated OpenAPI contracts without manual schema overrides.

At the same time, over-constraining open fields (such as model IDs, company names, product slugs, or
extensible change types) into rigid Enums would break forward compatibility and create unnecessary
merge friction.

This ADR establishes a project-wide policy for when, where, and how Enums must be introduced and
maintained.

---

## Decision

### 1. Technology: Standard Library `enum.StrEnum`

We select Python 3.12 standard-library `enum.StrEnum` (`from enum import StrEnum`).

- **Zero Dependencies**: Requires no third-party package.
- **String & JSON Compatibility**: `StrEnum` instances inherit from `str`, ensuring
  `isinstance(val, str)` is true, string serialization is native, and wire JSON output is identical.
- **Pydantic & FastAPI Support**: Pydantic v2 and FastAPI natively generate OpenAPI `enum` schemas for
  `StrEnum` fields.

---

### 2. Phase 1 Shared Enums (`shared/schemas.py`)

Phase 1 introduces four shared `StrEnum` definitions, placed directly in
`src/ai_daily_digest/shared/schemas.py` beside their owning Pydantic models:

1. **`DisclosureStatus(StrEnum)`** (owning model: `ExtractedFact`):
   - `DISCLOSED = "disclosed"`
   - `NOT_DISCLOSED = "not_disclosed"`
2. **`ExtractionMethod(StrEnum)`** (owning model: `ExtractedFact`):
   - `DETERMINISTIC = "deterministic"`
   - `LLM_STRUCTURED_OUTPUT = "llm_structured_output"`
3. **`ClaimValidationStatus(StrEnum)`** (owning model: `DigestClaim`):
   - `PENDING = "pending"`
   - `SUPPORTED = "supported"`
   - `UNSUPPORTED = "unsupported"`
4. **`DigestStatus(StrEnum)`** (owning model: `Digest`):
   - `DRAFT = "draft"`
   - `REVIEW = "review"`
   - `PUBLISHED = "published"`

---

### 3. ChangeType Decision: Keep `Change.change_type` Open (`str`)

`Change.change_type` in `shared/schemas.py` remains typed as **`str`** (open set).

- **Rationale**: Change detection is an evolving area. While core inference produces known change
  types, external adapters, future plugins, or manual overrides may introduce new change categories
  without requiring a breaking shared schema migration.
- **Invariant Safety**: `validate_change_shape()` in `shared/schemas.py` continues to enforce
  observation-shape invariants for both known and unknown change types (ensuring any unrecognized
  string still receives strict evidence validation).
- **Intelligence-Local Enum**: Intelligence may define a module-local `InferredChangeType(StrEnum)` in
  `src/ai_daily_digest/intelligence/facts.py` covering values produced by `_infer_change_type()`:
  - `INCREASED = "increased"`
  - `DECREASED = "decreased"`
  - `CHANGED = "changed"`
  - `DISCLOSED = "disclosed"`
  - `NOT_DISCLOSED = "not_disclosed"`
- Closing `Change.change_type` globally in `shared` is explicitly deferred and would require a future
  ADR amendment.

---

### 4. Explicit Deferrals (Values that Remain Strings for Now)

The following fields remain `str` and are NOT converted to Enums in Phase 1:

- **`Change.review_status` / `ChangeSet.review_status`**: The human-review lifecycle and approval
  state machine transitions are not yet fully specified.
- **`FactRow.disclosure_status` (in `compare_subjects.py`)**: Remains an intelligence-local
  `Literal["unknown", "disclosed", "not_disclosed"]`.
  - *Critical Distinction*: Persisted `DisclosureStatus` only contains `disclosed` and
    `not_disclosed`. `"unknown"` represents the absence of an `ExtractedFact` row and MUST NEVER
    become a member of the persisted `DisclosureStatus` Enum.
- **Resolution methods, comparison relations, source adapter types, email delivery statuses**:
  Deferred until their respective vertical slices are implemented.

---

### 5. Values that Must Permanently Remain Open Strings

The following fields must never become Enums:

- `publisher`, `company`, `product`
- `source_id` (slug in `sources.yaml`)
- `event_id`
- `dedupe_key`, `content_hash`
- fact/change field names (`field`)
- `tags`, `url`, `language`, external model identifiers (`extraction_model`)

---

### 6. Code Placement Policy

- **No Miscellaneous Dumping Grounds**: Shared Enums must live directly in
  `src/ai_daily_digest/shared/schemas.py` next to the Pydantic models that own them.
- Do NOT create generic bucket modules such as `shared/enums.py`, `shared/statuses.py`, or
  `shared/vocabularies.py`.
- Do NOT place lifecycle statuses in `src/ai_daily_digest/shared/attributes.py` (which is dedicated
  exclusively to comparison attributes and rules).
- Module-local Enums (such as `InferredChangeType`) live in their respective owning domain module
  (e.g. `intelligence/facts.py`).

---

### 7. Compatibility & Wire Format Guarantees

Implementation of this policy must preserve:

1. **Wire Compatibility**: JSON serialization (`model_dump(mode="json")`, `model_dump_json()`) emits
   exact lowercase string values (e.g. `"disclosed"`, `"published"`).
2. **Fixture Compatibility**: Existing test fixtures in `tests/fixtures/contracts/*.json` continue to
   load and validate without modification.
3. **Strict Boundary Validation**: Pydantic models reject misspelled, wrong-case, or invalid strings
   during validation.
4. **`model_copy` Invariant**: `model_copy(update=...)` does not re-validate inputs; callers must pass
   Enum members rather than raw unvalidated strings.
5. **OpenAPI Generation**: FastAPI will generate `enum` schemas in the OpenAPI specification from
   these shared types. Delivery must reuse these shared Enums and never duplicate equivalent Enum
   definitions.

---

## Future Implementation Scope (Separate PR)

Once this ADR is accepted by Persons A, B, and C, the implementation PR will:

1. Add the four `StrEnum` definitions to `src/ai_daily_digest/shared/schemas.py`.
2. Add `InferredChangeType` to `src/ai_daily_digest/intelligence/facts.py`.
3. Update Pydantic model field annotations and defaults in `shared/schemas.py`.
4. Update intelligence call sites, comparisons, and `model_copy` invocations.
5. Add unit tests in `tests/unit/test_schemas.py` verifying:
   - Valid member acceptance and rejection of invalid/misspelled/wrong-case strings.
   - JSON serialization and round-trip integrity.
   - Exact `model_json_schema()` enum members.
   - Fixture deserialization.
   - Behavior tests confirming disclosure invariants, LLM extraction quotes, and publication gate
     rules remain intact.
