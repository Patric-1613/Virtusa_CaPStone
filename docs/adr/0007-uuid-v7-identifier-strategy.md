# 0007 — Project-wide UUID v7 identifier policy

Status: Proposed for peer review — UUID v7 selected by Person A on 2026-09-01
Date: 2026-09-01

## Status detail

Person A has selected UUID v7 as the identifier strategy and drafted this ADR. **This is not yet a
team decision.** Per `docs/adr/README.md` and this repository's own contract-change process
(`docs/API_CONTRACT.md`'s "Contract-change process"), a shared-contract decision needs review from
at least one other module owner before it is Accepted — this ADR has not received that review yet.

- **Person B** must review and approve this ADR before it can move to Accepted.
- **Person C's confirmation remains required before any delivery/API implementation depends on this
  decision** — the same deferred-but-not-skipped posture already established for ADR 0004 and ADR
  0005 (see their own "pending Person C's confirmation" status lines), since UUID formatting is a
  cross-cutting concern that will directly shape the delivery module's request/response handling.
- Once Person B approves, the status line changes to
  `Accepted by Persons A and B; Person C confirmation pending`, in a commit that itself requires a
  fresh approval — a status-changing commit is not exempt from review just because the surrounding
  text already went through it once; this repository's branch protection dismisses stale approvals
  on new pushes for exactly this reason.

**This ADR is documentation-only and touches no `docs/API_CONTRACT.md` content in this PR.**
`docs/API_CONTRACT.md` still states UUID v4, unchanged, on this branch. This is deliberate, not an
oversight: this repository's own contract-change process requires `docs/API_CONTRACT.md` and the
affected Pydantic/OpenAPI schemas to change **in the same PR**. Since this branch carries no Python
implementation (`shared/ids.py`, `shared/schemas.py` unchanged), updating the live contract now
would create a known, immediate mismatch between what the contract promises and what the code
actually does. The contract update — together with every example migrated to UUID v7, `shared/ids.py`,
`shared/schemas.py`, fixtures, contract tests, and the dependency change — is deferred as one unit
to the implementation PR (see "Implementation PR scope" below), not split across two PRs that would
each individually violate the contract-change process on their own.

## Context

- The application generates many traceable, time-oriented records — `SourceItem`,
  `DocumentSnapshot`, `ExtractedFact`, `Change`, `ChangeSet`, `Digest`, `DigestClaim` — across
  `ingestion`, `intelligence`, and `delivery`.
- The current implementation (`shared/ids.py::new_id()`) generates UUID v4 (`uuid.uuid4()`) only,
  and every `id`-shaped field in `shared/schemas.py` is typed as a plain `str` with a `# UUID v4`
  comment — no runtime format validation exists at the model boundary today.
- **Python 3.12 has no standard-library UUID v7 generator.** UUID v6/v7/v8 generation
  (`uuid.uuid7()` etc.) was added to CPython's `uuid` module for RFC 9562 in Python 3.14, not 3.12
  (this project's pinned interpreter, per `pyproject.toml`'s `requires-python = ">=3.12,<3.13"`).
  UUID v7 therefore requires a vetted third-party dependency for generation on this project's
  Python version — evaluated below, not hand-rolled (per the team's explicit instruction: no
  hand-written UUID v7 generator, vetted-dependency only).
- **No persistent production data exists yet** — there is no database, no migrations, and the only
  UUID-shaped values in the repository are illustrative examples in `docs/API_CONTRACT.md` and
  hand-crafted, v4-shaped placeholder IDs in `tests/fixtures/contracts/*.json`. This is the safest
  possible point to establish the identifier policy: there is no historical data to migrate, and no
  external consumer has ever seen a v4-formatted ID from this system.

## Dependency evaluation

Evaluated against Python 3.12 compatibility, RFC 9562 compliance, maintenance activity, licence,
dependency tree, serialization behaviour, and Pydantic compatibility, using primary sources
(PyPI project pages, GitHub repositories, release histories) as of 2026-09-01.

| Candidate | Python 3.12 | RFC 9562 | Maintenance | Licence | Dependencies | Recommendation |
|---|---|---|---|---|---|---|
| **`uuid-utils`** (aminalaee) | Prebuilt wheels for cp312 across manylinux/musllinux/macOS/Windows, all architectures — matches this project's `ubuntu-latest` CI runner exactly | Wraps the mature Rust `uuid` crate's `uuid7()` implementation; a `.compat` submodule returns genuine stdlib `uuid.UUID` instances, not a custom subclass | **Active**: v0.17.0 released 2026-07-09 (~1 month before this decision), 368 GitHub stars, BSD-3-Clause | BSD-3-Clause | Zero runtime Python dependencies (compiled Rust extension) | **Selected** |
| `uuid6` (oittaa) | Pure Python, no compile step, works anywhere | README explicitly states it implements "the proposed IETF RFC 9562" (direct quote) | **Stale**: latest release 2025.0.1 (2025-07-04) — no release in over a year as of this decision; single maintainer; PyPI classifier still "Beta" | MIT | None (pure Python) | Documented fallback — see below |
| `uuid-backport` (line1029) | Python ≥3.9, backport of CPython 3.14's own `uuid` module | Framed as a faithful backport of RFC 9562's reference implementation | **Immature**: 3 GitHub stars, 8 commits total, first release 2025-11-21 (~9 months old, single maintainer) | MIT / PSF dual | Unconfirmed | Rejected — track record too thin for a shared/public-model dependency |

**Selected: `uuid-utils`.** `uuid6` is recorded here as the evaluated fallback, for visibility of
the trade-off considered ("zero compiled dependency" vs. "larger, more recently active
maintenance") — **it is not an implementation-time option the author may pick freely.** Switching
the selected dependency from `uuid-utils` to `uuid6` (or to anything else) after this ADR is
Accepted requires, before any code is merged: (1) an amendment to this ADR recording the switch and
the reason for it, (2) written justification for why `uuid-utils` — the actually-selected
dependency — turned out to be unworkable, and (3) peer review of that amendment, under the same
contract-change process as the original decision. An implementation PR that silently substitutes
`uuid6` for `uuid-utils` without that amendment is not implementing this ADR; it is making an
undocumented, unreviewed dependency decision.

Verified facts, kept distinct from inference:

- **Verified**: Python 3.12 has no native `uuid7()` (CPython adds it in 3.14, tracked as
  cpython#89083). PostgreSQL ≤17 has no native `uuidv7()`; PostgreSQL 18 adds one — irrelevant to
  this decision since generation is application-side (see Decision), not database-side.
- **Verified**: Pydantic ships a built-in `pydantic.UUID7` type
  (`Annotated[UUID, UuidVersion(7)]`, validation-only — it does not generate) via
  `pydantic/pydantic#11436`, merged 2025-02-25, released in **Pydantic 2.11.0** (2025-03-27). This
  project's `pyproject.toml` currently pins `pydantic>=2.6` — below that floor — even though the
  *locked* version (`uv.lock`, `pydantic==2.13.4`) already satisfies it. **The implementation PR
  must raise the declared floor from `>=2.6` to `>=2.11`** so the requirement is enforced by the
  dependency constraint itself, not left as an accident of whatever Dependabot has resolved.
- **Inference, not a verified direct quote**: `uuid-utils`'s own README wasn't confirmed (in this
  pass) to state RFC 9562 compliance in those exact words. The implementation PR's validation step
  (parse each generated value, assert `.version == 7` and correct RFC 9562 variant bits) closes this
  gap empirically and must not skip it on the assumption the library's documentation is sufficient.

## Decision

- **All generated UUIDs use RFC 9562 UUID v7.** No new UUID v4 generation is introduced anywhere
  in the codebase going forward.
- **Application-side generation through one central shared factory**: `shared/ids.py` remains the
  only place a UUID is generated. **Only `shared/ids.py` may import the selected UUID dependency
  directly** — `ingestion`, `intelligence`, and `delivery` call `shared.ids.new_id()` (or its
  renamed v7 equivalent), never the third-party library. This is already the de facto shape of the
  codebase (verified: `new_id()` is the only UUID-generation call site today) and this ADR makes it
  an explicit, enforced rule rather than an accident of how the code happens to be organized.
- **PostgreSQL native `uuid` column storage** for every UUID-typed field, once tables exist —
  version-agnostic at the column-type level, so no future version change requires a column
  migration.
- **Canonical lowercase hyphenated string serialization in JSON** (`8-4-4-4-12`, lowercase hex) —
  the format every current `docs/API_CONTRACT.md` example already uses and Pydantic's default `UUID`
  serialization already produces.
- **Runtime validation at shared/public model boundaries**, using Pydantic's built-in `UUID7` type
  (see dependency evaluation above) once the implementation PR rewires `shared/schemas.py`'s `id`
  fields from plain `str` to `UUID7`. Not implemented in this PR.
- **API clients treat every ID as opaque**: no parsing, no construction, no sorting by ID — this
  applies identically to persistent resource IDs, request/correlation IDs, and persisted chat
  session IDs, since all three now share the same UUID v7 policy.
- **Code must not order records by UUID.** Explicit timestamp fields (`fetched_at`, `observed_at`,
  `digest_date`, and any future `detected_at`) remain the sole authority for business ordering and
  pagination, exactly as already stated in `docs/API_CONTRACT.md`'s pagination rule.
- **Persistent resource IDs, request/correlation IDs, and persisted chat session IDs all use UUID
  v7** — this decision deliberately does not split these into different ID strategies; there is
  one identifier policy, not several.
- **Foreign keys referencing UUID resources use UUID v7** — a foreign key column stores the same
  v7-formatted value as the row it references; no translation layer.
- **Source registry slugs are not converted to UUIDs.** `sources.yaml` entries such as
  `openai_news` remain human-readable string slugs — they identify configuration, not a persisted,
  traceable resource row, and converting them would make `sources.yaml` itself harder to read and
  review for no benefit this policy is trying to deliver.
- **Content hashes and deduplication keys remain hashes**, not UUIDs — `dedupe_key`
  (`sha256:...`) and `content_hash` (`sha256:...`) encode content identity, a fundamentally
  different property than resource identity, and must not be conflated with it.
- **Security tokens are signed, cryptographically random values — never UUIDs.** Subscription
  confirmation and unsubscribe tokens are single-purpose, signed, cryptographically random values
  (not generated by `shared/ids.py`, not passed through the UUID factory at all). A UUID — v4 or v7
  — is designed for uniqueness, not unguessability, and v7 additionally embeds a coarse creation
  timestamp; neither property belongs anywhere near a token whose entire job is resisting guessing.
  Raw tokens are never stored or logged — only their hashes, purpose, expiry, and use/revocation
  time (already stated in `docs/API_CONTRACT.md`'s subscription contract; this ADR does not change
  that, it reaffirms it applies regardless of the UUID decision).

## Consequences

- **Approximate generation time is encoded in UUID v7, and this is an accepted consequence.** Every
  v7 ID leaks a coarse (millisecond) creation timestamp to anyone who can decode it. For source
  items, snapshots, facts, changes, and digests — all already-public, already-timestamped data —
  this discloses nothing beyond what the record's own explicit timestamp fields already state. The
  one case worth naming explicitly: if a future `Subscription` resource follows this same policy,
  its ID would embed an approximate signup time. This is accepted as low-risk specifically because
  confirmation/unsubscribe flows use separate signed tokens, never the subscription row's own ID, as
  the externally-exposed value.
- **Existing test placeholders and fixtures will need migration** — `tests/fixtures/contracts/*.json`
  contains 33 hand-crafted, UUID-v4-*shaped* IDs across its five files, and unit tests use ~190
  non-UUID placeholder strings (e.g. `id="f1"`, `"snap_1"`) that pass today only because no format
  validation exists yet. Both need attention once the implementation PR adds `UUID7` typing — sized
  and flagged here so it lands as planned scope, not a surprise. Not touched in this PR.
- **Public examples must use valid UUID v7 values once `docs/API_CONTRACT.md` is updated.** That
  update is explicitly deferred to the implementation PR (see "Implementation PR scope" below), not
  made here — `docs/API_CONTRACT.md` is unchanged on this branch and still states UUID v4. When that
  update happens, every replacement example must be generated and self-validated (parsed via
  Python's `uuid.UUID`, `.version == 7`, correct RFC 9562 variant bits, canonical lowercase
  hyphenated form) before being written into the document.
- **A future switch away from UUID v7, once this ADR is Accepted, requires a superseding ADR** —
  reopening an accepted decision needs the same weight of process that established it, not a quiet
  reversion in an unrelated PR. Until acceptance, this ADR itself remains open to revision through
  ordinary review, including from Person B and Person C.
- **UUIDs are identifiers, never authentication credentials.** Nothing in this ADR authorizes using
  a UUID — v7 or otherwise — as a bearer token, API key, or any other credential-shaped value; that
  boundary belongs entirely to the separate signed-token mechanism described above.
- **Dependency updates require Dependabot/security review**, per `AGENTS.md`'s existing rule —
  `uuid-utils` joins the same Dependabot-monitored dependency set as `pydantic`, `anthropic`, and
  `langgraph` already are. Switching to `uuid6` is not a routine dependency update and is not
  covered by this bullet — see the Dependency evaluation section's amendment requirement above.

## Validation expectations for the later implementation

Not performed in this PR — recorded here so the implementation PR has a concrete, pre-agreed bar,
not a re-litigated one. Note the deliberately narrow scope of the rejection requirements below:
Pydantic's built-in `UUID7` type (`Annotated[UUID, UuidVersion(7)]`) validates the **version**
of an already-well-formed UUID; it is a thin wrapper around Python's `uuid.UUID` parser, which
accepts several parseable-but-non-canonical input forms (e.g. uppercase hex, a leading `urn:uuid:`
prefix, or hyphens omitted) as the *same* underlying value. `UUID7` alone does not reject those
forms — only a genuinely malformed value (wrong length, non-hex characters, wrong structure) fails
to parse at all. The bar below reflects that distinction; it must not be read as a promise that
`UUID7` alone enforces canonical *input* formatting:

- A generated UUID parses successfully with Python's `uuid.UUID`.
- `uuid.version == 7` for every generated value; a UUID of any other version (v4 included) is
  rejected wherever `UUID7`-typed validation is in effect.
- The RFC 9562 variant bits are correct (the two high bits of byte 8 read `10`).
- Malformed values — wrong length, non-hex characters, structurally invalid — are rejected at the
  model boundary. This is what `UUID7` provides out of the box, together with the version check.
- **Output is always canonical lowercase hyphenated serialization** (`8-4-4-4-12`, lowercase hex) —
  this is Pydantic's default `UUID` JSON serialization behavior and must be verified, not assumed.
- **Not promised without further work**: that every parseable-but-differently-hyphenated or
  differently-cased *input* is rejected. If the team wants stricter input-form rejection than
  `UUID7` provides by default, the implementation PR must add and test an explicit custom validator
  for it — this ADR does not assume that behavior exists just because `UUID7` is in use.
- Source registry slugs (`sources.yaml`'s `id` values, e.g. `openai_news`) remain valid, unaffected
  plain strings — this policy must not accidentally sweep them in.
- No `01K...` or UUID v4 example remains anywhere in public documentation, once
  `docs/API_CONTRACT.md` is updated in the implementation PR.
- Security-token code does not import or call the UUID factory, `shared/ids.py`, or the
  third-party UUID dependency at all.

## Implementation PR scope (deferred, not built here)

Out of scope for this documentation-only PR, and — per this repository's contract-change process —
made together as **one** PR, not split further, so `docs/API_CONTRACT.md` never states a contract
the code doesn't yet implement:

- Update `docs/API_CONTRACT.md`: replace every UUID v4 statement and example, and both `"01K..."`
  placeholders, with valid, self-validated UUID v7 examples (canonical lowercase hyphenated form),
  consistent with this ADR's Decision section.
- Add `uuid-utils` to `pyproject.toml` production dependencies; regenerate `uv.lock`.
- Raise the declared Pydantic floor from `pydantic>=2.6` to `pydantic>=2.11` in `pyproject.toml`.
- Rewrite `shared/ids.py::new_id()` to generate UUID v7 via `uuid-utils`, as the sole import site
  for the dependency. If the implementation author believes `uuid6` should be used instead, that is
  not a decision this PR can make on its own — see the Dependency evaluation section's amendment
  requirement.
- Retype every `id`-shaped field in `shared/schemas.py` from `str` to `pydantic.UUID7` (or the
  correct type for a foreign-key-style reference field), per "All affected shared/public fields" in
  the Phase 1 evidence report this ADR follows from.
- Migrate `tests/fixtures/contracts/*.json`'s 33 v4-shaped placeholder IDs to valid v7 values.
- Update the ~190 non-UUID placeholder IDs in unit tests that will fail once `UUID7` typing is
  enforced at the model boundary.
- Add the validation-expectations tests listed above, including an explicit test that a UUID v4
  value is rejected by `UUID7`-typed fields, and — if the team decides stricter input-form
  rejection is needed beyond what `UUID7` provides by default — the custom validator and its tests.

This PR requires this ADR to already be Accepted (with Person B's approval; Person C's confirmation
may remain pending per the Status detail above, but must land before delivery/API code depends on
it) — it must not proceed while this ADR is still Proposed.

## Explicitly out of scope

- Designing or implementing subscription confirmation and unsubscribe tokens.
- Security tokens require a separate delivery/security decision and separate implementation PR.
- That future mechanism must use cryptographically random signed values and must not call the UUID
  generator.

## References

- [RFC 9562 — Universally Unique IDentifiers (UUIDs)](https://datatracker.ietf.org/doc/rfc9562/)
- [ADR 0004](0004-extracted-fact-keeps-evidence.md) — precedent for additive, model-level-enforced
  shared-contract changes
- [ADR 0005](0005-structured-comparison-and-snapshot-resolution.md) — precedent for phased,
  explicitly-scoped shared-contract decisions
- `docs/API_CONTRACT.md` — General API rules, and every resource contract example
