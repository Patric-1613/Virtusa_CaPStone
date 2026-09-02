# 0010 — FastAPI and generated OpenAPI contract authority

Status: Proposed
Date: 2026-09-02

## Context

AI Daily Digest has an agreed modular-monolith direction, a human-readable API and shared-data
contract in `docs/API_CONTRACT.md`, Pydantic shared models, and an accepted UUID v7 policy in ADR
0007. It does not yet have a FastAPI application, an executable OpenAPI document, or a public HTTP
route implementation.

The first Delivery implementation needs to establish the HTTP boundary without prematurely adding
domain endpoints, persistence, pagination, subscriptions, chat, or deployment machinery. It also
needs a clear answer to a contract-authority question: once routes exist, which source defines their
machine-readable shape, and which source defines their human and business meaning?

Maintaining two independent machine-shape specifications would allow them to drift. Treating
generated OpenAPI as the only product contract would lose evidence, privacy, compatibility, and
planned-endpoint context that belongs in human-readable documentation. Fake routes created only to
make planned endpoints appear in OpenAPI would be worse: they would advertise behavior that is not
functional.

This decision is coordinated through issue #32. Person C (`@chamath-wijayasundara`) is the active
author. Persons A (`@Patric-1613`) and B (`@SujinJK`) are the review stewards because this decision
affects the public contract and cross-module integration. ADR 0008 is reserved for pagination and
ADR 0009 is reserved for the shared Enum policy; this ADR does not decide or implement either one.

## Decision

### Contract authority

For every implemented public endpoint, the OpenAPI document generated from the actual FastAPI
application is authoritative for its executable HTTP shape:

- paths and HTTP methods;
- query and path parameters;
- request and response fields;
- required versus optional fields;
- nullability;
- types and formats;
- Enum membership;
- HTTP response schemas;
- operation IDs; and
- component-schema names.

`docs/API_CONTRACT.md` remains authoritative for:

- business meaning and domain semantics;
- evidence, provenance, grounding, and citation rules;
- privacy and security rules;
- compatibility policy;
- pagination semantics;
- complete, human-readable JSON examples; and
- the planned public endpoint catalogue.

An endpoint documented in `docs/API_CONTRACT.md` but not implemented remains a design target. It
does not appear in generated OpenAPI, and Delivery must not register a fake, empty, or placeholder
route merely to advertise it.

Any disagreement between an implemented route's generated OpenAPI and
`docs/API_CONTRACT.md` is a defect. The human documentation and the executable Pydantic/FastAPI
schema must be corrected together in the same implementation PR. Neither source silently
overrides a contradiction in the other.

This ADR does not add or require a manually maintained Planned/Implemented column. Generated
OpenAPI is the implementation record; a second handwritten implementation-status list would drift.

### OpenAPI generation and stability

- OpenAPI is generated from the application at runtime with `app.openapi()`.
- Generated `openapi.json` is not initially committed to the repository.
- Contract tests inspect the object returned by `app.openapi()` directly.
- Repeated generation from equivalent fresh application instances must be deterministic.
- Every route has an explicit, unique, stable `snake_case` `operation_id`; generated function-name
  defaults are not part of the public contract.
- Public metadata must not expose personal email addresses, repository-internal URLs, local paths,
  real deployment hosts, or other environment-specific information.
- A committed OpenAPI snapshot and generated TypeScript client are deferred until the frontend
  generator and generated-artifact policy are selected.
- Once a TypeScript generator is selected, CI regenerates a client from the live application schema
  and compiles or type-checks it to detect drift. Whether generated output is committed remains a
  separate reviewed decision.

### FastAPI application structure

The initial Delivery API uses this minimum structure:

```text
src/ai_daily_digest/delivery/api/
├── __init__.py
├── app.py
├── dependencies.py
├── errors.py
└── routes/
    ├── __init__.py
    └── health.py
```

Rules for that structure:

- `app.py` exposes an application factory named `create_app()`.
- Calling `create_app()` returns a fresh application instance; tests and callers must not depend on
  mutation of one process-global application object.
- Importing an API module starts no server and performs no database, network, model-provider, email,
  or other infrastructure connection.
- Infrastructure enters route handlers through typed FastAPI dependencies and narrow protocols.
- Delivery may import stable public contracts from `ai_daily_digest.shared`.
- Delivery must not import private ingestion or intelligence implementations.
- Do not create `delivery/api/schemas.py` until a real HTTP request model, public projection, or
  response envelope requires it.
- Do not add empty repositories, services, adapters, or other speculative abstractions.
- Pagination code belongs in `delivery/api/pagination.py` when ADR 0008 is accepted and pagination
  implementation begins. Person A coordinates that work under the repository's active-author and
  review-steward workflow; this does not override the rule that any teammate may author code.

### First implementation PR: HTTP foundation only

The first implementation PR after this ADR is accepted contains only:

- the plain `fastapi` production dependency;
- an explicit `httpx` development dependency for FastAPI/Starlette `TestClient`;
- the application factory and safe OpenAPI metadata;
- the standard error envelope, typed API exception, and exception handlers;
- request-ID generation and propagation;
- `GET /v1/health/live`;
- `GET /v1/health/ready`; and
- focused unit and OpenAPI contract tests.

It must not contain:

- database access or migrations;
- domain list or detail endpoints;
- pagination;
- subscriptions or email;
- chat;
- authentication;
- CORS policy;
- inbound rate limiting;
- administrative routes; or
- background-job controls.

CORS and inbound rate limiting are deferred to separately scoped security or feature PRs after
their configuration and public behavior are agreed.

Dependency rules for that PR:

- Add plain `fastapi`, not `fastapi[standard]`.
- Do not directly pin Starlette; use the compatible version resolved through FastAPI.
- Do not add `uvicorn` until the project implements and tests a runnable API process or deployment
  entry point.
- Add `httpx` explicitly to the development group rather than relying on the Anthropic SDK's
  transitive dependency.
- During implementation, inspect current package metadata and resolve the minimum FastAPI version
  compatible with this repository's Python and Pydantic constraints. This ADR deliberately does
  not predict an exact version.
- Regenerate and commit `uv.lock`, explain each new direct dependency, prove the locked non-editable
  install, and run `pip-audit` after resolution.

### Health behavior

`GET /v1/health/live` proves only that the API process can answer an HTTP request. It performs no
dependency checks and returns a small typed response such as:

```json
{"status": "ok"}
```

`GET /v1/health/ready` evaluates configured readiness through an injected, typed readiness
protocol:

- production wiring supplies the configured probe; tests supply a deterministic fake;
- the route does not hard-code database, model-provider, email-provider, or network access;
- it returns `200` when configured required dependencies are ready;
- it returns `503` with the standard error envelope and code `service_unavailable` when they are
  not ready;
- any returned check list contains only safe check names and coarse statuses; and
- it never exposes a hostname, DSN, credential, driver error, raw exception, or connection string.

An unconfigured infrastructure dependency must not be represented by a fake successful check. The
foundation's configuration and tests must make the meaning of ready explicit.

The required-dependency set is explicit configuration. A foundation-only application may configure
that set as empty; in that case, `GET /v1/health/ready` returns `200` because there are no required
external dependencies to check. This result must not be implemented by installing fake successful
probes for dependencies that have not been configured.

Once a domain feature requires infrastructure, application startup or configuration validation
must require a corresponding real readiness probe. Naming a dependency as required without
providing its probe is a configuration error and must prevent normal startup; it must never be
silently ignored so that readiness reports success.

### Request-ID policy

The API generates one request ID at the beginning of every request:

- call `ai_daily_digest.shared.ids.new_id()` once;
- store the resulting UUID in request state;
- use that same value in structured logs;
- use that same value in every error response for the request; and
- never generate a replacement ID independently inside an exception handler.

The internal correlation ID follows ADR 0007 and is a UUID v7. A caller-supplied request-ID header
must not be trusted as the server's internal correlation identifier without a later explicit
policy. This ADR does not decide or promise a response request-ID header; adding one requires its
own explicit, reviewed contract decision rather than an incidental middleware behavior.

### Error contract

All API errors use the existing envelope shape:

```json
{
  "error": {
    "code": "stable_machine_readable_string",
    "message": "safe human-readable message",
    "request_id": "uuid-v7",
    "details": {}
  }
}
```

The foundation and later routes use these mappings unless a later accepted ADR owns a more specific
decision:

| Condition | HTTP status | Code |
|---|---:|---|
| Malformed JSON or request-schema failure | `422` | `validation_error` |
| Malformed UUID | `422` | `validation_error` |
| UUID v4 where UUID v7 is required | `422` | `validation_error` |
| Unknown or unmatched route | `404` | `not_found` |
| Unsupported method for a matched route | `405` | `method_not_allowed` |
| Missing resource | `404` | Endpoint-specific, for example `change_not_found` |
| Readiness failure | `503` | `service_unavailable` |
| Unexpected exception | `500` | `internal_error` |

Framework-generated errors, including unknown-route `404` and unsupported-method `405` responses,
must be intercepted and returned in the same `ErrorEnvelope`; the API must not expose FastAPI or
Starlette's default `detail` response body. The handlers use the request's existing UUID v7 and
preserve safe protocol headers required by the framework, including `Allow` on a `405` response.
Endpoint handlers continue to use endpoint-specific codes for known resources that are not found.

Invalid-cursor status and code are deliberately not decided here. ADR 0008 owns that contract; this
ADR does not predefine `invalid_cursor`.

Error-code values remain an extensible `str`. This ADR does not introduce a closed `ErrorCode` Enum
because endpoint-specific codes expand with implemented behavior. Implemented generic codes may use
constants without closing the envelope field.

Request-validation `details` may contain sanitized `loc`, `type`, and safe message values. They must
strip input values, request-body values, headers, Pydantic `ctx`, exception strings, tracebacks, and
any other content that could echo secrets or personal data. Unexpected exceptions log internal
diagnostic detail with the same request ID but return only a generic public message.

Resource-specific failures use a typed application/API exception that carries an explicit stable
code and safe message. Handlers must not infer endpoint-specific codes from arbitrary
`HTTPException` text or status values.

### Documentation endpoints

- `/openapi.json` remains enabled for the public API.
- `/docs` and `/redoc` are enabled by default for the MVP.
- `create_app()` may accept a configuration value that disables interactive `/docs` and `/redoc`
  for a hardened or internal deployment while retaining the deliberate OpenAPI policy.
- Hiding documentation is not authentication or authorization.
- Administrative and worker routes remain absent from the public API application.

### Reconciliation with ADR 0007

A health-only FastAPI foundation is HTTP infrastructure, not a UUID-backed domain vertical slice.
It generates request IDs under ADR 0007, but health routes do not accept or return a persistent
UUID-backed domain resource. Therefore ADR 0007's four UUID API acceptance gates become mandatory
in the first real UUID-backed domain endpoint PR, not in the health foundation.

- Do not add fake UUID routes merely to make the four gates run early.
- Every later UUID-backed route joins the same OpenAPI and HTTP contract-test harness.
- When UUID-route contract tests are activated, the harness asserts that at least one UUID-backed
  route was actually inspected; an empty loop or vacuous discovery test must fail.
- The cross-endpoint consistency suite expands as real detail, list, chat, and other UUID-backed
  routes are implemented.

This ADR supplements and clarifies ADR 0007's implementation sequence. This initial ADR PR does not
rewrite the accepted ADR 0007. Any later cross-reference added to ADR 0007 is a separate reviewed
documentation change.

### First domain vertical slice

After the health foundation, the recommended first domain PR is:

```text
GET /v1/changes/{change_id}
```

This endpoint requires no pagination, can return the existing evidence-bearing `ChangeSet`
contract, and exercises UUID v7 request validation and response serialization. The route receives
an individual `Change.id` and returns the containing `ChangeSet`, so its repository protocol method
is named `get_by_change_id(change_id)` rather than the ambiguous `get_by_id()`.

Requirements for that separate PR:

- the path parameter is a `Uuid7Id`;
- the route declares an explicit response model and stable operation ID;
- persistence access is behind a typed repository protocol;
- tests use an in-memory fake;
- no Postgres adapter exists merely to raise `NotImplementedError`;
- no public route merges without a functional configured data source;
- if persistence is unavailable, either configure an honest Phase-1 adapter or defer the route;
- all four ADR 0007 UUID acceptance gates pass; and
- a missing record returns a safe `404` error envelope with the agreed endpoint-specific code.

The foundation PR must not implement this route.

### Shared and Delivery schemas

Shared models may be reused directly as response models only when their entire shape is safe and
appropriate for the public API. Likely candidates, subject to implementation review, are:

- `Digest`;
- `DigestClaim`;
- `Subject`;
- `Change`; and
- `ChangeSet`.

Do not automatically expose:

- `DocumentSnapshot`, because `raw_location` is an internal storage reference; or
- `SourceItem`, until the team explicitly decides whether `dedupe_key` belongs in public output.

Create Delivery projections for public views that omit internal fields. HTTP-only types belong
under `delivery/api/`, including error envelopes, health responses, request bodies, query
parameters, presentation projections, and pagination envelopes after ADR 0008. Do not place
HTTP-only types in `shared`.

ADR 0009 is merged on current `main` with `Status: Proposed`. Delivery imports and reuses its
shared Enums only after ADR 0009 is formally accepted and its implementation is merged; Delivery
must not redefine equivalent HTTP-local Enums.

### API metadata and compatibility

The application uses:

- title `AI Daily Digest API`;
- `/v1` URL versioning;
- an explicit API metadata version changed intentionally and not derived automatically from the
  Python package version;
- OpenAPI 3.1 as emitted by the selected compatible FastAPI version;
- stable explicit operation IDs;
- named, stable component schemas; and
- no personal contact email, internal repository URL, local path, or real server hostname.

Compatibility rules:

- adding a new optional response field is normally additive;
- adding a new endpoint is additive;
- removing or renaming a field is breaking;
- changing a field type is breaking;
- adding a new required request field is breaking;
- adding an Enum response member may break exhaustive generated clients and requires compatibility
  review; and
- breaking public changes require `/v2` or an explicit, documented migration window.

### Parallel-work boundaries and order

The three coordinated ADR PRs initially add only their own ADR file:

- Person A: ADR 0008, pagination;
- Person B: ADR 0009, shared Enum policy; and
- Person C: ADR 0010, FastAPI/OpenAPI authority.

Initial implementation coordination:

- Person C: `delivery/api/app.py`, `delivery/api/dependencies.py`,
  `delivery/api/errors.py`, `delivery/api/routes/health.py`, OpenAPI foundation tests, and the
  FastAPI/httpx dependency change.
- Person A: `delivery/api/pagination.py`, cursor-codec tests, pagination contract sections, and the
  first list-endpoint pagination integration, coordinated with Person C.
- Person B: shared Enum implementation and the affected intelligence call sites and tests after ADR
  0009 is accepted.

Only one active author edits any of these integration files at a time:

- `docs/API_CONTRACT.md`;
- `src/ai_daily_digest/shared/schemas.py`;
- `src/ai_daily_digest/delivery/api/app.py`;
- `pyproject.toml`; and
- `uv.lock`.

Recommended order:

1. ADRs 0008, 0009, and 0010 may be reviewed and merged independently.
2. The FastAPI health foundation may merge without waiting for Enum or pagination implementation.
3. The Enum implementation rebases onto current `main` as necessary and may merge independently
   once its CI passes.
4. Person A implements the cursor codec without changing `app.py`.
5. Person C's first functional domain endpoint merges.
6. Person A rebases and integrates the first paginated list endpoint.
7. Every PR that changes `docs/API_CONTRACT.md` or `delivery/api/app.py` rebases on current `main`
   and reruns `make ci`.

These assignments record initial active authorship and coordination, not exclusive permission to
edit a module. They remain subject to the repository's current flexible-authorship workflow.

### Required implementation tests

The health-foundation PR records and passes tests proving:

- `create_app()` returns independent FastAPI instances;
- importing API modules performs no network, database, provider, or server side effect;
- `app.openapi()` succeeds;
- repeated OpenAPI generation is deterministic;
- operation IDs are explicit and unique;
- liveness returns the documented typed response;
- readiness returns `200` when the explicitly configured required-dependency set is empty;
- readiness success and failure use injected fakes;
- startup or configuration validation rejects a required dependency that has no real probe;
- error responses validate against `ErrorEnvelope`;
- an unknown path returns a `404` `ErrorEnvelope` with code `not_found`, not a default `detail`
  body;
- an unsupported method returns a `405` `ErrorEnvelope` with code `method_not_allowed` and
  preserves the `Allow` header;
- request-validation details contain no input, body, header, or Pydantic `ctx` values;
- unexpected exceptions expose no secrets, exception strings, or tracebacks;
- error responses and structured logs use the same request ID;
- request IDs are UUID v7;
- documentation endpoints follow the configuration flag; and
- no domain, administrative, or worker endpoint is accidentally registered.

Tests must not parse `docs/API_CONTRACT.md` to maintain a duplicate implementation-status list.
They assert the foundation's exact expected paths and treat `app.openapi()` as the executable record
of implemented routes.

The first UUID-backed endpoint PR additionally proves:

- a valid UUID v7 path value is accepted;
- malformed UUID and UUID v4 path values are rejected;
- response UUIDs are canonical lowercase hyphenated strings;
- Pydantic validation and serialization schemas are both inspected;
- `app.openapi()` exposes the UUID format actually emitted rather than an assumed value;
- a missing record returns a safe `404` envelope;
- no internal-only field appears in the response;
- the UUID harness fails unless at least one real UUID-backed route was inspected; and
- later UUID-backed endpoints automatically join the cross-endpoint consistency suite.

### Implementation PR boundaries

The work is split into narrowly reviewable PRs:

1. **ADR 0010 only** — this new ADR file; no API contract, Python, dependency, lockfile, fixture, or
   test change.
2. **FastAPI/OpenAPI health foundation** — `pyproject.toml`, `uv.lock`, the minimum
   `delivery/api/` structure, `tests/unit/delivery/`, and `tests/contract/test_openapi.py`; no domain
   endpoint or pagination.
3. **First functional UUID-backed detail endpoint** — one route, a functional configured repository
   adapter, HTTP boundary tests, ADR 0007 gates, and a coordinated `docs/API_CONTRACT.md` update.
4. **Pagination/list integration** — after ADR 0008; begin with one list endpoint and Person A's
   cursor codec rather than implementing every list route at once.
5. **Generated-client CI** — only after generator selection; regenerate and compile/type-check, and
   do not commit generated output unless separately approved.

## Consequences

- The generated contract cannot claim unimplemented routes, and implemented route shapes are
  continuously checked from executable code.
- Human documentation retains domain meaning, safety rules, planned scope, and full examples that
  OpenAPI cannot express adequately.
- Explicit operation and component names make future client generation more stable, but renaming
  them becomes a compatibility concern.
- The first HTTP PR remains small enough to test middleware, errors, health behavior, and OpenAPI
  before persistence complicates the boundary.
- Deferring a generated snapshot/client avoids committing an unchosen tool's output, but CI cannot
  prove TypeScript-client compatibility until the generator decision is made.
- Readiness remains honest and safe only if each configured dependency supplies a real probe and
  sanitizes failures.
- Shared models require deliberate exposure review; Delivery may need small projection models to
  prevent internal fields from reaching public responses.
- Parallel implementation requires active coordination on the five shared integration files named
  above.

## References

- Issue #32 — ADR 0010 coordination and scope.
- `docs/ARCHITECTURE.md` — modular-monolith and Delivery boundaries.
- `docs/API_CONTRACT.md` — human-readable public API and shared-data contract.
- ADR 0001 — modular monolith.
- ADR 0003 — quality gates.
- ADR 0006 — disclosure status and evidence semantics.
- ADR 0007 — UUID v7 policy and future HTTP acceptance gates.
- ADR 0008 — pagination policy (reserved; separate decision).
- ADR 0009 — shared Enum policy (merged with `Status: Proposed`; decided separately).
