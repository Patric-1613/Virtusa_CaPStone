# 0008 — Cursor pagination contract for list endpoints

Status: Accepted by Persons A, B, and C
Date: 2026-09-02

Person A authors and owns this ADR. Persons B and C must review and approve it before the
status changes to `Accepted`, because it authorizes changes to shared models
(`src/ai_daily_digest/shared/schemas.py`) and to the public API surface
(`docs/API_CONTRACT.md`).

## Context

- `docs/API_CONTRACT.md` already commits the API to "cursor-based" pagination with a
  `next_cursor` field, and to "business ordering [that] always uses the resource's explicit
  timestamp field; a UUID is never business chronology, only an opaque secondary tie-breaker".
  It does not define the cursor format, the per-endpoint sort tuples, the filter set, range
  semantics, the terminal-page behaviour, or the error response for a bad cursor. Every list
  endpoint is blocked on those details.
- [ADR 0007](0007-uuid-v7-identifier-strategy.md) requires that business chronology come from
  an explicit business timestamp and that a record's UUID be used **only as an opaque unique
  tie-breaker** in a keyset ordering tuple `(business_timestamp, id)`. UUID v7 embeds an
  approximate creation time; that embedded time must never be decoded, and a UUID's sort
  position among rows that share a business timestamp carries no business meaning. ADR 0007
  explicitly scopes "the full, endpoint-specific specification" of pagination to a dedicated
  pagination ADR — this one.
- No pagination code exists yet: there is no cursor codec, no `Page` model, no list route, no
  database query, and no pagination test. `src/ai_daily_digest/delivery/` contains only its
  package file and `README.md`.
- `SourceItem` already has a required `first_fetched_at: datetime`.
- `Digest.digest_date` is currently an **unvalidated `str`** (`# YYYY-MM-DD`). A string such as
  `"2026-13-40"` is accepted today.
- `Change` has **no** required detection timestamp. `FactObservation.observed_at` is optional
  and represents when a fact was observed in a source, not when the pipeline detected a change.
- Person B is separately handling shared Enum policy in
  [ADR 0009](0009-shared-enum-policy.md). Person C is separately handling FastAPI and OpenAPI
  in ADR 0010. This ADR must not edit their files, pre-empt their decisions, or absorb their
  implementation concerns. It defines only the pagination contract and the shared-model
  prerequisites that pagination cannot proceed without.

## Decision

### 1. Method

List endpoints use **keyset (seek) pagination**.

Phase 1 explicitly excludes:

- offset pagination;
- page numbers;
- previous-page (backward) cursors;
- total or approximate counts;
- arbitrary client-selected sort fields;
- multi-value and `OR` filters.

Each of these can be revisited in a later ADR if a concrete need appears. None is required for
the MVP, and several (offset, counts) scale poorly and are hard to make consistent.

### 2. Response envelope

Every paginated response body has exactly two keys:

```json
{
  "items": [],
  "next_cursor": null
}
```

Rules:

- `items` is always a JSON array (empty, never `null`).
- `next_cursor` is always present.
- `next_cursor` is an opaque string when another page exists.
- `next_cursor` is `null` on the last page and on an empty result.
- The envelope has no `total`, `total_count`, `page`, `has_more`, or `prev_cursor` field.

### 3. Limits

- Default page size: **20**, used when `limit` is **omitted entirely**.
- Minimum: **1**. Maximum: **100**.
- A **supplied** `limit` is validated and rejected with **HTTP 422** when it is:
  - a non-integer value;
  - a value below `1` or above `100`;
  - an empty value such as `?limit=`.
- Every `limit` rejection is returned in the project's standard error envelope by the
  request-validation exception handler described in section 3.1.
- `limit` is **not** encoded in the cursor and is not part of the filter fingerprint.
- A client may change `limit` between requests while continuing the same traversal.

#### 3.1 Request-validation responses

FastAPI's built-in request validation does **not** use this project's error envelope: it
returns its own `detail` structure with HTTP 422. Person C's `delivery/api/` implementation
must install a **request-validation exception handler** that maps every request-validation
failure — an invalid `limit`, a malformed query parameter, a bad path value, an out-of-range
range bound (section 8.1) — to the project's standard error envelope while **keeping the HTTP
422 status**. ADR 0010 remains authoritative for the general error-handler design; this ADR
only requires that pagination's 422 responses arrive in the standard envelope.

### 4. Ordering

Every list endpoint orders by `(business_sort_value DESC, id DESC)` — newest first, with the
record's UUID as an **opaque secondary tie-breaker only** (ADR 0007). The UUID is never
decoded, and its position among rows sharing a `business_sort_value` carries no business
meaning; it exists solely to make the ordering a total order so a cursor can resume exactly.

| Endpoint       | Ordering tuple                    | Sort identifier                    |
| -------------- | --------------------------------- | --------------------------------- |
| `/v1/updates`  | `(first_fetched_at DESC, id DESC)` | `first_fetched_at:desc,id:desc`   |
| `/v1/changes`  | `(detected_at DESC, id DESC)`      | `detected_at:desc,id:desc`        |
| `/v1/digests`  | `(digest_date DESC, id DESC)`      | `digest_date:desc,id:desc`        |

**`/v1/updates`** orders by `first_fetched_at`, which is when the service first discovered the
item. This is **service-discovery order, not publisher publication order**: a recently
discovered but old article can appear near the top of the feed. `first_fetched_at` is chosen
over `published_at` because it is required, non-null, internally controlled, and immutable —
`published_at` is nullable, externally supplied, and may be corrected by a publisher after the
fact, which would move a row across a live cursor boundary and cause a skip or a duplicate.
`first_fetched_at` **must be immutable after creation** and ingestion must never rewrite it
(see section 5.C).

**`/v1/changes`** orders by `detected_at` (see section 5.A — this field does not exist yet).

**`/v1/digests`** orders by `digest_date` (see section 5.B — this field must become a real
date).

**Continuation predicate** for the descending order, applied after all filters:

```
(sort_value, id) < (last_sort_value, last_id)
```

This is a row-value (tuple) comparison, evaluated left to right, and pairs with
`ORDER BY sort_value DESC, id DESC`. Filters narrow the result set first; the continuation
predicate then positions the traversal within the filtered set. Clients never parse, compare,
or sort IDs themselves — the server owns the tuple comparison.

### 5. Shared-contract preconditions

This ADR is the architecture decision that **authorizes** the shared-model changes below. They
cover **every component of every ordering tuple** in section 4 — the three business sort
fields (`SourceItem.first_fetched_at`, `Change.detected_at`, `Digest.digest_date`) and the
resource `id` used as the tie-breaker. They are pagination prerequisites, not Enum concerns,
and not Person B's to carry. This ADR does **not** create a separate ADR 0011 — ADR 0008 is
the authorizing decision.

#### 5.A `Change` gains `detected_at`

`Change` (in `src/ai_daily_digest/shared/schemas.py`) gains a field:

```python
detected_at: datetime
```

Requirements:

- **Required** — no default.
- **Timezone-aware.** A timezone-naive `datetime` is **rejected** at the model boundary.
- An aware input is **normalized to UTC** at the model/storage boundary **without losing
  microseconds**; **ISO 8601 UTC** on the wire, consistent with the existing datetime fields
  in the contract.
- Represents **when the intelligence pipeline detected the Change**, not when a source
  published or a fact was observed.
- **Immutable after construction** (see section 5.D).
- **Supplied through an injected batch clock / time value** carried by the orchestrator
  (`intelligence/daily_run.py` / `intelligence/graph.py`) and passed into
  `FactStore.update_fact()`. It is **never** produced by `datetime.now()` inside a Pydantic
  model or a graph node, so unit tests stay independent of wall-clock time
  (`AGENTS.md` testing rules).

Rationale: `/v1/changes` returns individual field-level `Change` summaries, each addressed by a
`Change` ID, so ordering that list requires a per-`Change` business timestamp. `ChangeSet` has
no timestamp, `FactObservation.observed_at` is optional and has different semantics, and a
value computed from nested facts would be derived and nullable — none is a stable sort key.

#### 5.B `Digest.digest_date` becomes `datetime.date`

`Digest.digest_date` changes from `str` to `datetime.date`.

Requirements:

- Use `datetime.date`.
- Wire JSON representation continues to serialize as exactly `YYYY-MM-DD` (Pydantic serializes
  `date` this way, so existing fixtures and API examples remain byte-for-byte valid).
- Invalid values such as `"2026-13-40"` are **rejected** at the model boundary instead of
  being stored as an unparseable string.
- The eventual database column is a native `DATE`.
- **Immutable after construction** (see section 5.D).

Pagination does **not** decide digest uniqueness, regeneration, correction, or version-history
policy — that is a separate digest-lifecycle decision. If that future policy permits more than
one digest record to share a `digest_date`, those records are still totally ordered by the `id`
tie-breaker (section 4), so keyset traversal stays correct either way.

#### 5.C `SourceItem.first_fetched_at` invariants

`SourceItem.first_fetched_at` already exists as a required `datetime`. This ADR tightens it so
it is safe as an ordering key:

- A timezone-naive `datetime` is **rejected** at the model boundary.
- An aware input is **normalized to UTC** at the model/storage boundary **without losing
  microseconds**.
- **Immutable after construction** (see section 5.D).
- **Ingestion must never rewrite it** — not on a re-fetch, a content-hash change, a new
  `DocumentSnapshot`, or any later correction. It records the first time the service saw the
  item and nothing after that moves it; `updated_at` and later snapshots carry subsequent
  activity.

#### 5.D Ordering-field and ID immutability (all endpoints)

Every ordering tuple in section 4 is `(business_sort_value, id)`; both components must be
immutable for the life of the record. The **protected ordering columns** are:

- `SourceItem.id`
- `SourceItem.first_fetched_at`
- `Change.id`
- `Change.detected_at`
- `Digest.id`
- `Digest.digest_date`

Immutability is enforced in **three separate layers**, none of which substitutes for another:

1. **Model-level freezing.** Each `id` is already opaque and immutable under ADR 0007; the
   three business sort fields are immutable per sections 5.A–5.C. The implementation must
   **prevent ordinary reassignment** of every protected column — plain attribute assignment
   must not succeed.
2. **Repository restriction (defence in depth).** The repository interface exposes **no
   update path** for the protected columns. This is genuine, useful protection, but it is
   **application-level** protection and must not be described as storage-level enforcement — a
   direct SQL statement or a different client bypasses it entirely.
3. **Storage-level enforcement.** The database itself must reject a change to an existing
   protected value. A normal PostgreSQL `CHECK` constraint is **not sufficient**: it validates
   only the resulting row and cannot compare `OLD` with `NEW`, so it cannot tell "this value
   was always X" from "this value just changed to X". Storage-level enforcement must use a
   mechanism capable of rejecting a change to an existing value, such as:
   - a `BEFORE UPDATE` trigger that raises on any change to a protected column; or
   - appropriately restricted column-level `UPDATE` privileges for the application database
     role.
   The exact mechanism is chosen in the future database schema / migration PR — this
   documentation PR does not select or implement a trigger or privilege design.

Application code must also **not bypass model-level freezing with `model_copy(update=...)`**
on a protected column. ADR 0009 already records that `model_copy(update=...)` skips Pydantic
validation; for these fields the rule is stronger — they are not updated by copy at all.

#### Delivery of the section 5 changes

The changes in sections 5.A–5.D ship in a **single focused shared-contract PR** (see
section 13, PR 2) after this ADR is accepted, once `shared/schemas.py` is not being actively
edited by another author. They are **not** added to Person B's Enum implementation PR, which
stays Enum-only.

### 6. Cursor format

The cursor is **opaque to clients, authenticated, and versioned**. It is built with the Python
standard library only — `base64`, `hashlib`, `hmac`, `json` — and adds **no production
dependency**.

**Wire token:**

```
base64url(canonical_payload_without_padding) + "." + base64url(hmac_sha256_signature_without_padding)
```

**Conceptual payload:**

```json
{
  "v": 1,
  "r": "updates",
  "s": "first_fetched_at:desc,id:desc",
  "f": "sha256-hex-filter-fingerprint",
  "k": {
    "t": "2026-09-02T10:00:00.123456Z",
    "id": "0192f0c4-1a2b-7c3d-8e4f-2b1c0d9e8f7a"
  }
}
```

| Field | Meaning |
| ----- | ------- |
| `v`   | Cursor schema version. An unrecognized version is rejected. |
| `r`   | Endpoint / resource identifier. Must equal the endpoint being called. |
| `s`   | Exact sort identifier (from section 4). Must equal the endpoint's active sort. |
| `f`   | SHA-256 fingerprint of the canonical request filters (section 7). |
| `k.t` | Exact sort value of the last **returned** row. |
| `k.id` | UUID v7 of the last **returned** row. |

**Datetime cursor values (`k.t` when the sort value is a timestamp):**

- must be timezone-aware and normalized to UTC;
- must preserve exact **microsecond** precision;
- use a canonical form with **six fractional digits** and a trailing `Z`
  (`YYYY-MM-DDTHH:MM:SS.ffffffZ`);
- must **never** be truncated to whole seconds — truncation would make two rows in the same
  second indistinguishable to the continuation predicate and risk a skipped or duplicated row.

**Date cursor values** (`/v1/digests`, where the sort value is a calendar date) use
`YYYY-MM-DD`.

**Canonical JSON** for both the signed payload and the filter fingerprint input:

- keys sorted;
- `separators=(",", ":")` (no incidental whitespace);
- UTF-8 encoded;
- no floating-point values;
- unknown keys rejected on decode.

**Cursor properties:**

- HMAC-SHA256 authenticated; **not encrypted**. Clients can base64-decode and read the payload;
  its contents must therefore be treated as visible to clients.
- Contains **no sensitive data** — no subscriber information, no secrets, no internal-only
  identifiers beyond the already-public resource `id`.
- **No `issued_at`** and **no expiration** in Phase 1.
- **One configured signing secret**, **no key ID**. Rotating the secret invalidates all
  outstanding cursors; clients then restart the traversal from page one (no cursor). This is
  acceptable for a read-only API and is documented as expected behaviour.
- The signed `r` and `s` fields bind a cursor to one endpoint and one sort, which prevents
  replaying a cursor issued by a different endpoint.

**Maximum encoded cursor length: 512 characters.** A longer value is rejected **before** any
base64 decoding, so an oversized token cannot force allocation or parsing work.

**Signing-key handling.** Code cannot measure how much entropy arbitrary bytes carry, so the
requirement is stated in terms the system can actually enforce:

- the decoded / injected signing key must be **at least 32 bytes long**;
- deployment documentation must require generating it with a **cryptographically secure random
  generator** (for example `secrets.token_bytes(32)` or `openssl rand`);
- the **configuration layer validates the key's byte length** and rejects a shorter key at
  startup;
- the `CursorCodec` receives the already-validated key bytes **through dependency injection**
  and must not read environment variables or any other ambient configuration itself.

`PAGINATION_CURSOR_SECRET` is **not** added to `.env.example` by this ADR or by the codec PR.
It is added only when a real configuration provider actually consumes it (Person C's
configuration or deployment work), so the example file never lists a variable that nothing
reads.

### 7. Filter binding

The cursor binds **every filter that affects result membership**, including hidden ones. The
fingerprint (`f`) is computed from a canonical representation of the request's filters:

- endpoint filter keys only;
- exclude `cursor`;
- exclude `limit`;
- trim surrounding whitespace from string values;
- normalize string values with Unicode **NFC**;
- normalize datetime values to exact UTC RFC 3339 with microseconds preserved;
- normalize date values to `YYYY-MM-DD`;
- omit absent filters entirely (a missing filter is not the same as an explicit `null`);
- include fixed hidden visibility constraints (for example, digests are published-only);
- include the cursor version;
- include the sort identifier;
- serialize as canonical JSON (section 6);
- hash with SHA-256; `f` is the lowercase hex digest.

**Canonicalization happens exactly once, at the HTTP boundary, and produces one typed
canonical filter object.** That same object is the single input to **both**:

1. computing the cursor filter fingerprint `f`; and
2. executing the repository query.

Raw request query strings are **not** passed to the repository separately after `f` is
computed — the repository interface accepts the **canonical typed filters**, never raw request
strings. The fingerprint and the query therefore cannot disagree about what was filtered.
Consequences of the canonical form, each deliberate:

- surrounding whitespace is **ignored** — `" OpenAI "` and `"OpenAI"` are the same filter, the
  same `f`, and the same query;
- Unicode **NFC-equivalent** values are the same filter;
- **case remains significant** — `"openai"` and `"OpenAI"` are different filters;
- an **absent** value stays distinct from any explicit value (absent means "no constraint");
- **hidden constraints** such as `published_only` for `/v1/digests` are part of **both** the
  fingerprint input **and** the repository query — they can never be applied to one and not
  the other.

The server recomputes `f` from the current request's canonical filters and compares it to the
cursor's `f` with a constant-time comparison. If a client changes any membership-affecting
canonical filter (or the hidden visibility rule, the sort, or the cursor version changes)
while presenting an existing cursor:

- respond with **HTTP 400**, error code **`invalid_cursor`**;
- do **not** silently restart pagination or reinterpret the request.

### 8. Phase 1 filters

**`/v1/updates`:**

- `publisher`
- `source_id`

Both are **exact, case-sensitive, single-value** filters (both are effectively identifiers —
`source_id` is a `sources.yaml` slug; `publisher` is a proper noun). Update **date-range
filters are deferred**: update ordering is by `first_fetched_at`, while the natural
publication timestamp is nullable and externally controlled, so a `published_*` range filter
needs its own null-handling decision that is out of scope here.

**`/v1/changes`:**

- `company`
- `product`
- `field`
- `detected_from`
- `detected_to`

`company`, `product`, and `field` are exact, case-sensitive, single-value filters
(`docs/API_CONTRACT.md` already promises `/v1/changes` is "filterable by company/product/date").
`detected_from` / `detected_to` filter on `detected_at`.

**`/v1/digests`:**

- `date_from`
- `date_to`

Filter on `digest_date`. **Published-only** is a fixed, implicit condition — not a
client-selectable status filter — and it is included in the filter fingerprint so that a
future change to the visibility rule invalidates existing cursors.

**Range semantics:** `from` is **inclusive**, `to` is **exclusive** — the half-open interval
`[from, to)`. This avoids sub-second boundary ambiguity and composes cleanly for contiguous
ranges. All supplied filters combine with **AND**.

#### 8.1 Range-bound validation

Applies to `detected_from` / `detected_to` (`/v1/changes`) and `date_from` / `date_to`
(`/v1/digests`):

- If **both** bounds are supplied, `from` must be **strictly earlier than** `to`. Equal bounds
  and reversed bounds are rejected with **HTTP 422** through the standard validation envelope
  (section 3.1).
- A **one-sided** range (only `from`, or only `to`, or neither) is valid.
- `detected_*` values must be **timezone-aware**; a naive timestamp is rejected with **HTTP
  422**. Accepted values are normalized to UTC **without losing microseconds** — the same
  canonical form as the cursor's `k.t` (section 6).
- `date_*` values are **calendar dates** (`YYYY-MM-DD`); an unparseable or non-calendar value
  (for example `2026-13-40`) is rejected with **HTTP 422**.
- The valid interval is `[from, to)` whichever bounds are supplied.

**Deferred:** `tags`; `review_status` (until its review lifecycle is defined — see
`docs/ARCHITECTURE.md`); multiple-value filters; `OR` behaviour; any client-selectable digest
status.

### 9. Invalid-cursor behaviour

A **supplied but unusable** cursor always returns:

- **HTTP 400**
- error code: **`invalid_cursor`**
- message: `"The pagination cursor is invalid for this request."`

This covers, without distinction in the response:

- an empty cursor string;
- a malformed token (missing the `.` separator, wrong segment count);
- invalid base64;
- an invalid signature;
- invalid JSON;
- missing or unknown payload fields;
- an unsupported cursor version;
- a resource (`r`) mismatch;
- a sort (`s`) mismatch;
- a filter (`f`) mismatch;
- a malformed UUID in `k.id`;
- a `k.id` that is a well-formed UUID but not version 7;
- an invalid or unparseable date/timestamp in `k.t`;
- a token longer than the maximum length.

The response **never** states which internal validation step failed. Server logs **may**
record a coarse, safe failure category (for example `reason=signature`), but must **never** log
the cursor token, the signature, the decoded payload, or the signing secret.

An **absent** `cursor` parameter is not an error — it requests the first page.

This ADR does **not** define a closed `ErrorCode` enum and does **not** ask Person C to
pre-reserve an `invalid_cursor` member. The error envelope's `code` field remains a string
(`docs/API_CONTRACT.md`). Person C wires the literal `"invalid_cursor"` into the delivery
error handling after this ADR is accepted.

### 10. Terminal and empty-page behaviour

The query fetches **`limit + 1`** rows.

- Return **at most `limit`** items.
- If the extra `(limit + 1)`-th row exists, build `next_cursor` from the **last returned**
  item's `(sort_value, id)`.
- **Never** build the cursor from the unreturned extra row.
- Otherwise `next_cursor` is `null`.

Cases:

- Empty initial result: `{"items": [], "next_cursor": null}`.
- Final non-empty page: the remaining items, `next_cursor` `null`.
- A request made after the last item (cursor points past the end): `{"items": [], "next_cursor": null}`.
- A result of exactly `limit` rows with no extra row: `next_cursor` `null`.

### 11. Concurrency

Phase 1 keyset traversal is **not** a transactionally frozen snapshot across requests. A
database transaction must never be held open across HTTP requests, and there is no `as_of`
boundary in Phase 1.

**Scoped "exactly once" guarantee.** For any record that, for the whole duration of a
traversal:

- remains present (not deleted); and
- remains a member of the same filtered result set; and
- retains immutable ordering values (`sort_value` and `id`),

that record is returned **exactly once** across the pages of the traversal — never duplicated,
never skipped.

**Outside that scope, a later page can legitimately observe a different world than an earlier
one, because Phase 1 is not snapshot isolation:**

- a record **deleted** mid-traversal does not appear on the page it would have been on;
- a record whose **membership-affecting field changes** can enter or leave the filtered set
  mid-traversal — for example a `Change`'s `company`, or a **draft digest becoming published**
  so it enters the published-only `/v1/digests` result — and may then appear on, be absent
  from, or shift relative to the cursor;
- these are accepted Phase 1 behaviours, not defects. A client must either tolerate such
  membership changes or restart the traversal to refresh it — and a restart only resumes from
  the *current* page-one state; it does not reconstruct the result set as it was when the
  traversal began. A genuinely frozen, consistent view would require a future `as_of` boundary
  or a snapshot-isolation design, which Phase 1 does not provide.

**Insertions** (consistent with the scoped guarantee):

- a record inserted after the traversal starts, with a sort value newer than the cursor
  position, sorts *above* the cursor in a descending scan and so does not appear on later pages
  of that traversal; the client requests page one again, without a cursor, to see newer
  records;
- a **backdated** insert (a new record whose sort timestamp is older than the cursor position)
  may appear once on a later page; it causes no duplication or skip of any *other* record.

**Mutating an ordering value (`sort_value` or `id`) of an existing record is prohibited** — it
would break the scoped guarantee for that record, and the fix is to prevent the mutation, not
to work around it in pagination. `first_fetched_at`, `detected_at`, `digest_date`, and every
`id` are immutable by contract and enforced across the three layers in section 5.D — model
freezing, repository restriction, and database storage-level enforcement.

### 12. Public summary models

List endpoints return **delivery-specific summary models**, never the raw shared models, so
internal fields cannot leak through a list response. These models live under `delivery/api/`
and are **not** placed in `shared/`.

**`UpdateSummary`** includes: `id`, `source_id`, `publisher`, `title`, `canonical_url`,
`published_at`, `first_fetched_at`, `event_id`, `tags`, `language`, `latest_snapshot_id`.
It **excludes** `dedupe_key`, raw-storage information, and body content.

**`ChangeSummary`** includes: `id`, `change_set_id`, `subject`, `field`, `change_type`,
`detected_at`, `confidence`, and a reduced `previous` / `current` evidence pair containing only
`value` and `snapshot_id`. It **excludes** `review_status` (until its lifecycle is defined) and
detail-only source data.

**`DigestSummary`** includes: `id`, `digest_date`, `status`, `title`, `claim_count`. It
**excludes** the `claims` array — that belongs to the digest detail endpoint. In Phase 1
`status` is always `published`; it is included for wire-shape stability.

**Never exposed in any list response:** `DocumentSnapshot.content_text`, `raw_location`,
`dedupe_key`, `ExtractedFact` internals, unsupported claims, and pending or unpublished
digests.

The exact Pydantic class definitions, field types, and OpenAPI component/`operationId` names
are Person C's OpenAPI implementation concern (ADR 0010), constrained by the field lists above.

### 13. Implementation boundaries

**Contract-change rule — applies to every PR below.** Per `docs/API_CONTRACT.md`'s
contract-change process, any PR that introduces or changes a public request or response
Pydantic model, or its generated OpenAPI schema, updates `docs/API_CONTRACT.md` **and** the
contract tests **in that same PR**. A PR may document semantics ahead of a concrete schema only
when it ships no public request/response schema of its own.

**PR 1 — this ADR only.** `docs/adr/0008-cursor-pagination-contract.md` and nothing else. No
`docs/API_CONTRACT.md` change, no Python, no dependency, no test. Status stays `Proposed` until
Persons B and C approve.

**PR 2 — shared ordering-field contract.** After this ADR is accepted and once
`shared/schemas.py` is not being actively edited by another author (in particular, after
Person B's Enum implementation no longer holds that file). One focused PR, covering:

- `SourceItem.first_fetched_at` — naive-datetime rejection, UTC normalization with microseconds
  preserved, and immutability, in `shared/schemas.py` (section 5.C);
- `Change.detected_at` — the new required field in `shared/schemas.py` (section 5.A);
- `Digest.digest_date: datetime.date` in `shared/schemas.py` (section 5.B);
- per-field immutability enforcement for the ordering fields and the `id` fields, and removal
  of any `model_copy(update=...)` path that would touch them (section 5.D);
- `FactStore.update_fact()` gains a `detected_at` parameter and stamps it onto the `Change` it
  builds;
- `intelligence/graph.py` and `intelligence/daily_run.py` thread the run's **injected batch
  detection time** into `FactStore.update_fact()` — no node or store calls `datetime.now()`;
- `intelligence/assemble_digest.py` accepts and passes a `datetime.date` digest date;
- `intelligence/daily_run.py` builds and passes the digest date as the typed `date`;
- the empty-digest fallback in `intelligence/evaluate.py` no longer constructs a `Digest` with
  `digest_date=""` — this ADR does not fix the exact replacement, only requires that it uses a
  valid deterministic typed `date`, or handles an empty fixture set without constructing an
  invalid placeholder `Digest` at all;
- all affected fixtures (`tests/fixtures/contracts/`), model constructors, unit and contract
  tests, and `docs/API_CONTRACT.md` field semantics — updated together in this PR
  (contract-change rule).

This is a distinct concern from Enums and is not merged into Person B's PR.

**PR 3 — pure pagination codec and cursor/envelope semantics.** After this ADR is accepted and
after coordinating ownership of `docs/API_CONTRACT.md`:

- `src/ai_daily_digest/delivery/api/pagination.py` containing `Page[T]`, `CursorPayload`,
  `CursorCodec`, `InvalidCursorError`, and the canonical-filter helpers;
- cursor codec, canonicalization, and pure-helper tests (section 14);
- the pagination **rules and cursor/envelope semantics** in `docs/API_CONTRACT.md` — method,
  the `{items, next_cursor}` envelope, limit rules, ordering, cursor opacity and binding,
  filter fingerprinting, range validation, `invalid_cursor`, terminal behaviour, and
  concurrency — with the generic envelope JSON example.

`Page[T]` is a generic container, not a public resource schema; the concrete summary schemas it
wraps are added by the endpoint PRs. PR 3 therefore ships **no** per-endpoint summary schema,
**no** FastAPI route, **no** database code, **no** migration, **no** dependency, and touches
**no** `app.py`, `pyproject.toml`, or `uv.lock`. It does not change `.env.example`.

**PR 4 — first paginated endpoint: `GET /v1/updates`.** Only after Person C's FastAPI
foundation exists, PR 3 is merged, and a functional configured repository adapter exists
(a production adapter that only raises `NotImplementedError` does not count; the in-memory
fake stays test-only). This PR ships, **together in one PR** (contract-change rule):

- the concrete `UpdateSummary` Pydantic schema and the `Page[UpdateSummary]` response;
- the `GET /v1/updates` route, its route contract, and the `UpdateSummary` field list in
  `docs/API_CONTRACT.md`;
- OpenAPI query/response schema tests and HTTP tests — standard 400 and 422 envelopes, no
  internal fields in the response;
- one endpoint only.

**Later.** `/v1/digests`; then `/v1/changes` (only after `detected_at` exists); database
indexes and migrations once persistence exists. Each endpoint PR follows the same
contract-change rule: its summary schema, route contract, OpenAPI tests, and
`docs/API_CONTRACT.md` changes land together.

### 14. Tests the implementation PRs must include

Each test lives in the PR that owns the code under test. PR 3 is pure Python — no HTTP layer,
no FastAPI route, no repository — so it tests only the codec, the pure helpers, and the
generic `Page[T]` container. PR 3 helpers may raise typed/domain validation errors; mapping
those errors onto an HTTP 400 or 422 response is tested in the endpoint PRs, not here.

**PR 2 — shared ordering-field contract.**

- A timezone-naive `SourceItem.first_fetched_at` is rejected.
- An aware `SourceItem.first_fetched_at` is normalized to UTC with microseconds preserved.
- Reassigning `SourceItem.first_fetched_at` on an existing model is rejected.
- A `Change` without `detected_at` is rejected; a timezone-naive `detected_at` is rejected.
- An aware `Change.detected_at` is normalized to UTC with microseconds preserved.
- `Change.detected_at` is threaded through `FactStore.update_fact()`; the injected detection
  time is used **exactly** (equality assertion), and no test in that path uses wall-clock time.
- Reassigning `Change.detected_at` on an existing model is rejected.
- An invalid `Digest.digest_date` (for example `"2026-13-40"`) is rejected; a valid date still
  serializes as `YYYY-MM-DD` on the wire.
- Reassigning `Digest.digest_date` on an existing model is rejected.
- An `id` used as an ordering tie-breaker (`SourceItem.id`, `Change.id`, `Digest.id`) cannot be
  reassigned.
- No construction or update path uses `model_copy(update=...)` to mutate an ordering key —
  asserted by a targeted test or a lightweight static check over the intelligence code.
- `intelligence/assemble_digest.py` and `intelligence/evaluate.py` operate on the new typed
  `date` — including `evaluate.py`'s empty-fixture path, which produces no invalid placeholder
  `Digest`.

**PR 3 — cursor codec and pure helpers.**

- *Encoding / decoding:* deterministic round-trip; canonical JSON is byte-identical across
  repeated encodes; `datetime` microseconds are preserved exactly through a round-trip; date
  values round-trip as `YYYY-MM-DD`.
- *Signatures and tamper detection:* a tampered payload, a tampered signature, a truncated
  token, malformed base64, and malformed JSON are each rejected; an oversized token is
  rejected before any decode; `hmac.compare_digest` is used for verification; the signing key
  and the token never appear in an exception message, `repr`, or log line.
- *Cursor field validation:* a missing field, an unknown field, an unsupported version, a
  resource (`r`) mismatch, a sort (`s`) mismatch, a filter (`f`) mismatch, a malformed UUID in
  `k.id`, a well-formed but non-v7 UUID, and an unparseable `k.t` are each rejected. Every
  rejection surfaces as the codec's typed `InvalidCursorError`, never an HTTP status.
- *Canonical filter fingerprint:* the same logical filters in any argument order produce the
  same `f`; adding, removing, or changing a bound filter changes `f`; an absent filter is
  omitted, not hashed as `null`, and stays distinct from an explicit value; the hidden
  visibility constraint, the sort identifier, and the cursor version are all part of `f`.
- *Canonicalization is deliberate:* `" OpenAI "` and `"OpenAI"` produce the **same** canonical
  value and the **same** `f`; NFC-equivalent Unicode inputs produce the same canonical value
  and `f`; case-different values (`"openai"` vs `"OpenAI"`) produce **different** canonical
  values and `f`.
- *Unicode normalization:* NFC-equivalent filter strings produce the same `f`; unusual values
  (combining marks, emoji, right-to-left text) are handled deterministically.
- *Timestamp / date normalization helpers (pure):* a timezone-aware timestamp normalizes to
  UTC with microseconds preserved; a naive timestamp raises the helper's typed validation
  error; a non-calendar date string such as `2026-13-40` raises the helper's typed validation
  error.
- *Range helper (pure):* with both bounds present, `from` must be strictly earlier than `to` —
  equal or reversed bounds raise the helper's typed validation error; a one-sided range, and
  no bound at all, are accepted; the interval the helper represents is `[from, to)`.
- *Generic `Page[T]` serialization:* the model serializes to exactly
  `{"items": [...], "next_cursor": <string|null>}`; `items` is always an array; `next_cursor`
  is present and `null` when unset; no `total`, `page`, `has_more`, or `prev_cursor` key ever
  appears.
- *Changed-limit compatibility at the codec/fingerprint level:* `limit` is in neither the
  cursor payload nor `f`, so a cursor produced under one `limit` decodes and verifies
  unchanged under another.

**PR 4 — `GET /v1/updates`.**

- An omitted `limit` defaults to `20`.
- An invalid supplied `limit` (`0`, `101`, `-1`, a non-integer, an empty `?limit=`) returns
  **HTTP 422**, and that 422 body is the project's standard error envelope, not FastAPI's raw
  `detail`.
- An invalid cursor (any cause that raises the codec's `InvalidCursorError`) returns **HTTP
  400** with error code `invalid_cursor` in the standard envelope.
- The first page, a middle page, the final non-empty page, and an empty first page each return
  the documented envelope.
- An exact-`limit` result yields a `null` `next_cursor`; a result with a further row available
  yields a `next_cursor` built from the last returned item.
- Two items with equal `first_fetched_at` are ordered by the `id` tie-breaker, and a cursor
  positioned between them resumes correctly.
- A full traversal over items that stay present, stay in the filtered set, and keep immutable
  ordering values returns each such item exactly once — no duplicates, no skips.
- A changed `limit` between requests continues the same traversal.
- The response body matches `Page[UpdateSummary]` and exposes no internal field (`dedupe_key`,
  raw storage location, snapshot body); `app.openapi()` shows the `cursor` and `limit` query
  parameters, the `Page[UpdateSummary]` response schema, and a stable, explicitly assigned
  `operation_id` for the route.
- *Filter / query parity:* the repository fake (a spy) receives **exactly** the canonical
  filter values used to compute `f` — `" OpenAI "` and `"OpenAI"` reach the repository as the
  same value and select the same rows; NFC-equivalent inputs select the same rows;
  case-different values (`"openai"` vs `"OpenAI"`) select different rows.
- Changing a membership-affecting canonical filter while replaying a cursor is rejected at the
  HTTP boundary with `400` / `invalid_cursor`.

**Later — `/v1/digests` endpoint PR.**

- `date_from` / `date_to` HTTP validation: a non-calendar value, and equal or reversed bounds,
  return **HTTP 422** in the standard envelope.
- Published-only membership: an unpublished digest never appears in a page.
- Hidden-constraint parity: `published_only` is present in **both** the fingerprint input and
  the query the repository fake receives; an unpublished digest is excluded by the query, not
  merely dropped from the response.
- Digest-date pagination cases: multiple digests; `digest_date` ties resolved by the `id`
  tie-breaker; first, middle, final, and empty pages.

**Later — `/v1/changes` endpoint PR.**

- `detected_from` / `detected_to` HTTP validation: equal or reversed bounds return **HTTP
  422** in the standard envelope.
- A naive (timezone-less) `detected_*` bound returns **HTTP 422**.
- An accepted `detected_*` bound is applied in UTC with microsecond precision preserved.
- `detected_at` pagination cases: first, middle, final, and empty pages; `detected_at` ties
  resolved by the `id` tie-breaker.

**Later — persistence-adapter integration PR.**

- The continuation predicate is the exact tuple comparison
  `(sort_value, id) < (last_sort_value, last_id)`, paired with
  `ORDER BY sort_value DESC, id DESC`.
- The query fetches `limit + 1` rows; the extra row is never returned and is never used to
  build the cursor.
- An equal-sort-key traversal crosses a run of rows sharing one `sort_value` with no duplicate
  and no skip.
- Concurrency behaviour is verified against the real persistence boundary: a deleted record is
  absent from its page; a record whose membership-affecting field changes enters or leaves a
  later page; newer and backdated inserts behave as section 11 describes.
- *Protected-column immutability (one test per protected column in section 5.D —
  `SourceItem.id`, `SourceItem.first_fetched_at`, `Change.id`, `Change.detected_at`,
  `Digest.id`, `Digest.digest_date`):* an attempt to modify that column on an existing row
  proves that (1) the database **rejects the update**; (2) the transaction is **rolled back or
  otherwise handled safely** — no partial write; (3) the **previously stored value is
  unchanged** when re-read; and (4) the repository API exposes **no ordinary mutation path**
  for that field. These tests do not assert which storage mechanism (trigger vs. restricted
  `UPDATE` privilege) is in use — only that a direct update attempt fails and the value
  survives.

**Configuration — the config-provider PR.** A signing key shorter than 32 bytes is rejected at
startup by the configuration layer, not silently accepted by the codec.

### 15. Security requirements

- Standard library only for the codec; no new production dependency.
- The signing key must be at least 32 bytes long and generated with a cryptographically secure
  random generator; the configuration layer validates its length; it is injected into the codec
  as bytes, never read from the environment inside the codec, and never committed.
- Signature comparison is constant-time (`hmac.compare_digest`).
- Token size is checked before any decode; the decoded payload is strictly validated
  (canonical JSON, known keys only, typed fields) before any use.
- No value taken from a cursor is ever used as a SQL identifier. Sort identifiers map to
  columns through a hard-coded server-side table, never by interpolation.
- Never use `pickle`, `eval`, `marshal`, or unsafe YAML loading anywhere in cursor handling.
- A malformed cursor causes **no** database access — it is rejected before the query layer.
- The cursor carries no sensitive or subscriber data.
- The cursor token, signature, decoded payload, and signing secret are never logged.

## Consequences

- Keyset traversal is stable and cheap at any depth — it never pays the cost of a large
  `OFFSET` — but Phase 1 gives up count and page-number support and backward navigation.
- Rotating the signing secret invalidates every outstanding cursor; clients transparently
  restart from page one.
- The cursor is readable by clients (base64, not encryption) but is tamper-evident; it cannot
  be used to request data outside the endpoint, sort, and filters it was issued for.
- A traversal is not snapshot-isolated. Section 11's "exactly once" guarantee is scoped to
  records that stay present, stay in the filtered result set, and keep immutable ordering
  values; deletion, a membership-affecting field change, or a draft digest becoming published
  can change what a later page shows. Restarting the traversal only resumes from the current
  page-one state — it is not a frozen snapshot, which would need a future `as_of` boundary or
  a snapshot-isolation design.
- The first paginated endpoint is `/v1/updates`. It still depends on PR 2 — the
  `first_fetched_at` naive-rejection / UTC-normalization / immutability work and the
  ordering-field and `id` immutability enforcement — but not on any *new* field. `/v1/changes`
  additionally needs the new `Change.detected_at` field and is blocked until it lands.
- The codec adds no dependency and stays independently unit-testable because it never imports
  FastAPI and never reads configuration directly.
- Dedicated summary models keep internal fields (`raw_location`, `dedupe_key`, snapshot body
  text, unsupported claims, unpublished digests) out of every list response by construction.
- Filter canonicalization is computed once at the HTTP boundary and reused for both the cursor
  fingerprint and the repository query, so the two cannot disagree; the repository interface
  takes canonical typed filters, not raw request strings.
- The shared-model prerequisites — `first_fetched_at` invariants, the new `Change.detected_at`
  field, `Digest.digest_date` as `date`, ordering-field/`id` immutability across model,
  repository, and storage layers (section 5.D), and the `FactStore.update_fact()` /
  `assemble_digest` / `evaluate` call-site changes — must be implemented in their own PR
  (section 13, PR 2), serialized after Person B's Enum work releases `shared/schemas.py`, and
  must not be folded into the Enum PR.
- Public route integration waits for a real repository boundary; a `NotImplementedError`-only
  adapter is not an acceptable production dependency for shipping an endpoint.

## Non-goals

This ADR does not decide, and must not be read as deciding:

- shared Enum policy or any Enum definition (ADR 0009, Person B);
- the FastAPI application factory, error-envelope handlers, OpenAPI metadata, `operationId`
  conventions, or `/docs` exposure (ADR 0010, Person C);
- database schema, indexes, or migrations;
- the exact Python mechanism for model-level ordering-field immutability, the exact
  storage-level enforcement mechanism (`BEFORE UPDATE` trigger vs. restricted column-level
  `UPDATE` privilege — chosen in the future database schema / migration PR), or the exact
  `intelligence/evaluate.py` empty-digest replacement — each is chosen later within the
  constraints of section 5;
- digest uniqueness, regeneration, correction, or version-history policy — a separate
  digest-lifecycle decision (section 5.B);
- authentication, authorization, or rate limiting;
- any change to `docs/API_CONTRACT.md`, Python code, `pyproject.toml`, `uv.lock`, or
  `.env.example` — those land in the later PRs described in section 13.
