# 0007 — Project-wide UUID v7 identifier policy

Status: Accepted by Persons A and B; Person C confirmation pending
Date: 2026-09-01

## Status detail

Person A selected UUID v7 as the identifier strategy and drafted this ADR. Person B reviewed the
design and accepted it on PR #16 on 2026-09-01.

- **Person C's confirmation remains pending.** Delivery/API implementation must not depend on this
  ADR until Person C confirms it — the same deferred-but-not-skipped posture already established
  for ADR 0004 and ADR 0005 (see their own "pending Person C's confirmation" status lines), since
  UUID formatting is a cross-cutting concern that will directly shape the delivery module's
  request/response handling. See "Amendment PR workflow — Person C acceptance sequence" below for
  the exact steps this confirmation follows.

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

**The implementation PR must prove the dependency actually works in this project, not merely that
it happens to already be resolvable.** `uuid-utils` v0.17.0 being present today as a transitive
dependency of `langgraph`'s own stack (via `langchain-core` → `langsmith`) is corroborating evidence
— it shows the version is real, published, and already coexists with this project's other
dependencies — but it is not proof that adding it as a *direct* dependency of `ai-daily-digest`
itself resolves and installs cleanly. The implementation PR must demonstrate, and report in its PR
description:

- **Local implementation validation**: `uv lock` succeeds after adding `uuid-utils` as a direct
  dependency, followed by `uv sync --locked --no-editable` succeeding on Python 3.12. Plain
  `uv sync --locked` does not prove this — this project's own build (`pyproject.toml`'s
  `[tool.hatch.build.targets.wheel]`, the Makefile's `UV_RUN := uv run --no-editable`) is verified as
  a non-editable wheel install specifically, so the proof must use `--no-editable` too, not editable
  mode.
- **CI lock validation**: `uv lock --check` (or the supported equivalent for this `uv` version)
  confirms the committed `uv.lock` is not stale relative to `pyproject.toml`, followed by the same
  locked, non-editable installation CI already performs.
- CI must exercise `import uuid_utils.compat`, generation returning a genuine standard-library
  `uuid.UUID` instance, and the `.version == 7`/correct RFC 9562 variant-bit checks **through the
  normal test suite** — not as a separate one-off script — so this evidence is continuously
  re-verified on every future change, not proven once and then trusted indefinitely.
- `uv build`, `pip-audit`, and the full `make ci` gate set pass with the new dependency in place.

**Deployment-platform validation remains a separate, later requirement.** Once a production
deployment/container platform is selected, that environment must independently prove the same
install/import/generation chain before this ADR's guarantees are treated as extending to it. If that
environment cannot consume `uuid-utils`, the resolution is the amendment process already defined
above (amendment + written justification + peer review) — never a silent runtime substitution to
`uuid6` or anything else.

## Decision

- **All generated UUIDs use RFC 9562 UUID v7.** No new UUID v4 generation is introduced anywhere
  in the codebase going forward.
- **Application-side generation through one central shared factory**: `shared/ids.py` remains the
  only place a UUID is generated, and `shared.ids.new_id()` remains its stable public name —
  consumers never depend on the UUID version being encoded in the function name. **Only
  `shared/ids.py` may import the selected UUID dependency directly** — `ingestion`, `intelligence`,
  and `delivery` call `shared.ids.new_id()`, never the third-party library. This is already the de
  facto shape of the codebase (verified: `new_id()` is the only UUID-generation call site today) and
  this ADR makes it an explicit, enforced rule rather than an accident of how the code happens to be
  organized. `new_id()` returns a standard-library `uuid.UUID`, generated via
  `uuid_utils.compat.uuid7()` — verified empirically, not merely inferred from documentation: this
  returns a genuine `uuid.UUID` instance (`isinstance(uuid_utils.compat.uuid7(), uuid.UUID)` is
  `True`), not `uuid_utils`'s own Rust-backed type, with `.version == 7` and RFC 9562-correct variant
  bits.
- **PostgreSQL native `uuid` column storage** for every UUID-typed field, once tables exist —
  version-agnostic at the column-type level, so no future version change requires a column
  migration.
- **Canonical lowercase hyphenated string serialization in JSON** (`8-4-4-4-12`, lowercase hex) —
  the format every current `docs/API_CONTRACT.md` example already uses and Pydantic's default `UUID`
  serialization already produces.
- **Runtime validation at shared/public model boundaries**, using Pydantic's built-in `UUID7` type
  through one central alias — `Uuid7Id: TypeAlias = pydantic.UUID7`, defined once in `shared/ids.py`
  next to the generator and imported by `shared/schemas.py` — rather than `pydantic.UUID7`
  re-imported ad hoc at each field. This is a direct re-export of Pydantic's own type, not a
  separately reconstructed validator; do not substitute a different validation approach (e.g. a
  custom `Annotated[str, AfterValidator(...)]` type) without amending this ADR first. Once the
  implementation PR rewires `shared/schemas.py`'s `id` fields from plain `str` to `Uuid7Id`, every
  such field's Python attribute is a real `uuid.UUID`, matching `new_id()`'s return type — one
  explicit representation end to end, not coercion at scattered call sites. Not implemented in this
  PR.
- **API clients treat every ID as opaque**: no parsing, no construction, no sorting by ID — this
  applies identically to persistent resource IDs, request/correlation IDs, and persisted chat
  session IDs, since all three now share the same UUID v7 policy.
- **Business chronology comes from the resource's explicit timestamp field alone** (`fetched_at`,
  `observed_at`, `digest_date`, and any future `detected_at`). A timestamp is not a total order —
  multiple records can share one, especially at millisecond precision — so a stable keyset ordering
  additionally uses the record's UUID **only as an opaque secondary tie-breaker**:
  `(business_timestamp, id)`. The UUID's role here is strictly as an already-unique tie-breaker
  value, chosen because every resource already has one — never because of anything encoded inside
  it. Code must never decode UUID v7's embedded timestamp and must never treat UUID ordering as
  business chronology on its own; a UUID's sort position among values sharing the same business
  timestamp carries no business meaning. `docs/API_CONTRACT.md` does not yet define this ordering
  tuple — correcting this ADR's own prior claim that it did. The full, endpoint-specific
  specification (`/v1/updates`, `/v1/changes`, `/v1/digests` — filters, exact sort keys, cursor
  encoding) remains scoped to a dedicated pagination ADR; this ADR states only the general rule that
  ADR must build on.
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

## Batch-scoped ChangeSet ID allocation

`intelligence/facts.py::FactStore.update_fact()` is the only production construction site of
`Change`. Retyping `Change.change_set_id` to `Uuid7Id` means the pre-existing placeholder pattern —
`Change(change_set_id="", ...)`, backfilled later by `intelligence/change_sets.py::build_change_sets()`
via `model_copy` — can no longer construct successfully. This section records the design that
replaces it, so the implementation PR builds a pre-agreed shape rather than inventing one under
review pressure.

**`Change` is never constructed with a placeholder or temporary ID — and no UUID is generated for
an observation that will not become a real `Change`.** `FactStore.update_fact()` receives a required
lazy callback, `change_set_id_factory: Callable[[], uuid.UUID]`, instead of a plain value:

- It does **not** call the callback for a first observation, an unchanged/equivalent value, or
  processing that fails before a `Change` would be constructed — in every one of those paths,
  `update_fact()` already returns `None` (or raises) without ever building a `Change`, so no ID
  should be spent either.
- It calls the callback exactly once, immediately before constructing a real `Change` — the only
  path where a `Change` is actually returned.

The callback closes over a batch-scoped allocator, `dict[Subject, uuid.UUID]`, owned by
`daily_run.py::run_daily()`'s existing `_BatchAccumulator` — already batch-scoped, already rebuilt
fresh per run, unlike `FactStore` which persists across runs by design (its own docstring: the
caller "threads the same objects into tomorrow's run so history... carr[ies] over"). `FactStore`
must never own this mapping itself — a lazily-cached change-set ID living inside `FactStore` would
silently reuse a previous run's ID for a recurring subject, since `FactStore` deliberately outlives
one batch. The callback is built via an explicit get-or-create helper, **not**
`dict.setdefault(subject, new_id())` — that expression evaluates `new_id()` unconditionally on
every call before `setdefault` ever runs, generating and discarding a fresh UUID even when the
subject already has one:

```python
# intelligence/change_sets.py
def get_or_create_change_set_id(
    change_set_ids: dict[Subject, uuid.UUID], subject: Subject
) -> uuid.UUID:
    """Batch-scoped get-or-create. Deliberately NOT
    change_set_ids.setdefault(subject, new_id()) -- Python evaluates every
    argument before setdefault() runs, so that expression calls new_id()
    unconditionally on every invocation, discarding a freshly generated
    UUID even when `subject` already has one."""
    existing = change_set_ids.get(subject)
    if existing is not None:
        return existing
    allocated = new_id()
    change_set_ids[subject] = allocated
    return allocated
```

`graph.py`'s `compare` node builds the closure — `lambda: get_or_create_change_set_id(change_set_ids, subject)`
— and passes it to `update_fact(..., change_set_id_factory=...)`. `change_sets.py::build_change_sets()`
simplifies rather than grows: every `Change` it receives already carries its final, correct
`change_set_id`, so it groups by subject and uses the value already present instead of minting one
and backfilling via `model_copy`.

**`build_change_sets()` must verify that every `Change` grouped under one subject carries the same
`change_set_id`.** This is now a construction-time invariant (every `Change` for a subject in one
batch is built with the same allocator value), and `build_change_sets()` must check it rather than
trust it silently — inconsistent input must raise a specific `ValueError` or domain invariant error
before returning a misleading `ChangeSet`; it must never silently select the first or last ID among
a group that disagrees.

**Regression tests required** (`tests/unit/test_facts.py`, `tests/unit/test_change_sets.py`,
`tests/unit/test_daily_run.py` unless noted), asserting on **equality and generator call counts** —
never on object identity (`is`), which is not part of the public contract:

1. No ID is generated for a first observation, an unchanged/equivalent value, or a failed-processing
   path — assert the factory callback is never invoked (call count of zero) on those paths.
2. Two real `Change`s for the same subject in one batch have `change_set_id` values that compare
   equal to each other.
3. The underlying UUID generator (`new_id()`, or the factory callback wrapping it) is called exactly
   once for that subject in that batch — proven by call count, not by re-deriving the value and
   checking it "looks right."
4. Two distinct subjects in the same batch receive `change_set_id` values that compare unequal.
5. A second, later `run_daily()` call (a fresh `_BatchAccumulator`, fresh allocator) produces a
   `change_set_id` for the same subject that compares unequal to the first run's value.
6. No empty string, sentinel, or other temporary value ever reaches a constructed `Change` —
   `change_set_id` is always a real, parseable UUID v7 immediately upon construction.
7. `build_change_sets()` given a subject's `Change`s with inconsistent `change_set_id` values (a
   corrupted or hand-built input, not something the batch-scoped allocator itself can produce)
   raises a specific `ValueError`/domain invariant error — it must never silently pick the first or
   last ID among the disagreeing values.

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
  validation exists yet. Both need attention once the implementation PR adds `Uuid7Id` typing —
  sized and flagged here so it lands as planned scope, not a surprise. Not touched in this PR.
  Fixture IDs are **not** migrated by editing the existing v4-shaped placeholders' version nibble —
  that would freeze in values the real generator never produced. Each fixture ID is instead
  generated once, offline, by the approved generator itself
  (`uuid_utils.compat.uuid7(timestamp=...)`, which accepts an explicit timestamp — verified via its
  own signature), using a timestamp plausibly matching that record's own narrative date, then frozen
  as a literal constant. Every existing cross-reference relationship (a source item's
  `latest_snapshot_id` equal to its snapshot's `id`, etc.) is preserved exactly. Test fixtures remain
  fully deterministic — generated once at authoring time, never regenerated at test-run time.
- **`SourceItem.event_id` remains `str | None`, unvalidated by this policy.** It is a human-readable
  grouping key today (e.g. `"ev-gpt4o-256k"` in the committed fixture pack), not a generated resource
  ID — no `Event` model is persisted and nothing constructs one via the UUID factory. A future
  persistent `Event` resource, if built, requires its own contract decision (a new or amending ADR)
  before `event_id` is retyped to `Uuid7Id` — out of scope here, not an oversight.
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
- Define the central `Uuid7Id: TypeAlias = pydantic.UUID7` alias in `shared/ids.py` and retype every
  `id`-shaped field in `shared/schemas.py` from `str` to `Uuid7Id` (or the correct type for a
  foreign-key-style reference field), per "All affected shared/public fields" in the Phase 1
  evidence report this ADR follows from.
- **Complete the UUID type-propagation audit.** Changing shared model attributes from `str` to
  `uuid.UUID` is not confined to `shared/schemas.py` — every typed interface and internal collection
  that receives, stores, or forwards one of those attributes must be updated to match, including
  where applicable:
  - `SnapshotResolver`'s method parameters and its in-memory dictionary keys (`shared/snapshot_resolver.py`);
  - `ResolutionResult.item_id` (`intelligence/resolve.py`);
  - `known_snapshot_ids` sets (`graph.py`, `daily_run.py`, `evaluate.py`);
  - the unresolved/failed item-ID result collections (`daily_run.py`'s `_BatchAccumulator`);
  - `validate.py`, `evaluate.py`, and `assemble_digest.py`'s signatures wherever they accept or
    return a resource ID;
  - the comparison-claim ID sets built in `daily_run.py`;
  - `ChangeSet.previous_snapshot_ids`/`current_snapshot_ids` and any internal collection that
    mirrors them during construction (`change_sets.py`);
  - every dataclass, `Protocol`, callback, and test helper that currently types a resource ID as
    `str`.

  Do not convert ordinary strings that are not resource IDs — source registry slugs, `event_id`,
  content hashes/`dedupe_key`, model names, field names, or status strings all stay `str`, per this
  ADR's existing scope boundaries. Avoid scattered `str()` conversions inside the domain: a
  `uuid.UUID` value stays a `uuid.UUID` value as it flows through internal code, converting to a
  string only at an explicit external boundary — JSON serialization, logging, or similar — never as
  an ad hoc workaround for a type mismatch discovered mid-refactor.
- Implement the batch-scoped `change_set_id_factory` design in `facts.py`, `graph.py`,
  `daily_run.py`, and `change_sets.py`, per "Batch-scoped ChangeSet ID allocation" above, including
  its seven regression tests.
- Migrate `tests/fixtures/contracts/*.json`'s 33 v4-shaped placeholder IDs to valid, frozen,
  generator-produced v7 values (not a nibble edit), per "Consequences" above.
- Update the ~190 non-UUID placeholder IDs in unit tests that will fail once `Uuid7Id` typing is
  enforced at the model boundary.
- Add the validation-expectations tests listed above, including an explicit test that a UUID v4
  value is rejected by `Uuid7Id`-typed fields, and — if the team decides stricter input-form
  rejection is needed beyond what `UUID7` provides by default — the custom validator and its tests.
- Prove the direct-dependency installation chain — local `uv lock` + `uv sync --locked --no-editable`,
  CI `uv lock --check` (or the supported equivalent) + the same locked non-editable install, import/
  generation/version/variant checks exercised through the normal test suite, and `uv build`/
  `pip-audit`/`make ci` — per "Dependency evaluation" above. Transitive presence via `langgraph` is
  not sufficient evidence on its own, and plain `uv sync --locked` does not prove a non-editable
  installation.

This implementation PR may begin only after this ADR amendment has been accepted by Persons A, B,
and C and merged. Because the implementation changes shared models and `docs/API_CONTRACT.md` used
by Delivery, Person C's confirmation is a prerequisite, not a deferred follow-up.

## API implementation PR — mandatory acceptance gates (future, separate PR, delivery-owned)

This ADR's implementation PR (shared/ids.py, shared/schemas.py, fixtures, tests) proves UUID v7
correctness at the Pydantic model level only — there is no FastAPI application yet to generate an
OpenAPI document or serve real HTTP responses against. That is a deliberate staging, not an
omission. Once a FastAPI vertical slice exists, its own PR must include four distinct acceptance
gates, kept separate because they prove different things:

1. **OpenAPI schema test.** The OpenAPI contract test must inspect the schema FastAPI/Pydantic
   actually emits for `Uuid7Id`, confirm that its base JSON type is `string`, record and verify its
   UUID7 format representation, and prove that the team's selected TypeScript generator can consume
   it. The test must not silently rewrite or assume the format value — **this ADR does not promise
   `format: uuid`**; Pydantic's emitted schema for a version-constrained UUID type may use a
   version-specific value such as `format: uuid7`, and the implementation PR must inspect and assert
   on the value actually observed, not one predicted here. Inspect both
   `model_json_schema(mode="validation")` and `model_json_schema(mode="serialization")` explicitly —
   Pydantic can emit different schemas for the two modes, and both are relevant to a generated
   client (validation shapes what the server accepts; serialization shapes what it returns).
2. **HTTP response test.** A real endpoint response serializes its UUID fields as canonical
   lowercase hyphenated strings — this tests actual serialized *values*, not the schema.
3. **HTTP request validation test.** A UUID v4 value and a malformed value are both rejected with
   the correct error response at the live HTTP boundary where `Uuid7Id` validation applies, not only
   at the isolated Pydantic model level.
4. **Cross-endpoint consistency test.** List, detail, chat, and applicable error/correlation IDs all
   use the same representation, checked in one test spanning multiple endpoints rather than trusting
   each endpoint's own isolated test.

**Not required by this ADR**: committing the generated TypeScript client to the repository — Person
C has not yet selected the code generator or decided whether generated output is version-controlled;
that is a separate, later decision. What *is* required once a generator is selected: a CI job that
regenerates the client from the live OpenAPI schema and compiles/type-checks it as a smoke test,
catching schema drift without presupposing a commit-vs-generate-only answer.

## Explicitly out of scope

- Designing or implementing subscription confirmation and unsubscribe tokens.
- Security tokens require a separate delivery/security decision and separate implementation PR.
- That future mechanism must use cryptographically random signed values and must not call the UUID
  generator.

## Amendment PR workflow — Person C acceptance sequence

Person C is active again; nothing in this ADR treats their confirmation as deferred or optional.
This section is part of the durable record, not just PR narration:

1. Push this documentation amendment while the ADR status remains `Accepted by Persons A and B;
   Person C confirmation pending` — the amendment does not itself claim Person C's confirmation; it
   exists to earn it.
2. Request design review from both Person B (`@SujinJK`) and Person C (`@chamath-wijayasundara`)
   explicitly, since the amendment changes technical content Person B previously approved under
   different wording.
3. Wait for Person C to leave an explicit comment confirming that all five of their original
   concerns — pagination tie-breaker, factory representation, `change_set_id` lifecycle, OpenAPI/
   client gates, dependency validation — are resolved by this amendment. A directionally-supportive
   comment without that explicit confirmation does not satisfy this step, per the standard Person C
   already set on PR #16.
4. Only after that confirmation, update the ADR status line to `Accepted by Persons A, B, and C`.
5. Push that status-only change as its own commit, separate from the content amendment — the same
   discipline already used for the Person B status update, so content and status changes each have
   their own clean, reviewable diff.
6. Because a new push dismisses stale approvals under this repository's branch protection, request
   fresh formal approvals from both Person B and Person C on that status commit — a prior approval
   on earlier content does not carry forward automatically.
7. Merge only after required CI checks (`quality`, `tests`, `security`) and both fresh approvals are
   green — not on Person C's comment alone, and not on CI alone.

## References

- [RFC 9562 — Universally Unique IDentifiers (UUIDs)](https://datatracker.ietf.org/doc/rfc9562/)
- [ADR 0004](0004-extracted-fact-keeps-evidence.md) — precedent for additive, model-level-enforced
  shared-contract changes
- [ADR 0005](0005-structured-comparison-and-snapshot-resolution.md) — precedent for phased,
  explicitly-scoped shared-contract decisions
- `docs/API_CONTRACT.md` — General API rules, and every resource contract example
