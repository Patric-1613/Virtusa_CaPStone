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
- **Business chronology ordering tuples:** [ADR 0007](0007-uuid-v7-identifier-strategy.md) establishes that business chronology derives exclusively from an explicit business timestamp, with UUIDv7 serving solely as an opaque tie-breaker. Keyset pagination ordering tuples mandated by [ADR 0008](0008-cursor-pagination-contract.md) (`(detected_at DESC, id DESC)` for `/v1/changes`, `(digest_date DESC, id DESC)` for `/v1/digests`) and current-fact resolution (`(observed_at DESC, id DESC)`) adhere strictly to this `(business_timestamp, id)` paradigm.
- **Canonical subject identity:** Following `FactStore._subject_key()` (`intelligence/facts.py:115-118`), subjects are keyed in the database by canonical normalized strings (`company_key`, `product_key`) computed via `normalise_name()`, preventing duplicate records from incidental casing, whitespace, or punctuation differences.
- **Cross-table relational consistency:** Composite foreign keys guarantee that a `Change` cannot reference a `ChangeSet` belonging to a different subject (mirroring the referential integrity pattern in PR #48 §9).
- **Three-layer immutability enforcement:** As established in [ADR 0008](0008-cursor-pagination-contract.md) §5.D and PR #48 §11, immutability is guaranteed across model freezing, repository method restriction, and database storage-level trigger enforcement.
- **Trigger-based storage mechanism:** Rather than managing complex column-level privileges, `BEFORE UPDATE` database triggers are used to reject in-place mutations on protected ordering columns and evidence attributes. On `changes`, every column except `review_status` is immutable. Once a `Digest` enters `published` status, its title, claims, and claim citations are permanently locked against update or deletion.
- **Whole-row immutability for source evidence:** `extracted_facts` mirror `document_snapshots` from PR #48 §11 — once written, facts are immutable evidence. Ordinary `UPDATE`, `DELETE`, and `TRUNCATE` operations are rejected via triggers. Corrections or updates append a new row and retain full provenance.
- **Referential integrity across module boundaries:** All snapshot citations reference `document_snapshots(id)` with `ON DELETE RESTRICT` foreign keys. Multi-value citations are stored in normalized join tables rather than loose arrays, ensuring database-enforced existence and retention of evidence.

---

## 2. What already exists — verified against the tree

The shared contracts and intelligence domain models already define the required fields, invariants, and validation boundaries. The citations below for `shared/schemas.py`, `intelligence/facts.py`, and `docs/adr/0008-cursor-pagination-contract.md` have been opened and confirmed in the repository tree. Citations referencing `ADR 0002` represent proposed text from PR #48 (open, unmerged as of this writing; section numbering must be re-verified upon merge):

| Component | Existing definition / location | Status & Invariants |
|---|---|---|
| `Change.id` | `src/ai_daily_digest/shared/schemas.py:439` | `id: Uuid7Id = Field(frozen=True)` ([ADR 0007](0007-uuid-v7-identifier-strategy.md) / [ADR 0008](0008-cursor-pagination-contract.md)) |
| `Change.detected_at` | `src/ai_daily_digest/shared/schemas.py:447` | `detected_at: OrderingTimestamp = Field(frozen=True)` (UTC, microseconds preserved, naive rejected) |
| `Change` shape validator | `src/ai_daily_digest/shared/schemas.py:450-459` | `_require_valid_change_shape` delegates to `validate_change_shape()` |
| `ChangeSet` aggregate | `src/ai_daily_digest/shared/schemas.py:461-472` | `id`, `subject: Subject`, `changes: list[Change]`, `previous_snapshot_ids`, `current_snapshot_ids`, `review_status` |
| `Digest.id` | `src/ai_daily_digest/shared/schemas.py:520` | `id: Uuid7Id = Field(frozen=True)` |
| `Digest.digest_date` | `src/ai_daily_digest/shared/schemas.py:521` | `digest_date: date = Field(frozen=True)` (Native calendar date, YYYY-MM-DD on wire) |
| `DigestClaim` | `src/ai_daily_digest/shared/schemas.py:500-509` | `id: Uuid7Id`, `text: str`, `citation_snapshot_ids: list[Uuid7Id]`, `validation_status: ClaimValidationStatus` |
| `ExtractedFact` | `src/ai_daily_digest/shared/schemas.py:195-298` | `id: Uuid7Id`, `snapshot_id: Uuid7Id`, `field: str`, `value: str \| None`, `disclosure_status`, `extraction_method`, `extraction_model`, `prompt_version`, `quoted_span`, `confidence`; evidence invariants enforced via model validators ([ADR 0004](0004-extracted-fact-keeps-evidence.md), [ADR 0006](0006-disclosure-status-semantics.md)) |
| `Subject` value type | `src/ai_daily_digest/shared/schemas.py:151-165` | `company: str`, `product: str`; frozen, hashable value object without a surrogate ID |
| `FactStore._subject_key` | `src/ai_daily_digest/intelligence/facts.py:115-118` | `(normalise_name(company), normalise_name(product))` defines the canonical identity in-memory |
| `FactStore._FieldRecord` | `src/ai_daily_digest/intelligence/facts.py:162-175` | In-memory state: `current: ExtractedFact \| None`, provenance (`current_snapshot_id`, `current_source_url`, `current_observed_at`), and `history: list[ExtractedFact]`. `update_fact()` (`facts.py:341-348, 359-363`) tracks `source_url` and `observed_at` alongside each fact so subsequent changes can construct `previous: FactObservation` with full provenance. This is the source of truth for why `extracted_facts.observed_at` and `.source_url` exist in storage despite not being on `ExtractedFact` itself. |
| Ordering tuple protection | `docs/adr/0008-cursor-pagination-contract.md:212-219` | Exactly 6 protected columns across the service: `SourceItem.id`, `SourceItem.first_fetched_at`, `Change.id`, `Change.detected_at`, `Digest.id`, `Digest.digest_date` |
| Protected-column test suite | `docs/adr/0008-cursor-pagination-contract.md:767-775` | Mandatory 4-part test pattern: reject update, safe rollback, stored value survives, no repository mutation path |
| Keyset pagination index | PR #48 §9.1 (open, unmerged) | Composite descending index `(first_fetched_at DESC, id DESC)` precedent |
| Composite FK consistency | PR #48 §9.2 (open, unmerged) | Composite FK constraint precedent (`source_items.latest_snapshot_id` → `document_snapshots(id, source_item_id)`) |
| Trigger-based immutability | PR #48 §11 (open, unmerged) | Precedent: `BEFORE UPDATE` trigger function raising exception on protected column modification |
| Immutable snapshots | PR #48 §11 (open, unmerged) | Precedent: row-level `UPDATE`/`DELETE` and statement-level `TRUNCATE` triggers protecting raw source snapshots |

---

## 3. Architectural Decisions

| # | Decision | Selected Approach | Rationale | Rejected Alternatives |
|---|---|---|---|---|
| 1 | **`changes` table schema & observations** | Flattened typed columns (`previous_value`, `previous_observed_at`, `previous_snapshot_id`, `previous_source_url`, and `current_*`). Both `current_value` and `current_source_url` are **nullable** (`TEXT`). | Follows PR #48 §10's rationale against `jsonb` for fixed, small shapes: yields strict database type-checking, direct SQL indexing, and explicit nullability semantics. Per [ADR 0006](0006-disclosure-status-semantics.md), a `change_type="not_disclosed"` transition legitimately produces `previous_value` non-null and `current_value=None`, while retaining real citation evidence in `current_observed_at`, `current_snapshot_id`, and `current_source_url`. Furthermore, `FactObservation.source_url: HttpUrl \| None = None` (`shared/schemas.py:314`) and `FactStore.update_fact(source_url: str \| None, ...)` (`intelligence/facts.py:238`) explicitly permit `None`, requiring `current_source_url` to be nullable. Regarding `current_observed_at` and `extracted_facts.observed_at`: while `FactObservation.observed_at` is typed as optional in `shared/schemas.py:312`, the real production pipeline path in `FactStore.update_fact()` always supplies a required `observed_at: datetime`. It is recommended (Option a) to tighten `FactObservation.observed_at` to required via the shared-contract review process (`AGENTS.md`) rather than relaxing the database column to nullable (Option b). | Storing `previous` and `current` as `jsonb` columns. Rejected: sacrifices schema-level type enforcement, complicates SQL queries, and hides foreign keys. Making `current_value` or `current_source_url` `NOT NULL` was rejected because it contradicts the Python domain model and breaks [ADR 0006](0006-disclosure-status-semantics.md) disclosure transitions. |
| 2 | **`changes` immutability** | `BEFORE UPDATE` trigger on `changes` enforcing immutability of **all columns except `review_status`**. | Fulfills [ADR 0008](0008-cursor-pagination-contract.md) §5.D storage-level immutability for the keyset ordering tuple `(detected_at DESC, id DESC)` and prevents direct SQL tampering with evidence columns (`value`, snapshot IDs, `source_url`, `confidence`, `change_type`, subject keys). Only `review_status` transitions (`pending` → `validated` / `rejected`) are mutable. | Protecting only `(id, detected_at)`. Rejected: allows silent mutation of historical evidence rows via direct SQL or compromised clients. |
| 3 | **`digests` table, immutability & publish uniqueness** | Native `DATE` column for `digest_date`; `BEFORE UPDATE` trigger on `(id, digest_date)`. Once `status = 'published'`, `title` is permanently frozen, and status cannot transition away from `published`. Unique partial index `uq_digests_one_published_per_date` on `(digest_date) WHERE status = 'published'`. | Fulfills [ADR 0008](0008-cursor-pagination-contract.md) §5.B, §5.D, and §12 for `/v1/digests` ordering `(digest_date DESC, id DESC)`. Retried or reprocessed daily pipeline runs require publish idempotency: multiple draft attempts for a date are legitimate during drafting/review, but exactly one published digest may exist per calendar date. An attempt to publish a second digest fails closed at the database level. Partial indexing `WHERE status = 'published'` also ensures list queries scan only published records. | Unconstrained updates or string-based date storage. Rejected: string dates permit invalid days (e.g. "2026-13-40") and mutable ordering fields break keyset pagination traversals. Omitting the unique publish index was rejected because it allows duplicate published digests on retry, breaking consumer idempotency. |
| 4 | **Referential integrity & join tables** | Normalized join tables (`change_set_snapshot_citations`, `digest_claim_citations`) with `ON DELETE RESTRICT` foreign keys. `digest_claims.digest_id` is `UUID NOT NULL REFERENCES digests(id) ON DELETE RESTRICT`. Once a digest is published, its child `digest_claims` and `digest_claim_citations` are frozen against update or delete. | Snapshot citations reference real rows in `document_snapshots(id)` that must exist and must not be deleted while cited by claims or changesets. While PR #48 §10 used `text[]` arrays for `authors` and `tags`, those are unstructured scalar keywords without foreign entity targets; evidence citations demand strict foreign-key integrity. Storing `source_url` and `observed_at` denormalized alongside snapshot IDs is snapshot-derived provenance captured at extraction/detection time for fast summary projection and offline auditing; the write protocol populates them directly from the cited `document_snapshots.fetched_at` and `source_items.canonical_url`, preventing independent drift. Child claims and citations of published digests are permanently locked via triggers. | PostgreSQL `UUID[]` arrays. Rejected: arrays cannot enforce foreign-key constraints in PostgreSQL, allowing dangling snapshot IDs or silent deletion of primary evidence. |
| 5 | **Current-fact resolution & advancement** | Current fact resolved via explicit `(observed_at DESC, id DESC)` ordering. Dedicated `current_facts` pointer table updated conditionally: `(current_facts.observed_at, current_facts.id) < (EXCLUDED.observed_at, EXCLUDED.id)`. | Conforms strictly to [ADR 0007](0007-uuid-v7-identifier-strategy.md) lines 154-161 (UUID is an opaque tie-breaker, never primary chronology). An explicit pointer table updated atomically with conditional comparison mirrors PR #48 §13's `advance_latest_snapshot` pattern: late-arriving observations with older `observed_at` are stored in append-only history but do not advance current state or emit false Changes. | Resolving current fact by highest `id`. Rejected: violates ADR 0007. Mutable `is_current` flag on `extracted_facts` was rejected because it violates whole-row immutability. Dynamic `SELECT ... ORDER BY observed_at DESC, id DESC LIMIT 1` on every read was rejected due to O(N) table scans on deep field histories. |
| 6 | **Canonical subject identity & consistency** | `subjects` primary key is `(company_key, product_key)` derived via `normalise_name()`, storing original `company`/`product` display values (first-seen wins). Child tables (`extracted_facts`, `current_facts`, `change_sets`, `changes`) key on `(company_key, product_key)`. `change_sets` enforces `UNIQUE (id, company_key, product_key)`; `changes` references it via composite FK `(change_set_id, company_key, product_key)`. | Replicates `FactStore._subject_key()` in SQL so that equivalent inputs (`"OpenAI"` / `"openai"` / `"OpenAI."`) collide deterministically into a single subject. The composite FK on `changes` mirrors PR #48 §9 (`source_items.latest_snapshot_id` → `document_snapshots(id, source_item_id)`), ensuring a Change cannot reference a ChangeSet belonging to a different subject. | Raw `PRIMARY KEY (company, product)`. Rejected: allows duplicate subject rows due to casing or punctuation differences. Plain `change_set_id` FK on `changes`. Rejected: permits a Change to reference a ChangeSet with a mismatched subject. |
| 7 | **Module ownership & database connection** | Intelligence-owned under `src/ai_daily_digest/intelligence/db/`. Engine imported from `shared/db/`. | Maintains strict modular boundaries: intelligence defines its own ORM models and repository implementations. Shared database connectivity lives in `shared/db/` per the working assumption on PR #48 §12.3. | Placing intelligence tables or repositories in `ingestion/db/` or `shared/`. Rejected: violates module review stewardship (Person B owns intelligence). |

> **Risk note on canonical subject keys:** Consistent with PR #48's treatment of `source_items.dedupe_key`, `company_key` and `product_key` are generated application-side by `normalise_name()` before persistence. Any future modification to `normalise_name()`'s normalization rules represents a canonicalization policy change and requires an explicit migration and backfill plan, not an uncoordinated code change.

---

## 4. Proposed Database Schema (PostgreSQL DDL)

The schema definitions below will be managed via Alembic migrations owned by the intelligence module.

```sql
-- -----------------------------------------------------------------------------
-- Subjects Registry (Canonical Subject Identity)
-- -----------------------------------------------------------------------------
CREATE TABLE subjects (
    company_key         TEXT NOT NULL,
    product_key         TEXT NOT NULL,
    company             TEXT NOT NULL, -- original display name (first-seen wins)
    product             TEXT NOT NULL, -- original display name (first-seen wins)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (company_key, product_key)
);

-- -----------------------------------------------------------------------------
-- Extracted Facts (Whole-row immutable evidence)
-- -----------------------------------------------------------------------------
CREATE TABLE extracted_facts (
    id                  UUID PRIMARY KEY,
    snapshot_id         UUID NOT NULL REFERENCES document_snapshots(id) ON DELETE RESTRICT,
    company_key         TEXT NOT NULL,
    product_key         TEXT NOT NULL,
    field               TEXT NOT NULL,
    value               TEXT,          -- NULL when disclosure_status = 'not_disclosed'
    disclosure_status   TEXT NOT NULL CHECK (disclosure_status IN ('disclosed', 'not_disclosed')),
    extraction_method   TEXT NOT NULL CHECK (extraction_method IN ('deterministic', 'llm_structured_output')),
    extraction_model    TEXT,
    prompt_version      TEXT,
    quoted_span         TEXT,
    confidence          DOUBLE PRECISION CHECK (
                            confidence IS NULL OR
                            (confidence >= 0 AND confidence <= 1 AND confidence = confidence)
                        ),
    -- FieldRecord provenance: tracked alongside facts so FactStore hydration
    -- after a restart can populate previous FactObservation on subsequent Changes (facts.py:162-175, 341-348)
    observed_at         TIMESTAMPTZ NOT NULL,
    source_url          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (company_key, product_key) REFERENCES subjects(company_key, product_key) ON DELETE RESTRICT
);

-- Chronological business ordering index:
CREATE INDEX idx_extracted_facts_subject_field_chronology
    ON extracted_facts (company_key, product_key, field, observed_at DESC, id DESC);

CREATE INDEX idx_extracted_facts_snapshot_id
    ON extracted_facts (snapshot_id);

-- -----------------------------------------------------------------------------
-- Current Facts Pointer Table (Fast O(1) current state resolution)
-- -----------------------------------------------------------------------------
CREATE TABLE current_facts (
    company_key         TEXT NOT NULL,
    product_key         TEXT NOT NULL,
    field               TEXT NOT NULL,
    fact_id             UUID NOT NULL REFERENCES extracted_facts(id) ON DELETE RESTRICT,
    observed_at         TIMESTAMPTZ NOT NULL,
    id                  UUID NOT NULL, -- mirrors fact_id for keyset tie-breaking
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (company_key, product_key, field),
    FOREIGN KEY (company_key, product_key) REFERENCES subjects(company_key, product_key) ON DELETE RESTRICT
);

-- -----------------------------------------------------------------------------
-- ChangeSets (Batch-level grouping of changes)
-- -----------------------------------------------------------------------------
CREATE TABLE change_sets (
    id                  UUID PRIMARY KEY,
    company_key         TEXT NOT NULL,
    product_key         TEXT NOT NULL,
    review_status       TEXT NOT NULL DEFAULT 'pending'
                        CHECK (review_status IN ('pending', 'validated', 'rejected')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (company_key, product_key) REFERENCES subjects(company_key, product_key) ON DELETE RESTRICT,
    -- Composite unique constraint required for composite FK reference from changes:
    UNIQUE (id, company_key, product_key)
);

-- Normalized citations for change sets:
CREATE TABLE change_set_snapshot_citations (
    change_set_id       UUID NOT NULL REFERENCES change_sets(id) ON DELETE RESTRICT,
    snapshot_id         UUID NOT NULL REFERENCES document_snapshots(id) ON DELETE RESTRICT,
    kind                TEXT NOT NULL CHECK (kind IN ('previous', 'current')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (change_set_id, snapshot_id, kind)
);

CREATE INDEX idx_change_set_citations_snapshot_id
    ON change_set_snapshot_citations (snapshot_id);

-- -----------------------------------------------------------------------------
-- Changes (Individual field-level detected changes)
-- -----------------------------------------------------------------------------
CREATE TABLE changes (
    id                      UUID PRIMARY KEY,
    detected_at             TIMESTAMPTZ NOT NULL,
    change_set_id           UUID NOT NULL,
    company_key             TEXT NOT NULL,
    product_key             TEXT NOT NULL,
    field                   TEXT NOT NULL,
    change_type             TEXT NOT NULL, -- open string per ADR 0009
    confidence              DOUBLE PRECISION NOT NULL CHECK (
                                confidence >= 0 AND confidence <= 1 AND confidence = confidence
                            ),
    review_status           TEXT NOT NULL DEFAULT 'pending'
                            CHECK (review_status IN ('pending', 'validated', 'rejected')),

    -- Previous observation (NULL if first disclosure)
    previous_value          TEXT,
    previous_observed_at    TIMESTAMPTZ,
    previous_snapshot_id    UUID REFERENCES document_snapshots(id) ON DELETE RESTRICT,
    previous_source_url     TEXT,

    -- Current observation (value is NULL if change_type = 'not_disclosed'; URL nullable per contract)
    current_value           TEXT,
    current_observed_at     TIMESTAMPTZ NOT NULL,
    current_snapshot_id     UUID NOT NULL REFERENCES document_snapshots(id) ON DELETE RESTRICT,
    current_source_url      TEXT,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- Enforce that a Change references a valid Subject:
    FOREIGN KEY (company_key, product_key) REFERENCES subjects(company_key, product_key) ON DELETE RESTRICT,
    -- Enforce that a Change shares the exact subject of its parent ChangeSet (PR #48 §9 pattern):
    FOREIGN KEY (change_set_id, company_key, product_key)
        REFERENCES change_sets(id, company_key, product_key) ON DELETE RESTRICT
);

-- Keyset pagination index for GET /v1/changes (ADR 0008 §4):
CREATE INDEX idx_changes_pagination
    ON changes (detected_at DESC, id DESC);

-- Query filter indexes:
CREATE INDEX idx_changes_subject_field
    ON changes (company_key, product_key, field);

CREATE INDEX idx_changes_change_set_id
    ON changes (change_set_id);

CREATE INDEX idx_changes_previous_snapshot_id
    ON changes (previous_snapshot_id);

CREATE INDEX idx_changes_current_snapshot_id
    ON changes (current_snapshot_id);

-- -----------------------------------------------------------------------------
-- Digests (Published or draft daily updates)
-- -----------------------------------------------------------------------------
CREATE TABLE digests (
    id                  UUID PRIMARY KEY,
    digest_date         DATE NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'review', 'published')),
    title               TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- Unique index enforcing at most one published digest per calendar date (idempotent retries):
CREATE UNIQUE INDEX uq_digests_one_published_per_date
    ON digests (digest_date)
    WHERE status = 'published';

-- Keyset pagination index for GET /v1/digests (ADR 0008 §4 & §12: published-only in Phase 1):
CREATE INDEX idx_digests_pagination
    ON digests (digest_date DESC, id DESC)
    WHERE status = 'published';

-- -----------------------------------------------------------------------------
-- Digest Claims (Factual claims linked to a digest)
-- -----------------------------------------------------------------------------
CREATE TABLE digest_claims (
    id                  UUID PRIMARY KEY,
    digest_id           UUID NOT NULL REFERENCES digests(id) ON DELETE RESTRICT,
    text                TEXT NOT NULL,
    validation_status   TEXT NOT NULL DEFAULT 'pending'
                        CHECK (validation_status IN ('pending', 'supported', 'unsupported')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_digest_claims_digest_id
    ON digest_claims (digest_id);

-- Normalized citations for digest claims:
CREATE TABLE digest_claim_citations (
    claim_id            UUID NOT NULL REFERENCES digest_claims(id) ON DELETE RESTRICT,
    snapshot_id         UUID NOT NULL REFERENCES document_snapshots(id) ON DELETE RESTRICT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (claim_id, snapshot_id)
);

CREATE INDEX idx_digest_claim_citations_snapshot_id
    ON digest_claim_citations (snapshot_id);
```

---

## 5. Repository Protocols, Transaction Boundaries, and Publication Transitions

Following the structure of PR #48 §12–13, intelligence persistence defines clear transaction boundaries, idempotency semantics, and publication gates.

### 5.1 Transaction Units and Ownership

Repository methods **never** issue commits or rollbacks independently. The service or orchestrator layer (`intelligence/daily_run.py`, `intelligence/graph.py`) owns the transaction boundary via an injected unit-of-work or session context. Persistence writes are structured into three atomic units:

1. **Unit 1: Fact Ingestion & State Advancement (per field):**
   - Register subject in `subjects` (idempotent `ON CONFLICT (company_key, product_key) DO NOTHING`).
   - Append raw fact to `extracted_facts`.
   - Conditionally advance `current_facts` pointer.
   - *Atomicity:* An extracted fact is never stored without its subject registered, and the current pointer never advances unless the fact append succeeds.
2. **Unit 2: Change Detection Batch (per subject batch):**
   - Insert `change_sets` record.
   - Insert all associated `changes` records.
   - Insert citation rows into `change_set_snapshot_citations`.
   - *Atomicity:* A `ChangeSet` and its associated `Change` rows commit together. No partial change sets can appear in `/v1/changes`.
3. **Unit 3: Digest Assembly & Publication:**
   - Insert `digests` row (initially `draft` or `review`).
   - Insert all associated `digest_claims`.
   - Insert claim citations into `digest_claim_citations`.
   - *Atomicity:* A digest and its claim graph persist as an all-or-nothing unit.

### 5.2 Canonical Reconstruction Order for Child Lists

In the shared Python contract, `ChangeSet.changes`, `Digest.claims`, and snapshot citation lists are represented as typed Python lists. In `intelligence/draft_claims.py` and `intelligence/assemble_digest.py`, claims and changes are constructed deterministically in pipeline execution sequence; there is no independent reordering capability.

To eliminate ambiguity when reconstructing domain models from SQL queries without introducing artificial position columns:
- **`ChangeSet.changes`:** Reconstructed via `SELECT * FROM changes WHERE change_set_id = ? ORDER BY id ASC`. Because UUIDv7 IDs embed creation timestamps and sub-millisecond sequence numbers, ascending ID ordering reproduces the exact creation/insertion sequence.
- **`Digest.claims`:** Reconstructed via `SELECT * FROM digest_claims WHERE digest_id = ? ORDER BY id ASC`.
- **Citations:** Reconstructed via `ORDER BY created_at ASC, snapshot_id ASC`.

This ordering represents strict *insertion-order* reconstruction and is distinct from *business chronology* ordering, which remains governed by explicit timestamps (`observed_at`, `detected_at`, `digest_date`).

### 5.3 Concurrency and Conditional Advancement

When concurrent extraction runs process observations for the same `(company_key, product_key, field)`:
- Rows in `extracted_facts` are append-only and do not collide (each carries a unique UUIDv7 `id`).
- Pointer advancement in `current_facts` uses an atomic conditional `UPSERT`:
  ```sql
  INSERT INTO current_facts (company_key, product_key, field, fact_id, observed_at, id, updated_at)
  VALUES (:company_key, :product_key, :field, :fact_id, :observed_at, :fact_id, clock_timestamp())
  ON CONFLICT (company_key, product_key, field) DO UPDATE
  SET fact_id     = EXCLUDED.fact_id,
      observed_at = EXCLUDED.observed_at,
      id          = EXCLUDED.id,
      updated_at  = clock_timestamp()
  WHERE (current_facts.observed_at, current_facts.id) < (EXCLUDED.observed_at, EXCLUDED.id);
  ```
- If an incoming fact has an older `observed_at` than the stored current record, the `WHERE` clause evaluates to false. Zero rows are updated in `current_facts`. The out-of-order fact is safely preserved in `extracted_facts` history without clobbering newer state or generating a retroactive Change.

### 5.4 Fail-Closed Publication Transition

Per the core project invariant ("LLM output is never treated as evidence; claims must cite stored source records"), a digest cannot enter `published` status unless every claim is supported. To guarantee storage-level enforcement independent of application logic, a `BEFORE UPDATE` trigger on `digests` validates claim integrity:

```sql
CREATE OR REPLACE FUNCTION check_digest_publication_prerequisites()
RETURNS TRIGGER AS $$
DECLARE
    unsupported_count INTEGER;
    claim_count INTEGER;
    uncited_count INTEGER;
BEGIN
    IF NEW.status = 'published' AND (OLD.status IS DISTINCT FROM 'published') THEN
        -- Verify at least one claim exists
        SELECT COUNT(*) INTO claim_count
        FROM digest_claims
        WHERE digest_id = NEW.id;

        IF claim_count = 0 THEN
            RAISE EXCEPTION 'Cannot publish digest %: digest has no claims', NEW.id;
        END IF;

        -- Verify all claims have status 'supported'
        SELECT COUNT(*) INTO unsupported_count
        FROM digest_claims
        WHERE digest_id = NEW.id AND validation_status != 'supported';

        IF unsupported_count > 0 THEN
            RAISE EXCEPTION 'Cannot publish digest %: contains % unsupported claims', NEW.id, unsupported_count;
        END IF;

        -- Verify every claim has at least one valid citation
        SELECT COUNT(*) INTO uncited_count
        FROM digest_claims dc
        WHERE dc.digest_id = NEW.id
          AND NOT EXISTS (
              SELECT 1 FROM digest_claim_citations dcc WHERE dcc.claim_id = dc.id
          );

        IF uncited_count > 0 THEN
            RAISE EXCEPTION 'Cannot publish digest %: contains % claims without citations', NEW.id, uncited_count;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_digest_publication
BEFORE UPDATE ON digests
FOR EACH ROW
EXECUTE FUNCTION check_digest_publication_prerequisites();
```

---

## 6. Storage-Level Immutability Enforcement

To satisfy [ADR 0008](0008-cursor-pagination-contract.md) §5.D and PR #48 §11:

### 6.1 Comprehensive Immutability on `changes`

Mutating ordering columns would corrupt keyset traversal cursors, and modifying evidence columns would compromise historical traceability. `BEFORE UPDATE` triggers ensure that **every column on `changes` except `review_status` is immutable**:

```sql
CREATE OR REPLACE FUNCTION check_changes_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR
       NEW.detected_at IS DISTINCT FROM OLD.detected_at OR
       NEW.change_set_id IS DISTINCT FROM OLD.change_set_id OR
       NEW.company_key IS DISTINCT FROM OLD.company_key OR
       NEW.product_key IS DISTINCT FROM OLD.product_key OR
       NEW.field IS DISTINCT FROM OLD.field OR
       NEW.change_type IS DISTINCT FROM OLD.change_type OR
       NEW.confidence IS DISTINCT FROM OLD.confidence OR
       NEW.previous_value IS DISTINCT FROM OLD.previous_value OR
       NEW.previous_observed_at IS DISTINCT FROM OLD.previous_observed_at OR
       NEW.previous_snapshot_id IS DISTINCT FROM OLD.previous_snapshot_id OR
       NEW.previous_source_url IS DISTINCT FROM OLD.previous_source_url OR
       NEW.current_value IS DISTINCT FROM OLD.current_value OR
       NEW.current_observed_at IS DISTINCT FROM OLD.current_observed_at OR
       NEW.current_snapshot_id IS DISTINCT FROM OLD.current_snapshot_id OR
       NEW.current_source_url IS DISTINCT FROM OLD.current_source_url OR
       NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'Cannot update immutable columns on changes (only review_status may be updated)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_changes_immutability
BEFORE UPDATE ON changes
FOR EACH ROW
EXECUTE FUNCTION check_changes_immutability();
```

### 6.2 Immutability on `digests`, `digest_claims`, and Citations

`digests(id, digest_date)` are strictly protected against update. Furthermore, once a digest is published, its title is locked, unpublishing is prohibited, and all child claims and citations become permanently immutable:

```sql
CREATE OR REPLACE FUNCTION check_digests_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'Cannot update immutable column digests.id';
    END IF;
    IF NEW.digest_date IS DISTINCT FROM OLD.digest_date THEN
        RAISE EXCEPTION 'Cannot update immutable column digests.digest_date';
    END IF;
    -- Once published, title is locked and status cannot be reverted:
    IF OLD.status = 'published' AND NEW.title IS DISTINCT FROM OLD.title THEN
        RAISE EXCEPTION 'Cannot update title of an already published digest';
    END IF;
    IF OLD.status = 'published' AND NEW.status IS DISTINCT FROM 'published' THEN
        RAISE EXCEPTION 'Cannot unpublish an already published digest';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_digests_immutability
BEFORE UPDATE ON digests
FOR EACH ROW
EXECUTE FUNCTION check_digests_immutability();

-- Lock claims of published digests:
CREATE OR REPLACE FUNCTION check_published_digest_claims_immutability()
RETURNS TRIGGER AS $$
DECLARE
    parent_status TEXT;
    target_digest_id UUID;
BEGIN
    target_digest_id := COALESCE(OLD.digest_id, NEW.digest_id);
    SELECT status INTO parent_status FROM digests WHERE id = target_digest_id;
    IF parent_status = 'published' THEN
        RAISE EXCEPTION 'Cannot modify or delete claims for an already published digest (%)', target_digest_id;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_published_digest_claims_update
BEFORE UPDATE ON digest_claims
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claims_immutability();

CREATE TRIGGER trg_protect_published_digest_claims_delete
BEFORE DELETE ON digest_claims
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claims_immutability();

-- Lock citations of published digest claims:
CREATE OR REPLACE FUNCTION check_published_digest_claim_citations_immutability()
RETURNS TRIGGER AS $$
DECLARE
    parent_status TEXT;
    target_claim_id UUID;
BEGIN
    target_claim_id := COALESCE(OLD.claim_id, NEW.claim_id);
    SELECT d.status INTO parent_status
    FROM digests d
    JOIN digest_claims dc ON dc.digest_id = d.id
    WHERE dc.id = target_claim_id;

    IF parent_status = 'published' THEN
        RAISE EXCEPTION 'Cannot modify or delete citations for an already published digest';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_published_digest_claim_citations_update
BEFORE UPDATE ON digest_claim_citations
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claim_citations_immutability();

CREATE TRIGGER trg_protect_published_digest_claim_citations_delete
BEFORE DELETE ON digest_claim_citations
FOR EACH ROW
EXECUTE FUNCTION check_published_digest_claim_citations_immutability();
```

### 6.3 Whole-Row Immutability (`extracted_facts`)

`extracted_facts` represent historical provenance evidence and cannot be updated, deleted, or truncated:

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

-- Statement-level truncate protection
CREATE OR REPLACE FUNCTION reject_table_truncate()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only: truncate is prohibited', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_extracted_facts_immutable_truncate
BEFORE TRUNCATE ON extracted_facts
FOR EACH STATEMENT
EXECUTE FUNCTION reject_table_truncate();
```

### 6.4 Scope and Limitations of Storage Enforcement

Consistent with PR #48 §11:
- Database triggers protect ordinary application DML while enabled.
- Triggers do **not** prevent a PostgreSQL superuser or table owner from running `ALTER TABLE ... DISABLE TRIGGER`, dropping tables (`DROP TABLE`), or executing catalog updates.
- Operational security requires that the application runtime connects via a restricted PostgreSQL role granted only `SELECT`, `INSERT`, and constrained `UPDATE` privileges (with no `DELETE`, `TRUNCATE`, or DDL privileges). Restricting application database privileges is a required production deployment gate.

---

## 7. Testing and Verification Requirements

Per [ADR 0008](0008-cursor-pagination-contract.md) §14, any persistence implementation PR based on this ADR must implement the mandatory 4-part immutability test suite for each protected table:

1. **Reject update:** Directly executing an `UPDATE` on protected columns (`changes` evidence fields, `changes.detected_at`, `digests.id`, `digests.digest_date`, or any column in `extracted_facts`) raises a database error.
2. **Safe rollback:** The failed update aborts the transaction cleanly without partial writes or database corruption.
3. **Value survival:** Re-reading the record in a new transaction confirms the original value is preserved.
4. **No repository update path:** The repository protocol and implementation expose no methods for mutating protected columns.

Additional required integration test suites:
- **Canonical subject collision & idempotency test:** Inserting `"OpenAI"` then `"openai"` then `"OpenAI."` for the same product resolves idempotently to a single row in `subjects` with canonical key `("openai", ...)` and stores the first-seen display casing.
- **ChangeSet↔Change subject consistency test:** Directly attempting to insert a `changes` row whose `(company_key, product_key)` does not match the referenced `change_set_id` fails with a foreign key violation.
- **Published digest immutability test:**
  - Attempting to update `title` or revert `status` on a published `digests` row fails with an exception.
  - Attempting to update or delete a `digest_claims` or `digest_claim_citations` row belonging to a published digest fails with an exception.
- **Publish idempotency test:** Attempting to publish two distinct digests for the exact same `digest_date` results in the second publish failing closed via `uq_digests_one_published_per_date`.
- **Statement-level truncate rejection:** Executing `TRUNCATE TABLE extracted_facts` fails with an explicit exception and preserves all rows.
- **Referential integrity & deletion restriction:**
  - Attempting to insert an `extracted_facts`, `changes`, or citation row with a non-existent `snapshot_id` fails with a foreign key violation.
  - Attempting to delete a `document_snapshots` row that is referenced by an `extracted_facts`, `changes`, or citation row fails with `ON DELETE RESTRICT`.
- **Out-of-order write & conditional advancement test:** Inserting an `extracted_facts` row with an older `(observed_at, id)` successfully appends to history but leaves `current_facts` unchanged and emits no Change.
- **Fail-closed publication transition test:**
  - Attempting to update `digests.status` to `'published'` on a digest with zero claims fails.
  - Attempting to update `digests.status` to `'published'` on a digest containing any claim with `validation_status != 'supported'` fails.
  - Attempting to update `digests.status` to `'published'` on a digest containing an uncited claim fails.
  - Transitioning a digest where all claims are `'supported'` and cited succeeds.
- **Keyset pagination query test:** Keyset traversal using `(detected_at, id) < (cursor_time, cursor_id)` and `(digest_date, id) < (cursor_date, cursor_id)` fetches `limit + 1` rows, slices correctly, and preserves pagination consistency.
- **Disclosure-transition test:** Inserting `change_type="not_disclosed"` with `current_value=NULL` succeeds and preserves evidence fields (`current_snapshot_id`, `current_observed_at`, `current_source_url`).
- **Transaction rollback & atomicity test:** An intentional error during ChangeSet citation insertion rolls back the entire batch, ensuring no orphan `ChangeSet` or `Change` rows remain.

---

## 8. Deferred Scope

The following areas are explicitly deferred to separate decisions and implementation PRs:

| Deferred Item | Owner & Target | Reason |
|---|---|---|
| SQLAlchemy engine / session factory placement | Person A / PR #48 resolution | Waiting for PR #48 review resolution on `shared/db/` vs `ingestion/db/`. |
| FastAPI route wiring (`/v1/changes`, `/v1/digests`) | Person C / ADR 0010 | HTTP endpoints, route contracts, and serialization models belong to Delivery. |
| Vector index & embeddings (`pgvector`) | Person B / Future Phase | Semantic search is a derived index and not required for baseline relational persistence. |
| Subscriptions and email delivery tables | Person C | Delivery-internal persistence. |
| Shared `ReviewStatus` StrEnum promotion | Person B / Future ADR | `review_status` is currently typed as `str = "pending"` with documented values. Adding an explicit database `CHECK (review_status IN ('pending', 'validated', 'rejected'))` protects persistence now; promoting it to a shared `StrEnum` under ADR 0009 is deferred. |
| Digest superseding and correction workflow | Person B / Future ADR | Uniqueness of a published digest per calendar date is resolved by `uq_digests_one_published_per_date`. The operational lifecycle for deliberately retracting, superseding, or version-revising an already-published digest (e.g. edition v2) is deferred to a dedicated workflow decision. |

---

## 9. Acceptance Checklist

- [ ] **Person A (Ingestion Steward):**
  - Confirms schema compatibility with `source_items` and `document_snapshots` (referential integrity from `extracted_facts.snapshot_id`, `changes.current_snapshot_id`, and citations).
  - Confirms canonical subject keying `(company_key, product_key)` matches `normalise_name()` semantics.
  - Confirms composite `(change_set_id, company_key, product_key)` FK enforces subject consistency across changes and change sets.
  - Confirms engine assumption alignment once PR #48 review resolves.
- [ ] **Person B (Intelligence Steward — Author):**
  - Confirms all domain invariants from `FactStore`, `Change`, and `Digest` are preserved.
  - Verifies that `extracted_facts` and `changes` accurately capture disclosure semantics ([ADR 0006](0006-disclosure-status-semantics.md)).
  - Verifies current-fact conditional advancement matches business chronology requirements ([ADR 0007](0007-uuid-v7-identifier-strategy.md)).
  - Verifies evidence immutability protects historical change records and published digests.
- [ ] **Person C (Delivery Steward):**
  - Confirms keyset indexes `idx_changes_pagination` and `idx_digests_pagination` match the requirements of [ADR 0008](0008-cursor-pagination-contract.md) §4 and §12.
  - Confirms that public summary models (`ChangeSummary`, `DigestSummary`) can be projected efficiently from this schema.
  - Confirms publication prerequisites trigger enforces fail-closed guarantees.
