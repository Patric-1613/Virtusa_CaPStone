# 0002 — PostgreSQL system of record, deferred pgvector, and the Phase-1 persistence foundation

Status: Proposed
Date: 2026-08-24
Amended: 2026-09-04 — narrowed from a direction ("use PostgreSQL and pgvector") into a
concrete Phase-1 persistence decision the team can review before any SQLAlchemy model,
Alembic migration, or database dependency is added. Status stays **Proposed**; it is not
Accepted. Persons A, B, and C must review before implementation begins.

## Status detail

Person A (ingestion review steward) authors this amendment because the first persistence
tables (`source_items`, `document_snapshots`) are ingestion-owned. Persons B (intelligence)
and C (delivery) are review stewards: the decision fixes a shared contract
(`shared/schemas.py` gains no new resource type here, but the ORM must mirror its existing
models exactly), touches a shared integration file boundary (`delivery/api/app.py` wiring),
and unblocks [ADR 0008](0008-cursor-pagination-contract.md) PR 4 (`GET /v1/updates`), tracked
in issue #47 with Person B as the active author and Persons A and C as review stewards.

This amendment adds **no code, no dependency, no migration, no route, and no collector.** It
records decisions and their rejected alternatives so the implementation PR builds a
pre-agreed shape instead of inventing one under review pressure — the same discipline
[ADR 0007](0007-uuid-v7-identifier-strategy.md) and ADR 0008 already used.

## Phase-1 decision summary

The following is the exact proposal Persons A, B, and C are being asked to accept:

| Concern | Phase-1 decision |
|---|---|
| Database and vector search | PostgreSQL 17 is the system of record; pgvector remains planned but is not part of the first persistence PR. |
| Runtime database access | SQLAlchemy 2 async sessions over Psycopg 3; Alembic uses the same driver through the standard async migration bridge. |
| Initial tables | `source_items` and immutable `document_snapshots` only. |
| IDs and identity | Application-generated UUID v7; `dedupe_key`, `source_id`, and `canonical_url` are immutable after insertion. |
| Snapshot ownership/latest pointer | Composite FK proves ownership; a conditional update advances the pointer only to the newest `(fetched_at, id)`. |
| Lists | `authors` and normalized `tags` use PostgreSQL `text[]` in Phase 1. |
| Immutability | Restricted repository methods plus PostgreSQL update/delete triggers. |
| Cross-module reads | A narrow async read Protocol lives in `shared/`; its PostgreSQL adapter remains ingestion-owned. |
| Configuration | Typed `DatabaseConfig` lives in `shared/`; secrets remain environment-only. |
| CI | The existing test job gets a pinned `postgres:17` service and runs migrations plus real integration tests. |
| API pagination support | The foundation migration includes `(first_fetched_at DESC, id DESC)`. The endpoint remains a separate PR. |

## Context

### What already exists (verified against the tree at merge `06d11c9`)

| Fact | Evidence |
|---|---|
| No persistence code of any kind. No SQLAlchemy, Alembic, or driver dependency. | `pyproject.toml` `[project].dependencies` — `PyYAML`, `pydantic`, `anthropic`, `langgraph`, `uuid-utils`, `fastapi`; no `sqlalchemy`/`alembic`/`psycopg`. `src/ai_daily_digest/ingestion/` contains only `__init__.py` and `README.md`. |
| The shared contract is Pydantic-only today. | `src/ai_daily_digest/shared/schemas.py` — `SourceItem`, `DocumentSnapshot`, `Change`, `Digest`, etc. `SourceItem` has 14 fields including `first_fetched_at: OrderingTimestamp = Field(frozen=True)` (`schemas.py:116`), `authors`/`tags: list[str]` (`schemas.py:125-126`), `latest_snapshot_id: Uuid7Id \| None` (`schemas.py:117`). |
| IDs are already application-generated UUID v7 via one factory. | `src/ai_daily_digest/shared/ids.py::new_id()` (returns stdlib `uuid.UUID` from `uuid_utils.compat.uuid7()`); `type Uuid7Id = UUID7`. [ADR 0007](0007-uuid-v7-identifier-strategy.md) Decision: "PostgreSQL native `uuid` column storage for every UUID-typed field, once tables exist … PostgreSQL ≤17 has no native `uuidv7()`; … generation is application-side." |
| Model-level immutability of the six protected ordering columns already ships. | `schemas.py:100,116` (`SourceItem.id`, `first_fetched_at`), `:439,447` (`Change.id`, `detected_at`), `:520,521` (`Digest.id`, `digest_date`) all `Field(frozen=True)`. `tests/unit/test_protected_ordering_fields.py` (merged in PR #45) statically forbids `model_copy(update=...)` on any of them. |
| ADR 0008 requires three immutability layers and explicitly defers the database one. | [ADR 0008](0008-cursor-pagination-contract.md) §5.D: model freezing (done), repository restriction (no update path), and **storage-level enforcement** — "A normal PostgreSQL `CHECK` constraint is **not sufficient**: it … cannot compare `OLD` with `NEW`." "a `BEFORE UPDATE` trigger … or appropriately restricted column-level `UPDATE` privileges … The exact mechanism is chosen in the future database schema / migration PR." |
| `/v1/updates` sorts by `(first_fetched_at DESC, id DESC)`, filters on `publisher` and `source_id` only, and reads `limit + 1` rows. | ADR 0008 §4, §8, §10. `tags` and any `published_*` range filter for `/v1/updates` are deferred (§8). |
| The pagination codec is pure and framework-free; the repository takes canonical typed filters, never raw request strings. | `src/ai_daily_digest/delivery/api/pagination.py` — `CanonicalFilters`, `canonicalize_filters`, `CursorCodec`, `Page[T]`. ADR 0008 §7. |
| The FastAPI foundation exists with a typed, injected readiness boundary. | `src/ai_daily_digest/delivery/api/dependencies.py` — `ReadinessProbe` Protocol (`async def is_ready() -> bool`), `ReadinessRegistry.evaluate()` catches every probe exception and logs only `exception_type` + `request_id` (`dependencies.py:42-73`), `build_readiness_registry()` raises when a required dependency has no probe (`dependencies.py:90-93`). `routes/health.py` exposes only `{name, status}` publicly. |
| ADR 0010 governs how infrastructure enters routes and what the first persistence PR may contain. | [ADR 0010](0010-fastapi-openapi-contract-authority.md): "Infrastructure enters route handlers through typed FastAPI dependencies and narrow protocols." "Do not add empty repositories, services, adapters, or other speculative abstractions." "Naming a dependency as required without providing its probe is a configuration error and must prevent normal startup." "First domain vertical slice … no Postgres adapter exists merely to raise `NotImplementedError`; no public route merges without a functional configured data source." |
| A synchronous cross-module boundary precedent already lives in `shared/`. | `src/ai_daily_digest/shared/snapshot_resolver.py` — a `Protocol` in `shared/` "a protocol ingestion is eventually expected to provide a real implementation of cannot live inside an intelligence-private module." Its docstring: "a future database-backed implementation may need to be asynchronous — that is a separate design decision this module does not make or preclude." |
| `DATABASE_URL` is already an empty-placeholder environment variable. | `.env.example:2` — `DATABASE_URL=`. `ANTHROPIC_API_KEY` is the only env var any code reads today (`intelligence/llm.py:59`). |
| CI runs unit + contract + integration tests in one job; there is no database service. | `.github/workflows/ci.yml` — `tests` job runs `uv run pytest -m "not live"` (which **includes** `integration` and `e2e`). `Makefile:22` inner loop excludes `integration and e2e and live`. `tests/integration/` contains only `__init__.py`. `pytest` markers are declared in `pyproject.toml:114-119`. |
| ADR 0002's own original text already flags the operational follow-ups. | This file's prior "Deferred operational decisions" — backups, retention, ordinary indexes, pgvector metric/index type, vector rebuild procedure. Preserved and extended below. |

### The decision this amendment must make

`GET /v1/updates` (ADR 0008 PR 4) cannot ship without "a functional configured repository
adapter" (ADR 0008 §13; ADR 0010 first-vertical-slice rule). That adapter needs: a database,
an ORM, a migration tool, a driver, a repository protocol, a configuration boundary, a
readiness probe, and a real integration test suite. This ADR fixes each of those before the
implementation PR opens.

## Decision

### 1. PostgreSQL remains the durable system of record

Unchanged from the original decision. Normalized records, constraints, relationships,
provenance, and (later) subscription data live in PostgreSQL. The vector store is a derived
index and is never the source of truth (`AGENTS.md`; `docs/ARCHITECTURE.md` Storage rules).

### 2. pgvector stays the future derived vector index, deferred from the first persistence PR

pgvector remains the intended embedding index, in the same PostgreSQL instance, per the
original decision. It is **out of scope for the first persistence PR**:

- the first Alembic migration must **not** run `CREATE EXTENSION vector` and must **not**
  create any embedding table;
- keeping it out means the migration applies cleanly on a stock `postgres:17` image, with no
  `pgvector/pgvector` image required in CI or local development yet;
- when embeddings land, that PR either switches the test image to `pgvector/pgvector:pg17`
  or adds `CREATE EXTENSION IF NOT EXISTS vector` as its own reviewed migration, and chooses
  the distance metric and index type from measured corpus behaviour (already a deferred
  operational decision below).

### 3. Use SQLAlchemy 2 and Alembic

Confirmed from `docs/ARCHITECTURE.md`'s technology baseline. SQLAlchemy 2.0 typed ORM
(`DeclarativeBase`, `Mapped[...]`, `mapped_column(...)`) for table definitions and queries;
Alembic for every schema change. No raw-SQL schema management; no ORM-less query builder.
`AGENTS.md`: "Database changes use Alembic migrations; never edit production tables
manually."

### 4. Asynchronous SQLAlchemy engine — selected

**Decision: use SQLAlchemy's async engine (`create_async_engine`,
`async_sessionmaker`, `AsyncSession`) for the application runtime path** — the Delivery API
and the async collectors — with synchronous Alembic driven through the standard
`connection.run_sync(...)` bridge in `alembic/env.py`.

Why, against the standard "prefer async unless repository evidence shows a simpler reliable
alternative":

- **FastAPI route handlers here are already `async def`** (`routes/health.py`). A synchronous
  database call inside an async handler blocks the event loop; ADR 0010 and this task both
  forbid that.
- **Collectors are async by design.** `docs/ARCHITECTURE.md` selects `httpx` for "async
  support, timeouts, connection pooling"; the collection flow is concurrent with bounded
  parallelism. A synchronous session in that path would need a threadpool hop on every
  write.
- **The "simpler alternative" is not actually simpler here.** Synchronous SQLAlchemy behind
  `fastapi.concurrency.run_in_threadpool` still splits the mental model ("the ORM is sync but
  every call is awaited through a thread") and still needs a driver that also does async for
  the collectors, or a second driver. Psycopg 3 (section 5) gives one driver for both sync
  and async, so choosing async is **not** more dependencies than choosing sync.
- **The one genuinely synchronous consumer is Alembic**, and that is a solved problem: an
  async `env.py` calls `asyncio.run(...)` and `connection.run_sync(context.run_migrations)`.
  Autogenerate and `upgrade`/`downgrade` run exactly as they do in a sync project.
- The existing `shared/snapshot_resolver.py` precedent — deliberately synchronous "for this
  phase" — **explicitly reserves** the async decision for "a future database-backed
  implementation." This is that decision. `SnapshotResolver` itself stays synchronous until a
  database-backed resolver is actually built (not in this PR); it is not retrofitted here.

**Rejected — synchronous SQLAlchemy + threadpool offload:** viable and lower learning curve,
but pushes an executor hop into every request and every collector write, and still needs
psycopg 3 for the collectors, so it trades a small conceptual simplification for a worse
runtime shape.

**Rejected — sync in ingestion, async in delivery, over one URL:** two session factories and
two mental models over one engine; the collector path does not benefit from staying sync once
`httpx` is already async.

The worker/collector uses an `async def main()` entry point invoked with `asyncio.run()`. It
uses the same async session factory as the API. The existing synchronous intelligence pipeline
may run as an in-process step in that dedicated worker after database inputs have been loaded;
it must not perform synchronous database I/O from an async FastAPI request handler.

### 5. Psycopg 3 as the PostgreSQL driver — selected

**Decision: `psycopg` version 3** (`postgresql+psycopg://` DSN), installed as
`psycopg[binary]` for CI and local development.

- Psycopg 3 has **native sync and async** support in one package — the async runtime and the
  synchronous Alembic step use the same driver.
- It is the driver SQLAlchemy 2.0's own documentation now leads with for PostgreSQL.
- Actively maintained; `psycopg2` is in maintenance mode and is sync-only; `asyncpg` is
  async-only (forcing a second sync driver for Alembic) and uses a different parameter style.
- `psycopg[binary]` ships self-contained wheels — no system `libpq` needed in CI or on a
  developer laptop.

**Rejected — `asyncpg`:** fastest raw async driver, but async-only, so Alembic would need
`psycopg2`/`psycopg` anyway; two drivers for one database.

**Rejected — `psycopg2`:** sync-only, maintenance mode.

**Deployment follow-up (not this PR):** a production container may prefer `psycopg[c]` or a
system-`libpq` build over `psycopg[binary]`. That belongs to the deployment ADR, alongside
the `uuid-utils` deployment-platform validation ADR 0007 already defers.

### 6. Application generates UUID v7; PostgreSQL stores, never generates

- Every `id` and every UUID foreign key is generated by `shared.ids.new_id()` in application
  code **before** the row is written (ADR 0007).
- The database columns are **native `uuid`** (SQLAlchemy `Uuid` / `sqlalchemy.dialects.
  postgresql.UUID(as_uuid=True)`; Python attribute is a `uuid.UUID`).
- Columns have **no `DEFAULT`**, **no `server_default`**, **no `gen_random_uuid()`**, and
  **no `uuidv7()`** (PostgreSQL 18 only, and irrelevant — generation is application-side).
- A row inserted without an `id` is a bug, not a database-filled blank.
- Integration tests assert a `new_id()` value round-trips through the `uuid` column unchanged
  and reads back as canonical lowercase-hyphenated form (ADR 0007 validation expectations).

### 7. Phase-1 database scope

The first persistence PR creates **only**:

- the `source_items` table;
- the `document_snapshots` table;
- their integrity constraints, foreign keys, indexes required by their documented sort key,
  and the Phase-1 immutability triggers (section 11);
- Alembic configuration and the single initial migration, plus its `alembic_version` table.

It creates **no** `facts`, `changes`, `change_sets`, `digests`, `digest_claims`,
`subscriptions`, `email_deliveries`, `collection_runs`, `events`, `chat_sessions`,
`embeddings`, or any vector table. Each of those arrives with the feature that needs it, in
its own migration, owned by the module that owns it.

### 8. `source_items` schema

Covers the current `SourceItem` Pydantic contract (`schemas.py:86-127`) field-for-field.

| Column | PostgreSQL type | Constraints / notes |
|---|---|---|
| `id` | `uuid` | `PRIMARY KEY`. No default. **Immutable** (section 11). |
| `dedupe_key` | `text` | `NOT NULL`, `UNIQUE`. sha256 of the normalized canonical URL. Immutable after insertion in Phase 1. A future canonicalization-policy change requires an explicit migration/backfill plan rather than an ordinary upsert rewriting identity. |
| `source_id` | `text` | `NOT NULL`. `sources.yaml` slug (e.g. `openai_news`) — a config key, never a UUID (ADR 0007). |
| `publisher` | `text` | `NOT NULL`. |
| `title` | `text` | `NOT NULL`. |
| `canonical_url` | `text` | `NOT NULL`. Validated as a URL by the Pydantic boundary (`HttpUrl`); stored as `text`. Immutable after insertion in Phase 1 because it is the input to `dedupe_key`. |
| `published_at` | `timestamptz` | `NULL`. Publisher-controlled; may be corrected by the publisher. |
| `updated_at` | `timestamptz` | `NULL`. Mutable. |
| `first_fetched_at` | `timestamptz` | `NOT NULL`. UTC, microsecond precision. **Immutable** (section 11). The `/v1/updates` business sort value. |
| `latest_snapshot_id` | `uuid` | `NULL`. Part of the composite ownership FK described in section 9. **Mutable** — advances only when a newer snapshot for this same source item arrives. |
| `event_id` | `text` | `NULL`. Human-readable grouping key; not a generated resource id (ADR 0007). |
| `authors` | `text[]` | `NOT NULL DEFAULT '{}'`. See section 10. |
| `tags` | `text[]` | `NOT NULL DEFAULT '{}'`. See section 10. |
| `language` | `text` | `NOT NULL DEFAULT 'en'`. |

Indexes:

- `PRIMARY KEY (id)`;
- `UNIQUE (dedupe_key)`;
- **`(first_fetched_at DESC, id DESC)` btree** — the exact keyset order `/v1/updates` scans
  (ADR 0008 §4). Included in the foundation migration because PR 4 cannot serve pages without
  it and this ADR keeps persistence to one foundation PR.
- Filter-supporting indexes on `publisher` / `source_id` (or a composite
  `(publisher, first_fetched_at DESC, id DESC)`) are **deferred** to a measured follow-up —
  consistent with this ADR's existing "ordinary PostgreSQL indexes … chosen from measured
  … query behavior" deferral.

### 9. `document_snapshots` schema and foreign-key behaviour

Covers the current `DocumentSnapshot` contract (`schemas.py:130-143`).

| Column | PostgreSQL type | Constraints / notes |
|---|---|---|
| `id` | `uuid` | `PRIMARY KEY`. No default. **Immutable** (section 11). |
| `source_item_id` | `uuid` | `NOT NULL`. `FK → source_items(id) ON DELETE RESTRICT ON UPDATE RESTRICT`. |
| `fetched_at` | `timestamptz` | `NOT NULL`. UTC, microsecond precision. |
| `content_hash` | `text` | `NOT NULL`. sha256 of the cleaned content — a hash, never a UUID. |
| `content_text` | `text` | `NOT NULL`. Delivery projections may omit it from public list responses; omission from an API response is not a nullable storage value. Intelligence grounding requires stored content. |
| `raw_location` | `text` | `NULL`. Internal storage reference — never exposed in a list response. |
| `etag` | `text` | `NULL`. |
| `last_modified` | `text` | `NULL`. |
| `collector_version` | `text` | `NULL`. |

Constraints and indexes:

- `PRIMARY KEY (id)`;
- **`UNIQUE (source_item_id, content_hash)`** — the snapshot idempotency key; a re-fetch that
  produces identical content must not create a second row;
- the foreign key above;
- `UNIQUE (id, source_item_id)`, which is the target of the composite latest-snapshot ownership
  foreign key below (the primary key already makes `id` unique, but PostgreSQL requires a matching
  unique key for this two-column reference);
- **whole-row immutability** via a `BEFORE UPDATE` trigger, plus a `BEFORE DELETE` trigger
  that always raises (section 11);
- a `(source_item_id, fetched_at DESC)` history index is **deferred** — no Phase-1 endpoint
  reads snapshot history.

**Foreign-key behaviour — historical snapshots are never silently deleted:**

- `document_snapshots.source_item_id → source_items.id` is `ON DELETE RESTRICT`
  (**not** `CASCADE`). A `source_item` that still has snapshots cannot be deleted; deleting a
  source item is not a Phase-1 operation at all (provenance is immutable — `AGENTS.md`), and
  if one is ever attempted it must fail loudly rather than take the snapshots with it.
- `(source_items.latest_snapshot_id, source_items.id) →
  (document_snapshots.id, document_snapshots.source_item_id)` is a composite `ON DELETE
  RESTRICT ON UPDATE RESTRICT` foreign key. It proves at the storage layer that an item's
  `latest_snapshot_id` belongs to that same item; a single-column reference to
  `document_snapshots.id` would only prove that some snapshot exists and could silently attach
  another item's snapshot.
- The two tables reference each other. The migration resolves this without a `DEFERRABLE`
  constraint by ordering DDL: create `source_items` **without** the `latest_snapshot_id` FK →
  create `document_snapshots` with its FK to `source_items` → `ALTER TABLE source_items ADD
  CONSTRAINT … FOREIGN KEY (latest_snapshot_id, id) REFERENCES
  document_snapshots(id, source_item_id)`. At
  runtime the write order (section 13) inserts the item first with `latest_snapshot_id NULL`,
  so no deferral is needed.

`DocumentSnapshot` is tightened in the implementation PR: `fetched_at` rejects naive
datetimes and normalizes aware values to UTC; `content_text` becomes a required `str`; and
the model is frozen as a whole. This aligns the shared boundary with the existing contract
statement that every stored snapshot has content and with the database's immutable, non-null
row. Delivery list schemas remain separate projections and therefore do not expose snapshot
bodies.

### 10. `authors` and `tags` — PostgreSQL `text[]` arrays

**Decision: store `authors` and `tags` as `text[]` array columns**, `NOT NULL DEFAULT
'{}'`, element order preserved to match `list[str]` and the JSON contract.

Trade-off, stated rather than assumed:

| Option | For | Against | Verdict |
|---|---|---|---|
| **`text[]` array** | One row, no join; order preserved; native array operators (`@>`, `&&`) and a future GIN index if a `tags` filter is added; element typing kept. | No referential integrity; no cross-item tag dimension (rename/merge/counts) without a later migration. | **Chosen for Phase 1.** |
| `jsonb` | Flexible; GIN-indexable. | Untyped elements; loose duplicate/order semantics; overkill for a homogeneous string list. | Rejected — these are string lists, not documents. |
| `authors` / `tags` child tables (or a `tags` dimension + `source_item_tags` join) | Proper normalization; FK integrity; easy dedup and faceting. | Extra tables, joins, and repository code for a capability **no Phase-1 endpoint needs** — ADR 0008 §8 defers the `tags` filter and never filters `authors`. | Rejected for Phase 1; revisit if tags become a managed, filterable dimension. |

Element normalization stays the ingestion normalizer's responsibility, not a database constraint,
in Phase 1. Authors are Unicode-NFC-normalized, trimmed, empty values are removed, and exact
duplicates are removed while preserving first-seen order and case. Tags receive the same treatment
and are additionally case-folded before exact deduplication. Migrating `tags` to a dimension + join
table later is a
localized, additive migration (new tables, backfill, swap the repository read path) — it does
not disturb `source_items.id` or `first_fetched_at`.

### 11. Ordering-column and snapshot immutability — Phase-1 storage mechanism

ADR 0008 §5.D requires three independent layers. Layers 1 and 2 are settled; this section
fixes layer 3.

1. **Model-level freezing — already shipped.** `Field(frozen=True)` on `SourceItem.id`,
   `SourceItem.first_fetched_at`, `Change.id`, `Change.detected_at`, `Digest.id`,
   `Digest.digest_date` (`schemas.py`); `tests/unit/test_protected_ordering_fields.py`
   statically forbids `model_copy(update=...)` on any of them.
2. **Repository restriction — this PR.** The ingestion write protocol (section 12) exposes
   **no method that updates a protected column.** `upsert_source_item` writes the
   allowed-mutable set only; there is no `set_first_fetched_at`, no generic `update(**fields)`
   passthrough, no `id` reassignment. This is application-level defence in depth — a direct
   SQL statement bypasses it, which is why layer 3 exists.
3. **Storage-level enforcement — `BEFORE UPDATE` / `BEFORE DELETE` row-level triggers.**

**Decision: use PL/pgSQL `BEFORE UPDATE` triggers**, created in the initial Alembic
migration with a matching `downgrade()` that drops them.

- On `source_items`: `BEFORE UPDATE FOR EACH ROW`, raising
  `RAISE EXCEPTION 'source_items.% is immutable', 'first_fetched_at'` (and likewise `id`)
  when `OLD.id IS DISTINCT FROM NEW.id` **or**
  `OLD.first_fetched_at IS DISTINCT FROM NEW.first_fetched_at`. An `UPDATE` that leaves both
  columns byte-identical is permitted (an idempotent rewrite), which column privileges cannot
  express.
- On `document_snapshots`: `BEFORE UPDATE FOR EACH ROW` that raises on **any** column change
  (whole-row immutability — `AGENTS.md`: "Raw source snapshots are immutable"), plus a
  `BEFORE DELETE FOR EACH ROW` that always raises ("Corrections create a new version and
  retain provenance").
- One trigger function per table; the trigger definition names the protected columns, so
  adding a protected column later is a one-line change in a new migration.

**Why triggers over column-level `UPDATE` privileges:**

| | `BEFORE UPDATE` trigger | Restricted column `UPDATE` privilege |
|---|---|---|
| Fires for the CI database user | **Yes** — triggers are not bypassed by the table owner or a superuser, so the integration suite proves the guarantee even connecting as `postgres`. | **No** — `GRANT`/`REVOKE` do not restrict the table owner or a superuser, so a single-role CI database cannot exercise it without bespoke limited-role provisioning. |
| Distinguishes "unchanged" from "changed" | **Yes** — compares `OLD`/`NEW`; permits a no-op rewrite. | No — forbids naming the column in `UPDATE` at all. |
| Ships and rolls back in a migration | **Yes** — `op.execute(CREATE FUNCTION … CREATE TRIGGER …)` / `downgrade` drops them. | Partly — needs a dedicated runtime role and `GRANT` management in migrations. |
| Reviewer cost | A small PL/pgSQL function + trigger per table. | Role and grant plumbing; easy to get subtly wrong. |

**Rejected — normal `CHECK` constraint:** cannot compare `OLD` and `NEW`; ADR 0008 §5.D
already rules it out.

**Rejected as the Phase-1 test-enforced mechanism — column-level privileges:** kept as a
documented **production hardening** option — the deployment can additionally
`REVOKE UPDATE (id, first_fetched_at) ON source_items` and `REVOKE UPDATE, DELETE ON
document_snapshots` from the runtime application role as defence in depth — but it is not
what the Phase-1 integration suite proves, because a one-role CI Postgres cannot.

**How a rejected update is rolled back, and how tests confirm the value survives:**

1. The trigger's `RAISE EXCEPTION` aborts the statement; PostgreSQL marks the surrounding
   transaction aborted; `psycopg` raises `psycopg.errors.RaiseException`, which SQLAlchemy
   surfaces as `DBAPIError`.
2. The application (or the test) calls `session.rollback()` (or exits the
   `async with session.begin():` block, which rolls back on exception). Nothing was
   committed, so the on-disk row is untouched.
3. Integration test, one per Phase-1 protected column (`SourceItem.id`,
   `SourceItem.first_fetched_at`) and one for whole-snapshot immutability and one for
   snapshot deletion:
   - read the row, keep the original value;
   - issue a raw `text("UPDATE source_items SET first_fetched_at = :t WHERE id = :id")`
     (bypassing the repository on purpose — this tests layer 3, not layer 2);
   - assert it raises `DBAPIError`;
   - `await session.rollback()`; assert the session is usable again;
   - re-`SELECT` the row and assert `first_fetched_at` equals the original **to the
     microsecond**, and the full row is unchanged;
   - assert the repository exposes no ordinary method that could have issued that `UPDATE`
     (behavioural — the protocol has no such member).

The four `Change` / `Digest` protected-column storage-level tests from ADR 0008 §14 ("Later —
persistence-adapter integration PR") land with the migration that creates the `changes` and
`digests` tables (intelligence persistence — out of scope here). This ADR records that
boundary so the gap is understood as deferred, not skipped.

### 12. Repository protocols and module boundaries

Two narrow, typed protocols. No speculative abstraction — each has exactly one production
implementation and one caller (ADR 0010).

**12.1 Ingestion write protocol — `ingestion/`-private.**

Lives next to its only implementer and only caller, in `src/ai_daily_digest/ingestion/`
(e.g. `ingestion/persistence.py`). Not in `shared/` — no other module calls it.

Responsibilities:

- `upsert_source_item(...) -> SourceItem` — create-or-find by canonical `dedupe_key`. On
  find, **preserve the existing `id` and `first_fetched_at`** and update only an explicit
  allowed-mutable set (`publisher`, `title`, `published_at`, `updated_at`, `authors`, `tags`,
  `language`, `event_id`). `id`, `first_fetched_at`, `source_id`, `canonical_url`, and
  `dedupe_key` are never written on the update path.
- `add_snapshot_if_new(source_item_id, content_hash, ...) -> DocumentSnapshot` — insert only
  when `(source_item_id, content_hash)` is new; return the existing row otherwise. Never a
  second row for identical content.
- `advance_latest_snapshot(source_item_id, snapshot_id) -> bool` — after the snapshot row
  exists, conditionally advance `latest_snapshot_id` only when the candidate belongs to the
  item and its `(fetched_at, id)` tuple is newer than the current tuple (same transaction,
  section 13). Return whether the pointer advanced.
- All three are **idempotent under retries and concurrent duplicate ingestion**
  (`INSERT … ON CONFLICT DO NOTHING` + re-`SELECT`; unique-violation-safe).

**12.2 Delivery read protocol — cross-module, in `shared/`.**

`/v1/updates` needs a query capability that ingestion's tables provide. That is a genuine
cross-module seam — delivery depends on a capability ingestion supplies — so the **Protocol**
belongs in `shared/` (e.g. `src/ai_daily_digest/shared/repositories.py`), mirroring the
`shared/snapshot_resolver.py` precedent exactly. The concrete PostgreSQL adapter stays
**ingestion-owned**.

Responsibilities (`SourceItemFeedRepository` or similar):

- one async method, e.g.
  `async def list_source_items(*, publisher: str | None, source_id: str | None,
  after: tuple[datetime, uuid.UUID] | None, limit: int) -> Sequence[SourceItem]`;
- `publisher` / `source_id` are **already-canonical** values (trimmed, NFC-normalized,
  case-sensitive) exactly as `canonicalize_filters` produced them — the protocol takes
  canonical **values**, never raw request strings or the delivery-owned `CanonicalFilters`
  object (which `shared/` must not import);
- `after` is the keyset position `(first_fetched_at, id)`; the query applies the row-value
  predicate `(first_fetched_at, id) < (after_ts, after_id)` (ADR 0008 §4);
- ordering is fixed `first_fetched_at DESC, id DESC`;
- the method fetches **`limit + 1`** rows so the route can detect a further page (ADR 0008
  §10) — the route trims to `limit` and builds `next_cursor` from the last **returned** row;
- it returns the shared `SourceItem` domain model. The route projects that to the
  ADR 0008 §12 `UpdateSummary`, which **omits `dedupe_key`** and other internal fields. The
  repository returns domain records; the public omission happens at the projection boundary,
  under `delivery/api/`.

**12.3 Composition / wiring boundary.**

- SQLAlchemy models, `MetaData`, the engine/session factory, both concrete adapters, and the
  Alembic migrations are **ingestion-owned**, under `src/ai_daily_digest/ingestion/db/`
  (models, metadata, engine) and `alembic/` (top-level, `script_location` configured).
- `delivery/api/` imports only `shared` — the read Protocol and `SourceItem`. It **never**
  imports `ingestion.db`.
- `delivery/api/app.py::create_app()` gains an **optional injected parameter** for the read
  repository, typed as the `shared` Protocol — the same DI pattern as the existing
  `readiness_probes` parameter. A small **composition root** (the process entrypoint that
  runs the ASGI server, or a `delivery/` wiring helper) constructs the ingestion PostgreSQL
  adapter from configuration and passes it in.
- Unit tests pass an in-memory fake implementing the same Protocol (ADR 0010: "tests use an
  in-memory fake"; "no Postgres adapter exists merely to raise `NotImplementedError`").

This touches `delivery/api/app.py` and adds to `shared/` — both are coordination-controlled
files (ADR 0010 §"Parallel-work boundaries"). The implementation PR must rebase on current
`main` and rerun `make ci`, and the `shared/` addition needs peer review (`AGENTS.md`).

**Rejected — read Protocol under `delivery/api/`, adapter structurally typed in ingestion:**
technically works with `typing.Protocol` structural subtyping and mypy conformance-checked at
the `create_app` call site, and avoids a `shared/` change. Rejected as the recommendation
because it contradicts the `shared/snapshot_resolver.py` precedent (a real cross-module
contract is stated explicitly in `shared/`, not left implicit). The shared placement is the
Phase-1 decision, not an implementation-time choice.

### 13. Transactions and concurrency

**Transaction boundary — one transaction per ingested item** (inside a collection run):

1. Upsert the source item: `INSERT INTO source_items (…) ON CONFLICT (dedupe_key) DO UPDATE
   SET <allowed-mutable columns only> RETURNING *`. The `DO UPDATE` set-list never names
   `id` or `first_fetched_at` (and the trigger backstops that).
2. If the content hash is new: `INSERT INTO document_snapshots (…) ON CONFLICT
   (source_item_id, content_hash) DO NOTHING RETURNING id`; if no row is returned, `SELECT
   id` for the existing `(source_item_id, content_hash)`.
3. If a new snapshot was inserted, update `latest_snapshot_id` in the **same** transaction only
   when the new snapshot's `(fetched_at, id)` tuple is newer than the currently referenced
   snapshot's tuple. A conditional update/subquery enforces this comparison in PostgreSQL. This
   prevents two concurrent, different-content fetches from committing out of order and leaving an
   older snapshot marked as latest. The composite FK from section 9 proves the selected snapshot
   belongs to this item. `updated_at` records the accepted metadata refresh independently.
4. Commit. Snapshot row and `latest_snapshot_id` become visible **atomically**.

**Uniqueness-conflict handling:** the two `UNIQUE` constraints (`source_items.dedupe_key`,
`document_snapshots (source_item_id, content_hash)`) are the single source of truth.
Concurrent runs race on the `INSERT`; `ON CONFLICT DO NOTHING`/`DO UPDATE` makes the loser
converge on the winner's row via the `RETURNING`/re-`SELECT`. No duplicate row; no error
surfaced to the run.

**Retry / idempotency:** re-running the same item recomputes the same `dedupe_key` and
`content_hash`, hits the same conflict paths, and converges to the same rows.
`advance_latest_snapshot` is conditional and idempotent: the same snapshot is a no-op, an
older snapshot cannot move the pointer backwards, and only a newer same-item snapshot can
advance it.

**No partial `snapshot exists but latest_snapshot_id inconsistent` state:** steps 2 and 3
commit together. A crash between them rolls both back. At every commit boundary,
`latest_snapshot_id` is either `NULL` or points at a real, existing snapshot of that item.

**Rollback behaviour:** any exception in the per-item transaction (unexpected unique
violation, a trigger `RAISE`, a lost connection) → `rollback()` → no rows for that item; the
collection run records the item as failed and continues (`docs/ARCHITECTURE.md`: "One source
failure does not abort others").

**Isolation level:** default `READ COMMITTED` is sufficient for Phase 1 — `ON CONFLICT`
idempotency and keyset pagination do not need `REPEATABLE READ` or `SERIALIZABLE`. Stated
explicitly so a later change is a reviewed decision.

**No database transaction is held across HTTP pagination requests.** Each `GET /v1/updates`
request opens a short-lived session (a FastAPI dependency that yields one session per
request), runs one `SELECT … ORDER BY first_fetched_at DESC, id DESC LIMIT :limit + 1`, and
closes it. Continuity between pages is the signed keyset cursor (ADR 0008), never a
server-side transaction, cursor, or scroll. There is no `as_of` snapshot in Phase 1
(ADR 0008 §11).

### 14. Configuration and readiness

- **`DATABASE_URL` stays an environment setting with no committed secret.** Already present
  as an empty placeholder in `.env.example`. Format:
  `postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME`. Alembic reads the same variable.
  The configuration layer parses it, confirms it is set when a database-backed feature is
  configured, and never logs it or any component of it.
- **A configured database-backed API must install a real readiness probe.** A
  `DatabaseReadinessProbe` implementing the existing `ReadinessProbe` Protocol
  (`dependencies.py:20-24`) whose `is_ready()` runs a **bounded `SELECT 1`** on a pooled
  connection with a short per-statement timeout, returns `True`/`False`, and catches its own
  driver errors specifically (`SQLAlchemyError` / `psycopg.Error`) → `False` on failure.
- When `DATABASE_URL` is configured and any route needs the database, `"database"` goes into
  `required_dependencies`, and `build_readiness_registry()` **already raises** if that name
  has no probe (`dependencies.py:90-93`) — exactly ADR 0010's "Naming a dependency as
  required without providing its probe … must prevent normal startup." A foundation-only app
  with no database keeps `required_dependencies` empty and `/v1/health/ready` returns `200`.
- **Probe errors are sanitized through the merged PR #44 readiness boundary.**
  `ReadinessRegistry.evaluate()` catches every probe exception, logs only `exception_type`
  and `request_id`, and yields `ready=False` (`dependencies.py:42-73`); the public response
  carries only `{name, status}` (`routes/health.py`). The probe must never let a DSN, host,
  driver message, or connection string escape — it returns a bool; the registry boundary is
  the backstop. `GET /v1/health/ready` returns `503` + `service_unavailable` when the
  database is not ready.
- **Pool and timeout settings are bounded Phase-1 defaults:** `pool_size=5`,
  `max_overflow=5`, `pool_pre_ping=True`, `pool_recycle=1800` seconds, driver
  `connect_timeout=5` seconds, server-side `statement_timeout=10_000` milliseconds, and a
  2-second readiness-probe timeout. `DatabaseConfig` exposes typed overrides so deployment
  can tune them without code edits; values and connection URLs are never logged.

### 15. Testing and CI

**PostgreSQL integration tests run against a real PostgreSQL — never a SQLite stand-in.**
Arrays (`text[]`), the native `uuid` type, `timestamptz` microsecond precision, `ON
CONFLICT`, row-level triggers, and FK `RESTRICT` are not faithfully reproduced by SQLite.

- **CI:** the `tests` job in `.github/workflows/ci.yml` gains a `postgres:17` **service
  container** (with a health check), sets `DATABASE_URL` to it, runs `alembic upgrade head`
  before `pytest`, and runs the integration suite (`-m integration`, currently already
  inside `-m "not live"`) as part of that job. **CI always runs the real integration suite;
  it is never skipped there.** The `quality` and `security` jobs are unchanged.
- **Postgres version:** pin the image tag (`postgres:17`) as the tested version; 18 is not
  required and its `uuidv7()` is irrelevant (generation is application-side).
- **Local:** the implementation PR adds a `compose.yaml` with one `postgres` service and
  `Makefile` targets (`db-up`, `db-migrate`, `db-revision`, `test-integration`), and a short
  "running migrations and integration tests locally" doc. A developer may also point
  `DATABASE_URL` at any local PostgreSQL.
- **When local PostgreSQL is absent, behaviour is explicit, never silent:** integration tests
  carry `@pytest.mark.integration`; they **skip with a clear reason**
  (`pytest.skip("set DATABASE_URL to run PostgreSQL integration tests")`) when `DATABASE_URL`
  is unset, and a collection-time guard **fails** (not skips) if `-m integration` was
  explicitly selected with no `DATABASE_URL`. `make check` (inner loop) already excludes
  `integration` (`Makefile:22`). No test ever silently passes without touching the database,
  and there is no SQLite fallback path.
- **Unit tests** for delivery and ingestion logic use the in-memory fake repositories — no
  database at all.

**Integration tests the implementation PR must include:**

- **unique constraints** — `dedupe_key`; `(source_item_id, content_hash)`;
- **idempotency** — re-running an item insert produces the same rows and raises nothing;
  concurrent duplicate insert converges;
- **transaction rollback** — a failed snapshot write leaves no partial state; a trigger
  `RAISE` aborts and rolls back the whole transaction;
- **foreign keys** — a snapshot for a missing `source_item_id` is rejected; a `source_item`
  with snapshots cannot be deleted (`RESTRICT`); a source item cannot point at another
  item's snapshot;
- **timestamp precision** — `first_fetched_at` and `fetched_at` round-trip through
  `timestamptz` to the microsecond;
- **UUID v7 storage** — a `new_id()` value stored in a native `uuid` column reads back equal
  and canonical;
- **snapshot contract and immutability** — naive `fetched_at` and null `content_text` are
  rejected at the model/storage boundaries; `UPDATE` and `DELETE` on
  `document_snapshots` are both rejected;
- **latest-pointer concurrency** — two different snapshots committed out of arrival order
  still leave the greatest `(fetched_at, id)` selected, and an older retry cannot regress
  the pointer;
- **list normalization** — author/tag trimming, Unicode NFC normalization, empty removal,
  stable deduplication, and tag case-folding are deterministic;
- **protected ordering-column immutability** — one test each for `SourceItem.id` and
  `SourceItem.first_fetched_at` per section 11 (`Change`/`Digest` protected-column tests land
  with those tables).

### 16. The persistence-foundation implementation PR

One focused PR, opened only **after this ADR is Accepted by Persons A, B, and C.** It
contains exactly:

- **`pyproject.toml`** — add production dependencies **with written per-dependency
  justification** (`AGENTS.md` rule): `sqlalchemy>=2.0`, `alembic>=1.13`,
  `psycopg[binary]>=3.2`. Justification: no standard-library or existing dependency provides
  a typed ORM, reviewable migrations, or a PostgreSQL wire driver.
- **`uv.lock`** — regenerated; prove `uv lock --check` and a locked non-editable install
  (`uv sync --locked --no-editable`), and run `pip-audit` after resolution (same bar
  ADR 0007 / ADR 0010 set).
- **`alembic.ini` + `alembic/`** — `env.py` (async, via `connection.run_sync`), one initial
  migration `alembic/versions/0001_source_items_and_document_snapshots.py`. Hand-written, not
  autogenerated — autogenerate does not reliably emit `text[]` columns or trigger DDL.
- **`src/ai_daily_digest/ingestion/db/`** — `metadata.py` (a `MetaData` with a naming
  convention so constraint names are deterministic), `models.py` (`SourceItemRow`,
  `DocumentSnapshotRow` as SQLAlchemy 2.0 mapped classes mirroring the Pydantic contract),
  `engine.py` (`create_async_engine` + `async_sessionmaker` from a `DatabaseConfig`),
  `repository.py` (`PostgresSourceItemRepository` implementing the write protocol and the
  shared read protocol).
- **`src/ai_daily_digest/ingestion/persistence.py`** — the ingestion write `Protocol`.
- **`src/ai_daily_digest/shared/repositories.py`** — the cross-module read `Protocol`
  returning `SourceItem` (peer-reviewed `shared/` change).
- **`src/ai_daily_digest/shared/config.py`** — `DatabaseConfig` (`from_env()` reads and
  validates `DATABASE_URL` plus the bounded pool/timeout overrides in section 14; no secret
  or URL component is logged).
- **`src/ai_daily_digest/shared/schemas.py`** — tighten `DocumentSnapshot` exactly as
  section 9 specifies: aware UTC `fetched_at`, required `content_text`, frozen model.
- **`src/ai_daily_digest/delivery/api/` readiness** — `DatabaseReadinessProbe` (implements
  `ReadinessProbe`, runs bounded `SELECT 1`).
- **`src/ai_daily_digest/delivery/api/app.py`** — `create_app()` gains the optional injected
  read-repository parameter and wires `"database"` into readiness when configured.
- **`tests/integration/`** — `conftest.py` (engine + per-test rollback fixture),
  `test_source_item_repository.py`, `test_migrations.py` (`upgrade`→`downgrade`→`upgrade` on
  a real database), `test_immutability_triggers.py`.
- **`tests/unit/ingestion/`** — the in-memory fake repository and its tests.
- **`.github/workflows/ci.yml`** — `postgres:17` service + `alembic upgrade head` step for
  the `tests` job.
- **`compose.yaml`** — local PostgreSQL.
- **`Makefile`** — `db-up`, `db-migrate`, `db-revision`, `test-integration`.
- **A short local-database doc** — running migrations and integration tests.
- **`.env.example`** — unchanged (`DATABASE_URL` already present); no new variable in
  Phase 1.

**Explicitly excluded from that PR:**

- FastAPI domain routes; `GET /v1/updates`; cursor route integration;
- collectors and any external network call;
- pgvector, embeddings, or any vector table;
- intelligence persistence (`facts`, `changes`, `digests`, …);
- subscriptions and email tables;
- production deployment or cloud provisioning.

### 17. Dependency recommendation (no installation in this PR)

| Package | Role | Why not stdlib / an existing dependency | Recommended constraint |
|---|---|---|---|
| `sqlalchemy` | Typed ORM + query layer | No stdlib ORM; `docs/ARCHITECTURE.md` baseline | `>=2.0` |
| `alembic` | Reviewable schema migrations | No stdlib migration tool; `AGENTS.md` requires migrations | `>=1.13` |
| `psycopg[binary]` | PostgreSQL driver (sync + async) | No stdlib PostgreSQL driver; section 5 | `>=3.2` |

`greenlet` arrives transitively with SQLAlchemy's async support — expected, not a separate
direct dependency. No dependency is added by this ADR; the table is the pre-agreed set for
the implementation PR.

## Consequences

- One database serves structured queries now and semantic retrieval later; provenance
  survives a vector-index rebuild (unchanged from the original decision).
- `GET /v1/updates` (ADR 0008 PR 4) becomes implementable: a real configured repository
  adapter exists, satisfying ADR 0010's "no public route merges without a functional
  configured data source."
- The team manages schema migrations and (still-deferred) backups.
- Async SQLAlchemy keeps the FastAPI event loop unblocked and matches the async collector
  path, at the cost of an async `env.py` for Alembic and the `greenlet` transitive
  dependency.
- Psycopg 3 is one driver for both the async runtime and synchronous Alembic.
- `text[]` for `authors`/`tags` keeps Phase 1 simple; a future tag dimension is an additive
  migration that never touches an ordering column.
- Immutability triggers make ADR 0008 §5.D layer 3 real and CI-testable without limited-role
  database provisioning; column-privilege hardening remains available for production.
- The cross-module read Protocol in `shared/` follows the `snapshot_resolver` precedent and
  keeps `delivery/api/` free of any `ingestion` import.
- Integration tests need a real PostgreSQL in CI and (optionally) locally; the inner
  `make check` loop stays database-free and fast.
- A separate vector service remains an option if measured scale or features require it
  (unchanged).

## Risks, rollback, and migration considerations

- **No data migration** — the schema is greenfield; ADR 0007 already established "No
  persistent production data exists yet." The initial migration's `downgrade` drops both
  tables, both trigger functions, both triggers, and `alembic_version`. Once real rows exist,
  a table drop is destructive and requires the (still-deferred) backup procedure.
- **Autogenerate blind spots** — Alembic autogenerate does not reliably emit `text[]` columns
  or trigger DDL. The initial migration is hand-written; `test_migrations.py` proves
  `upgrade`→`downgrade`→`upgrade` on a real database.
- **Trigger maintenance** — every future protected column must be added to its table's
  trigger in the same migration that adds the column. Recorded as a checklist item for
  reviewers.
- **Circular FK** — handled by DDL ordering (section 9) and runtime write ordering
  (section 13); no `DEFERRABLE` constraint.
- **Driver packaging** — `psycopg[binary]` wheels suit CI and local development; a production
  image may want `psycopg[c]` or system `libpq`. Flagged for the deployment ADR.
- **Async learning curve** — `AsyncSession`, `async_sessionmaker`, and `run_sync` in
  `env.py` are well-documented SQLAlchemy 2.0 patterns; the risk is familiarity, not
  stability.
- **`shared/` and `delivery/api/app.py` are coordination files** — the implementation PR
  must rebase on current `main` and rerun `make ci` (ADR 0010).

## Team acceptance checklist

The implementation choices are no longer left for the coding PR to invent. Reviewers accept
or request a specific change to this ADR before implementation begins:

1. **Person A / ingestion:** confirm source-item identity and mutable columns, list
   normalization, per-item transaction behaviour, and immutable snapshot semantics.
2. **Person B / intelligence:** confirm stored snapshot content is mandatory, provenance
   cannot be deleted or overwritten, and the future database-backed snapshot resolver is a
   separate implementation concern.
3. **Person C / delivery:** confirm async repository access, shared read Protocol, readiness
   wiring, and the exact ordering index required by `GET /v1/updates`.
4. **All reviewers:** confirm SQLAlchemy 2 + Alembic + Psycopg 3, PostgreSQL 17 in the
   existing CI test job, PL/pgSQL immutability triggers, and the Phase-1 pool/timeouts.
5. **ADR placement:** these details remain an amendment to ADR 0002 rather than creating
   ADR 0011. If a reviewer requires a split, request it before accepting this ADR.

Acceptance approves the decisions, boundaries, implementation scope, and mandatory tests
above. It does not approve pgvector, collectors, API routes, cloud deployment, or any item
listed as deferred below.

## Deferred operational decisions

These do not block the first persistence PR, but must be resolved before deployment
(original list, extended):

- backup frequency, retention period, restore owner, and a tested restore procedure;
- retention/deletion rules for raw content and subscriber personal data;
- ordinary PostgreSQL indexes for source freshness, `publisher` / `source_id` filtering,
  snapshot history, publication status, and email idempotency — chosen from measured query
  behaviour (the `/v1/updates` keyset index in section 8 is the one exception, promoted into
  the foundation migration because the endpoint cannot page without it);
- the pgvector distance metric and index type, chosen from measured corpus size / query
  behaviour;
- migration and rebuild procedures that prove the derived vector index can be deleted and
  restored without losing source history;
- the production PostgreSQL role model (owner/migration role vs restricted runtime role) and
  whether column-level `UPDATE`/`DELETE` privileges are revoked from the runtime role as
  defence in depth on top of the triggers;
- connection-pool sizing, `statement_timeout`, and connect-timeout values, tuned to the
  deployment;
- the production `psycopg` packaging choice (`[binary]` vs `[c]` vs system `libpq`).

## References

- [ADR 0001](0001-modular-monolith.md) — module boundaries; ingestion / delivery / shared.
- [ADR 0003](0003-quality-gates.md) — the local and CI check loops these tests join.
- [ADR 0007](0007-uuid-v7-identifier-strategy.md) — application-side UUID v7, native `uuid`
  columns, the dependency-validation bar reused in section 16.
- [ADR 0008](0008-cursor-pagination-contract.md) — the `(first_fetched_at, id)` ordering
  tuple, the `publisher` / `source_id` filters, `limit + 1` reads, and §5.D's three
  immutability layers.
- [ADR 0010](0010-fastapi-openapi-contract-authority.md) — how infrastructure enters routes,
  the readiness-probe requirement, the "no `NotImplementedError` adapter" rule, and the
  coordination-controlled file list.
- `docs/ARCHITECTURE.md` — technology baseline (SQLAlchemy 2 + Alembic, PostgreSQL,
  pgvector), storage rules, collection flow.
- `docs/API_CONTRACT.md` — the `SourceItem` and `DocumentSnapshot` wire contracts this schema
  mirrors.
- `src/ai_daily_digest/shared/snapshot_resolver.py` — the `shared/` cross-module Protocol
  precedent, and its explicit deferral of the sync-vs-async database decision.
