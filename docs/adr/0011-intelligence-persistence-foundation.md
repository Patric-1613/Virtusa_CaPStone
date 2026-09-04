# 0011 — Intelligence persistence foundation (facts, changes, digests)

Status: Proposed (authored by Person B; review required from Persons A and C)  
Date: 2026-09-04  

> **Working assumption — state it explicitly:**  
> This ADR assumes the SQLAlchemy engine and session factory live in `shared/db/` (e.g. `shared/db/engine.py`), per the open amendment request on PR #48 §12.3. If that request is rejected and the engine remains in `ingestion/db/`, revise this section and the import paths accordingly. Do not silently assume either placement.

---

## 1. Context & Rationale

The intelligence module currently holds its operational state in-memory:
- `FactStore` tracks per-field current observations and history in `intelligence/facts.py`.
- `Change` and `ChangeSet` aggregates are computed during pipeline execution (`intelligence/change_sets.py`, `intelligence/daily_run.py`).
- `Digest` and `DigestClaim` records are generated, validated, and evaluated in `intelligence/assemble_digest.py` and `intelligence/validate.py`.

To support end-to-end trace journeys, historical auditability, and the `/v1/changes` and `/v1/digests` endpoints defined in `docs/API_CONTRACT.md`, intelligence requires a concrete PostgreSQL persistence foundation.

This design mirrors the architectural rigor and discipline of the ingestion persistence foundation defined in PR #48 (open, unmerged as of this writing):
- **Keyset pagination compatibility:** Keyset ordering tuples mandated by [ADR 0008](0008-cursor-pagination-contract.md) (`(detected_at DESC, id DESC)` for `/v1/changes`, `(digest_date DESC, id DESC)` for `/v1/digests`) require strictly immutable ordering columns.
- **Three-layer immutability enforcement:** As established in [ADR 0008](0008-cursor-pagination-contract.md) §5.D and PR #48 §11, immutability is guaranteed across model freezing, repository method restriction, and database storage-level trigger enforcement.
- **Trigger-based storage mechanism:** Rather than managing complex column-level privileges, `BEFORE UPDATE` database triggers are used to reject in-place mutations on protected columns, identical to the pattern selected for `source_items` in PR #48 §11.
- **Whole-row immutability for source evidence:** `extracted_facts` mirror `document_snapshots` from PR #48 §11 — once written, facts are immutable evidence. Corrections or updates append a new row and retain full provenance.

---

## 2. What already exists — verified against the tree

The shared contracts and intelligence domain models already define the required fields, invariants, and validation boundaries. The citations below for `shared/schemas.py`, `intelligence/facts.py`, and `docs/adr/0008-cursor-pagination-contract.md` have been opened and confirmed in the repository tree. Citations referencing `ADR 0002` represent proposed text from PR #48 (open, unmerged as of this writing; section numbering must be re-verified upon merge):

| Component | Existing definition / location | Status & Invariants |
|---|---|---|
| `Change.id` | `src/ai_daily_digest/shared/schemas.py:439` | `id: Uuid7Id = Field(frozen=True)` (ADR 0007 / ADR 0008) |
| `Change.detected_at` | `src/ai_daily_digest/shared/schemas.py:447` | `detected_at: OrderingTimestamp = Field(frozen=True)` (UTC, microseconds preserved, naive rejected) |
| `Change` shape validator | `src/ai_daily_digest/shared/schemas.py:450-459` | `_require_valid_change_shape` delegates to `validate_change_shape()` |
| `ChangeSet` aggregate | `src/ai_daily_digest/shared/schemas.py:461-472` | `id`, `subject: Subject`, `changes: list[Change]`, `previous_snapshot_ids`, `current_snapshot_ids`, `review_status` |
| `Digest.id` | `src/ai_daily_digest/shared/schemas.py:520` | `id: Uuid7Id = Field(frozen=True)` |
| `Digest.digest_date` | `src/ai_daily_digest/shared/schemas.py:521` | `digest_date: date = Field(frozen=True)` (Native calendar date, YYYY-MM-DD on wire) |
| `DigestClaim` | `src/ai_daily_digest/shared/schemas.py:500-509` | `id: Uuid7Id`, `text: str`, `citation_snapshot_ids: list[Uuid7Id]`, `validation_status: ClaimValidationStatus` |
| `ExtractedFact` | `src/ai_daily_digest/shared/schemas.py:195-298` | `id: Uuid7Id`, `snapshot_id: Uuid7Id`, `field: str`, `value: str \| None`, `disclosure_status`, `extraction_method`, `extraction_model`, `prompt_version`, `quoted_span`, `confidence`; evidence invariants enforced via model validators ([ADR 0004](0004-extracted-fact-keeps-evidence.md), [ADR 0006](0006-disclosure-status-semantics.md)). Fetch-time provenance is reached via `snapshot_id → document_snapshots.fetched_at`. |
| `Subject` value type | `src/ai_daily_digest/shared/schemas.py:151-165` | `company: str`, `product: str`; frozen, hashable value object without a surrogate ID |
| `FactStore._FieldRecord` | `src/ai_daily_digest/intelligence/facts.py:162-175` | In-memory state: `current: ExtractedFact \| None`, provenance (`current_snapshot_id`, `current_source_url`, `current_observed_at`), and `history: list[ExtractedFact]`. `update_fact()` (`facts.py:341-348, 359-363`) tracks `source_url` and `observed_at` alongside each fact so subsequent changes can construct `previous: FactObservation` with full provenance. This is the source of truth for why `extracted_facts.observed_at` and `.source_url` exist in storage despite not being on `ExtractedFact` itself. |
| Ordering tuple protection | `docs/adr/0008-cursor-pagination-contract.md:212-219` | Exactly 6 protected columns across the service: `SourceItem.id`, `SourceItem.first_fetched_at`, `Change.id`, `Change.detected_at`, `Digest.id`, `Digest.digest_date` |
| Protected-column test suite | `docs/adr/0008-cursor-pagination-contract.md:767-775` | Mandatory 4-part test pattern: reject update, safe rollback, stored value survives, no repository mutation path |
| Keyset pagination index | PR #48 §9.1 (open, unmerged) | Composite descending index `(first_fetched_at DESC, id DESC)` precedent |
| Trigger-based immutability | PR #48 §11 (open, unmerged) | Precedent: `BEFORE UPDATE` trigger function raising exception on protected column modification |
| Array column precedent | PR #48 §10 (open, unmerged) | Precedent: PostgreSQL native array types (`text[]` for authors/tags) rather than join tables or `jsonb` |

---

## 3. Architectural Decisions

| # | Decision | Selected Approach | Rationale | Rejected Alternatives |
|---|---|---|---|---|
| 1 | **`changes` table schema & observations** | Flattened typed columns (`previous_value`, `previous_observed_at`, `previous_snapshot_id`, `previous_source_url`, and `current_*`). `current_value` is **nullable** (`TEXT`). | Follows PR #48 §10's rationale against `jsonb` for fixed, small shapes: yields strict database type-checking, direct SQL indexing, and explicit nullability semantics. Per [ADR 0006](0006-disclosure-status-semantics.md), a `change_type="not_disclosed"` transition legitimately produces `previous_value` non-null and `current_value=None`, while retaining real citation evidence in `current_observed_at`, `current_snapshot_id`, and `current_source_url`. | Storing `previous` and `current` as `jsonb` columns. Rejected: sacrifices schema-level type enforcement, complicates SQL queries, and hides foreign keys. Making `current_value` `NOT NULL` was rejected because it breaks [ADR 0006](0006-disclosure-status-semantics.md) disclosure transitions. |
| 2 | **`changes` immutability** | `BEFORE UPDATE` trigger on `(id, detected_at)`. | Mirrors the exact mechanism selected in PR #48 §11 for `source_items`. Directly fulfills [ADR 0008](0008-cursor-pagination-contract.md) §5.D storage-level immutability for the `/v1/changes` keyset pagination tuple `(detected_at DESC, id DESC)`. | Relying solely on Pydantic freezing or repository checks. Rejected: application-layer only, bypassable by raw SQL or alternate clients. Column-level SQL privileges were rejected in PR #48 §11 as operationally burdensome. |
| 3 | **`digests` table & immutability** | Native `DATE` column for `digest_date`; `BEFORE UPDATE` trigger on `(id, digest_date)`. | Fulfills [ADR 0008](0008-cursor-pagination-contract.md) §5.B and §5.D for `/v1/digests` ordering `(digest_date DESC, id DESC)`. | Unconstrained updates or string-based date storage. Rejected: string dates permit invalid days (e.g. "2026-13-40") and mutable ordering fields break keyset pagination traversals. |
| 4 | **`change_sets` & `digest_claims`** | Mutable relational tables. `digest_claims` adds `digest_id UUID NOT NULL REFERENCES digests(id) ON DELETE RESTRICT`. Snapshot arrays use `UUID[]`. | Neither table participates in pagination ordering tuples, so triggers are unnecessary. `DigestClaim` only exists within `Digest.claims` in the domain model; adding `digest_id` as a `NOT NULL` foreign key maintains referential integrity (mirroring `document_snapshots.source_item_id` in PR #48 §6). Native `UUID[]` arrays mirror `text[]` in PR #48 §10. | Making `change_sets` immutable. Rejected: `review_status` updates are part of the domain lifecycle. Nullable `digest_id` was rejected because standalone claims do not exist in the contract. |
| 5 | **`extracted_facts` immutability & current state** | Whole-row immutable via `BEFORE UPDATE` and `BEFORE DELETE` triggers. Current fact resolved by highest UUIDv7 `id` per `(company, product, field)`. Rows store `observed_at` and `source_url` from `_FieldRecord`. | Extracted facts represent primary evidence and must never be mutated or deleted in-place (provenance requirement). Rows store `observed_at` and `source_url` (from `_FieldRecord`) so that `FactStore` hydration after restart preserves the provenance needed to build `previous: FactObservation` on future Changes. Because UUIDv7 values are monotonically time-ordered, the current fact is naturally the row with the maximum `id` for a given `(company, product, field)`. | Mutable `is_current` boolean flag or pointer column. Rejected: updating an `is_current` flag requires mutating historical rows, violating whole-row immutability. |
| 6 | **`subjects` registry table** | Natural composite primary key `(company, product)`. No surrogate UUID. | In `shared/schemas.py:151-165`, `Subject` is a frozen, hashable value object with no `id` attribute. Introducing an artificial surrogate key would represent an unvetted shared-contract change. `FactStore` indexes by normalized `(company, product)` tuple. | Adding a surrogate `subject_id UUID PRIMARY KEY`. Rejected: unnecessary abstraction that drifts from the shared contract model. |
| 7 | **Module ownership & database connection** | Intelligence-owned under `src/ai_daily_digest/intelligence/db/`. Engine imported from `shared/db/`. | Maintains strict modular boundaries: intelligence defines its own ORM models and repository implementations. Shared database connectivity lives in `shared/db/` per the working assumption on PR #48 §12.3. | Placing intelligence tables or repositories in `ingestion/db/` or `shared/`. Rejected: violates module review stewardship (Person B owns intelligence). |

---

## 4. Proposed Database Schema (PostgreSQL DDL)

The schema definitions below will be managed via Alembic migrations owned by the intelligence module.

```sql
-- -----------------------------------------------------------------------------
-- Subjects Registry
-- -----------------------------------------------------------------------------
CREATE TABLE subjects (
    company             TEXT NOT NULL,
    product             TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (company, product)
);

-- -----------------------------------------------------------------------------
-- Extracted Facts (Whole-row immutable evidence)
-- -----------------------------------------------------------------------------
CREATE TABLE extracted_facts (
    id                  UUID PRIMARY KEY,
    snapshot_id         UUID NOT NULL, -- references document_snapshots(id)
    company             TEXT NOT NULL,
    product             TEXT NOT NULL,
    field               TEXT NOT NULL,
    value               TEXT,          -- NULL when disclosure_status = 'not_disclosed'
    disclosure_status   TEXT NOT NULL, -- 'disclosed' | 'not_disclosed'
    extraction_method   TEXT NOT NULL, -- 'deterministic' | 'llm_structured_output'
    extraction_model    TEXT,
    prompt_version      TEXT,
    quoted_span         TEXT,
    confidence          DOUBLE PRECISION,
    -- FieldRecord provenance: tracked alongside facts so FactStore hydration
    -- after a restart can populate previous FactObservation on subsequent Changes (facts.py:162-175, 341-348)
    observed_at         TIMESTAMPTZ NOT NULL,
    source_url          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (company, product) REFERENCES subjects(company, product) ON DELETE RESTRICT
);

-- Efficient resolution of current fact and field history:
CREATE INDEX idx_extracted_facts_subject_field_id
    ON extracted_facts (company, product, field, id DESC);

-- -----------------------------------------------------------------------------
-- ChangeSets (Batch-level grouping of changes)
-- -----------------------------------------------------------------------------
CREATE TABLE change_sets (
    id                      UUID PRIMARY KEY,
    company                 TEXT NOT NULL,
    product                 TEXT NOT NULL,
    previous_snapshot_ids   UUID[] NOT NULL DEFAULT '{}',
    current_snapshot_ids    UUID[] NOT NULL DEFAULT '{}',
    review_status           TEXT NOT NULL DEFAULT 'pending',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (company, product) REFERENCES subjects(company, product) ON DELETE RESTRICT
);

-- -----------------------------------------------------------------------------
-- Changes (Individual field-level detected changes)
-- -----------------------------------------------------------------------------
CREATE TABLE changes (
    id                      UUID PRIMARY KEY,
    detected_at             TIMESTAMPTZ NOT NULL,
    change_set_id           UUID NOT NULL REFERENCES change_sets(id) ON DELETE RESTRICT,
    company                 TEXT NOT NULL,
    product                 TEXT NOT NULL,
    field                   TEXT NOT NULL,
    change_type             TEXT NOT NULL,
    confidence              DOUBLE PRECISION NOT NULL,
    review_status           TEXT NOT NULL DEFAULT 'pending',

    -- Previous observation (NULL if first disclosure)
    previous_value          TEXT,
    previous_observed_at    TIMESTAMPTZ,
    previous_snapshot_id    UUID,
    previous_source_url     TEXT,

    -- Current observation (value is NULL if change_type = 'not_disclosed')
    current_value           TEXT,
    current_observed_at     TIMESTAMPTZ NOT NULL,
    current_snapshot_id     UUID NOT NULL,
    current_source_url      TEXT NOT NULL,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (company, product) REFERENCES subjects(company, product) ON DELETE RESTRICT
);

-- Keyset pagination index for GET /v1/changes (ADR 0008 §4):
CREATE INDEX idx_changes_pagination
    ON changes (detected_at DESC, id DESC);

-- Query filter indexes:
CREATE INDEX idx_changes_subject_field
    ON changes (company, product, field);

-- -----------------------------------------------------------------------------
-- Digests (Published or draft daily updates)
-- -----------------------------------------------------------------------------
CREATE TABLE digests (
    id                  UUID PRIMARY KEY,
    digest_date         DATE NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft',
    title               TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- Keyset pagination index for GET /v1/digests (ADR 0008 §4):
CREATE INDEX idx_digests_pagination
    ON digests (digest_date DESC, id DESC);

-- -----------------------------------------------------------------------------
-- Digest Claims (Factual claims linked to a digest)
-- -----------------------------------------------------------------------------
CREATE TABLE digest_claims (
    id                      UUID PRIMARY KEY,
    digest_id               UUID NOT NULL REFERENCES digests(id) ON DELETE RESTRICT,
    text                    TEXT NOT NULL,
    citation_snapshot_ids   UUID[] NOT NULL DEFAULT '{}',
    validation_status       TEXT NOT NULL DEFAULT 'pending',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_digest_claims_digest_id
    ON digest_claims (digest_id);
```

---

## 5. Storage-Level Immutability Enforcement

To satisfy [ADR 0008](0008-cursor-pagination-contract.md) §5.D and PR #48 §11:

### 5.1 Protected Ordering Columns (`changes` and `digests`)

Mutating ordering columns would corrupt keyset traversal cursors. The trigger functions below abort any transaction attempting to update these columns:

```sql
-- Function to protect changes(id, detected_at)
CREATE OR REPLACE FUNCTION check_changes_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'Cannot update immutable column changes.id';
    END IF;
    IF NEW.detected_at IS DISTINCT FROM OLD.detected_at THEN
        RAISE EXCEPTION 'Cannot update immutable column changes.detected_at';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_changes_ordering_columns
BEFORE UPDATE ON changes
FOR EACH ROW
EXECUTE FUNCTION check_changes_immutability();

-- Function to protect digests(id, digest_date)
CREATE OR REPLACE FUNCTION check_digests_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'Cannot update immutable column digests.id';
    END IF;
    IF NEW.digest_date IS DISTINCT FROM OLD.digest_date THEN
        RAISE EXCEPTION 'Cannot update immutable column digests.digest_date';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_digests_ordering_columns
BEFORE UPDATE ON digests
FOR EACH ROW
EXECUTE FUNCTION check_digests_immutability();
```

### 5.2 Whole-Row Immutability (`extracted_facts`)

`extracted_facts` represent historical provenance evidence and cannot be updated or deleted:

```sql
CREATE OR REPLACE FUNCTION reject_row_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only: updates and deletes are prohibited', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_extracted_facts_immutable_update
BEFORE UPDATE ON extracted_facts
FOR EACH ROW
EXECUTE FUNCTION reject_row_mutation();

CREATE TRIGGER trg_protect_extracted_facts_immutable_delete
BEFORE DELETE ON extracted_facts
FOR EACH ROW
EXECUTE FUNCTION reject_row_mutation();
```

---

## 6. Testing and Verification Requirements

Per [ADR 0008](0008-cursor-pagination-contract.md) §14, any persistence implementation PR based on this ADR must implement the mandatory 4-part immutability test suite for each protected table:

1. **Reject update:** Directly executing an `UPDATE` on protected columns (`changes.id`, `changes.detected_at`, `digests.id`, `digests.digest_date`, or any column in `extracted_facts`) raises a database error.
2. **Safe rollback:** The failed update aborts the transaction cleanly without partial writes or database corruption.
3. **Value survival:** Re-reading the record in a new transaction confirms the original value is preserved.
4. **No repository update path:** The repository protocol and implementation expose no methods for mutating protected columns.

Additional required test suites:
- **Keyset pagination query test:** Keyset traversal using `(detected_at, id) < (cursor_time, cursor_id)` and `(digest_date, id) < (cursor_date, cursor_id)` fetches `limit + 1` rows, slices correctly, and preserves pagination consistency.
- **Current fact resolution test:** Adding new `ExtractedFact` records for an existing `(company, product, field)` updates the resolved current fact (highest UUIDv7 ID) and maintains the full append-only history.
- **Disclosure-transition test:** Inserting `change_type="not_disclosed"` with `current_value=NULL` succeeds and preserves evidence fields (`current_snapshot_id`, `current_observed_at`, `current_source_url`).

---

## 7. Deferred Scope

The following areas are explicitly deferred to separate decisions and implementation PRs:

| Deferred Item | Owner & Target | Reason |
|---|---|---|
| SQLAlchemy engine / session factory placement | Person A / PR #48 resolution | Waiting for PR #48 review resolution on `shared/db/` vs `ingestion/db/`. |
| FastAPI route wiring (`/v1/changes`, `/v1/digests`) | Person C / ADR 0010 | HTTP endpoints, route contracts, and serialization models belong to Delivery. |
| Vector index & embeddings (`pgvector`) | Person B / Future Phase | Semantic search is a derived index and not required for baseline relational persistence. |
| Subscriptions and email delivery tables | Person C | Delivery-internal persistence. |
| Review workflow state machine | Person B / Future ADR | Detailed transition rules for `review_status="validated"` or `"rejected"`. |
| Digest uniqueness and version-history policy | Person B / Future ADR | [ADR 0008](0008-cursor-pagination-contract.md) §5.B explicitly defers whether multiple published versions or revisions may share a `digest_date`. The schema permits multiple rows per date because keyset traversal is totally ordered by `(digest_date DESC, id DESC)`. A uniqueness constraint (e.g. partial index on published date) is deferred to that lifecycle decision. |

---

## 8. Acceptance Checklist

- [ ] **Person A (Ingestion Steward):**
  - Confirms schema compatibility with `source_items` and `document_snapshots` (referential integrity from `extracted_facts.snapshot_id`).
  - Confirms engine assumption alignment once PR #48 review resolves.
- [ ] **Person B (Intelligence Steward — Author):**
  - Confirms all domain invariants from `FactStore`, `Change`, and `Digest` are preserved.
  - Verifies that `extracted_facts` and `changes` accurately capture disclosure semantics ([ADR 0006](0006-disclosure-status-semantics.md)).
- [ ] **Person C (Delivery Steward):**
  - Confirms keyset indexes `idx_changes_pagination` and `idx_digests_pagination` match the requirements of [ADR 0008](0008-cursor-pagination-contract.md) §4 and §12.
  - Confirms that public summary models (`ChangeSummary`, `DigestSummary`) can be projected efficiently from this schema.
