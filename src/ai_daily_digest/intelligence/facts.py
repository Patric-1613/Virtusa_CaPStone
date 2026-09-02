"""Fact store with history — intelligence's internal working state for
change detection. Not part of the public shared contract:
docs/API_CONTRACT.md has no "Entity" or "FactStore" resource, only
Change/ChangeSet (the output) and ExtractedFact (the per-snapshot input).
This class exists purely to answer "what did we last observe for this
subject/field" so update_fact() can emit a contract-shaped Change when a
new observation differs from it.

The core rule this module exists to protect: a Change's `previous` is
built from what was actually stored before — never recomputed or
mutated after the fact. history() is append-only.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime

from pydantic import TypeAdapter

from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import (
    Change,
    Confidence,
    ExtractedFact,
    FactObservation,
    Subject,
)

# Compiled once at import time rather than inside normalise_name(): this
# function runs on every candidate string for every subject for every
# item during resolution (resolve.py) plus every FactStore lookup here,
# so re-compiling the same two patterns on every call is pure overhead.
_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")

# Mirrors grounding.py's _NUMBER_FORMATTING_RE -- deliberately duplicated
# rather than imported, to avoid a circular import (grounding.py already
# imports normalise_name from this module). Strips thousands separators
# and currency/percent symbols WITHOUT turning them into word-boundary
# spaces, so "$5,000.00" and "5000" parse as the same float.
_VALUE_NUMBER_FORMATTING_RE = re.compile(r"[,$%]")

# Reuses shared/schemas.py's Confidence constraint (bounded [0, 1], no
# inf/NaN) rather than restating the bound here, so update_fact() can
# reject a bad caller-supplied confidence BEFORE it allocates a UUID or
# calls change_set_id_factory -- without risking drift from the shared
# type. The Change model re-checks the same constraint; this is the
# earlier gate, not a second definition (ADR 0007's failed-processing
# rule).
_CONFIDENCE_ADAPTER: TypeAdapter[float] = TypeAdapter(Confidence)


def normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Deliberately
    simple and auditable — see intelligence/resolve.py for where this is
    used against the alias table."""
    lowered = name.lower()
    stripped = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def _values_are_equivalent(previous_value: str | None, current_value: str | None) -> bool:
    """True if two extracted values represent the same underlying fact
    despite different formatting. extract_facts.py's own acceptance
    check (grounding.py::value_supported_by_quote) already tolerates
    this kind of difference (e.g. a value of "5" is accepted against a
    quote saying "$5.00"), so a fact that's re-accepted as grounded must
    not then be treated as "changed" here purely because the model
    phrased the same real-world value differently between two
    extractions. Numeric values are compared as floats after stripping
    formatting; everything else falls back to normalise_name's
    case/punctuation/whitespace-insensitive comparison.

    None is ADR 0006's "not disclosed" (never "unknown" -- see
    ExtractedFact's own docstring, shared/schemas.py). Two not_disclosed
    observations in a row are equivalent (nothing changed); a disclosed
    value on one side and None on the other are never equivalent -- that
    IS a real disclosure-status change, just one update_fact() below
    doesn't currently turn into a reportable Change (see its own
    docstring)."""
    if previous_value is None or current_value is None:
        return previous_value == current_value
    if previous_value == current_value:
        return True
    try:
        previous_number = float(_VALUE_NUMBER_FORMATTING_RE.sub("", previous_value))
        current_number = float(_VALUE_NUMBER_FORMATTING_RE.sub("", current_value))
    except (TypeError, ValueError):
        return normalise_name(previous_value) == normalise_name(current_value)
    return previous_number == current_number


def change_snapshot_ids(change: Change) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(current_snapshot_id, previous_snapshot_id) -- the shared
    definition of which snapshot ids a Change references. Previously
    reimplemented independently in graph.py's validate node,
    draft_claims.py's citation building, and change_sets.py's
    previous/current collection, each writing the same
    previous-is-optional/current-is-required None-checks slightly
    differently -- a real risk of drift, not just duplication. Either
    element is None when that side has no recorded snapshot id (a first
    disclosure has no previous)."""
    previous_id = change.previous.snapshot_id if change.previous is not None else None
    return change.current.snapshot_id, previous_id


def _subject_key(subject: Subject) -> tuple[str, str]:
    """The dict key FactStore indexes everything by: normalised
    (company, product) so "OpenAI" / "openai" / "OpenAI." all collide."""
    return (normalise_name(subject.company), normalise_name(subject.product))


def _infer_change_type(previous_value: str, current_value: str) -> str:
    """ "increased"/"decreased" when both values parse as plain numbers
    and differ that way; "changed" otherwise (non-numeric fields like
    licence_terms, or values with units/formatting that don't parse).
    Callers can still override via update_fact()'s change_type param for
    cases they know more about than a bare float comparison can."""
    try:
        previous_number = float(previous_value)
        current_number = float(current_value)
    except (TypeError, ValueError):
        return "changed"
    if current_number > previous_number:
        return "increased"
    if current_number < previous_number:
        return "decreased"
    return "changed"


@dataclass
class _FieldRecord:
    """One (subject, field)'s current known value plus its full history.
    current_snapshot_id/current_source_url/current_observed_at are kept
    alongside `current` (rather than reading them off `current` itself)
    because ExtractedFact only carries snapshot_id — source_url and
    observed_at are provenance FactStore adds, not part of the extracted
    fact's own contract shape."""

    current: ExtractedFact | None = None
    current_snapshot_id: uuid.UUID | None = None
    current_source_url: str | None = None
    current_observed_at: datetime | None = None
    history: list[ExtractedFact] = dataclass_field(default_factory=list)

    def advance_current(
        self, fact: ExtractedFact, *, source_url: str | None, observed_at: datetime
    ) -> None:
        """Point `current` and its provenance at a newly observed fact.
        update_fact() calls this only once it has committed to recording
        the observation -- and, on the path that also emits a Change,
        only after that Change has been fully constructed and validated,
        so a rejected observation never advances the store (ADR 0007's
        failed-processing rule)."""
        self.current = fact
        self.current_snapshot_id = fact.snapshot_id
        self.current_source_url = source_url
        self.current_observed_at = observed_at


class FactStore:
    """In-memory now; the natural place for a Postgres-backed
    implementation to live once ingestion's database exists (see
    docs/adr/0002-postgres-pgvector.md) — the public method signatures
    below are the contract intelligence code depends on, not this
    dict-based storage."""

    def __init__(self) -> None:
        """Starts empty — nothing is known until register_subject() or
        update_fact() (which registers implicitly) is called."""
        self._known_subjects: dict[tuple[str, str], Subject] = {}
        self._fields: dict[tuple[str, str, str], _FieldRecord] = {}

    def known_subjects(self) -> list[Subject]:
        """Every subject this store has ever seen — resolve.py reads
        this as deterministic matching's candidate list."""
        return list(self._known_subjects.values())

    def register_subject(self, subject: Subject) -> None:
        """Idempotent — registering an already-known subject is a no-op,
        so callers never need to check first."""
        self._known_subjects.setdefault(_subject_key(subject), subject)

    def get_current_fact(self, subject: Subject, field: str) -> ExtractedFact | None:
        """None means "never observed", not "observed as empty" — a
        genuine gap, per the project's grounding rules."""
        record = self._fields.get((*_subject_key(subject), field))
        return record.current if record else None

    def field_history(self, subject: Subject, field: str) -> list[ExtractedFact]:
        """Superseded values only — the current one lives in
        get_current_fact(), not duplicated here. A defensive copy, so
        callers can't mutate the store's internal history by accident."""
        record = self._fields.get((*_subject_key(subject), field))
        return list(record.history) if record else []

    def update_fact(  # pylint: disable=too-many-arguments
        # subject/fact identify what's being recorded; source_url/
        # observed_at are provenance the caller must supply per-call
        # (see _FieldRecord's docstring for why FactStore doesn't infer
        # them); change_type/confidence are optional overrides for
        # callers that know more than a bare value comparison can.
        self,
        subject: Subject,
        fact: ExtractedFact,
        *,
        source_url: str | None,
        observed_at: datetime,
        change_set_id_factory: Callable[[], uuid.UUID],
        change_type: str | None = None,
        confidence: float = 1.0,
    ) -> Change | None:
        """Record a newly extracted fact for (subject, fact.field).
        Snapshot id comes from `fact.snapshot_id` — there is no separate
        snapshot_id parameter, deliberately: ExtractedFact already carries
        it, and a second copy is exactly the kind of thing that silently
        drifts out of sync (this file's own tests once did).

        change_type=None (the default) auto-infers "increased"/
        "decreased"/"changed" from the two values (_infer_change_type) —
        pass an explicit value only when the caller knows something a
        bare numeric comparison can't (e.g. "disclosed").

        change_set_id_factory (ADR 0007's "Batch-scoped ChangeSet ID
        allocation"): called exactly once, and only on the single path
        that returns a real Change -- after every value that could fail
        validation (both FactObservations, so a bad source_url; the
        confidence bound) has already been checked, and before the store
        is advanced. A first observation, an unchanged value, an ADR 0006
        disclosure transition, or a validation failure on any of those
        inputs all return None (or raise) without calling it and without
        touching record.current or history, so no UUID is spent -- and no
        partial write is left behind -- for an observation that never
        becomes a Change. Callers pass a
        batch-scoped get-or-create closure (change_sets.py::
        get_or_create_change_set_id, closed over graph.py's per-run
        allocator) -- never a plain value, and never something FactStore
        itself caches, since FactStore persists across runs (see its own
        class docstring) while a change_set_id must be fresh every batch.

        Returns a Change if this differs from the currently known value —
        returns None for a first-time observation (new information, but
        not a change — see this project's second review: nothing
        downstream currently turns a first observation into its own
        digest content either; "reported elsewhere" was aspirational,
        not a real path, and is corrected here to say so), an identical
        value, or a disclosure-status transition (ADR 0006 — either side
        of the comparison has value=None, meaning it's a "not disclosed"
        observation, not a real value): the fact IS still recorded (so
        get_current_fact()/build_fact_table() see the new disclosure
        state right away), it just doesn't become a Change/DigestClaim
        here — the same treatment a first observation already gets, and
        for the same reason: nothing downstream has an agreed wording
        yet for "X stopped/started disclosing Y" as a single-subject
        sentence, and this ADR's scope is compare_subjects.py's
        cross-subject rendering, not draft_claims.py's. An identical
        value still refreshes the stored provenance (snapshot/source/
        observed_at) to this newer confirmation, so a fact re-confirmed
        many times doesn't keep citing its original, increasingly stale
        snapshot — it's a no-op for Change purposes, not a no-op for
        "what's the freshest evidence for this fact"."""
        self.register_subject(subject)
        key = (*_subject_key(subject), fact.field)
        record = self._fields.setdefault(key, _FieldRecord())
        previous = record.current

        if previous is None or _values_are_equivalent(previous.value, fact.value):
            # First observation, or an unchanged value: the fact is
            # recorded (provenance refreshed to this newer confirmation),
            # but it is never a Change and never consumes a change_set_id.
            record.advance_current(fact, source_url=source_url, observed_at=observed_at)
            return None

        # `previous` exists and the value genuinely differs. Build the
        # `previous` side now, at the same point the original code did --
        # before the disclosure-transition check -- so that path's
        # behaviour is unchanged.
        previous_observation = FactObservation(
            value=previous.value,
            observed_at=record.current_observed_at,
            snapshot_id=record.current_snapshot_id,
            # pydantic validates/coerces str -> HttpUrl at runtime;
            # mypy doesn't model that coercion statically.
            source_url=record.current_source_url,  # type: ignore[arg-type]
        )

        if fact.value is None or previous.value is None:
            # A disclosure-status transition (ADR 0006), either direction --
            # the superseded value moves to history and the new state is
            # recorded, but it is not reported as a Change here (see this
            # method's own docstring for why). Guarding on nullness
            # directly, not `disclosure_status`, matches ExtractedFact's
            # own invariant that the two always agree.
            record.history.append(previous)
            record.advance_current(fact, source_url=source_url, observed_at=observed_at)
            return None

        # A real changed value -> a Change. Everything that can fail
        # validation -- the confidence bound and the `current`
        # FactObservation's source_url -- is checked HERE, before new_id()
        # and change_set_id_factory() run and before the store is touched,
        # so a rejected input spends no UUID and leaves record.current and
        # history exactly as they were (ADR 0007's failed-processing rule).
        _CONFIDENCE_ADAPTER.validate_python(confidence)
        current_observation = FactObservation(
            value=fact.value,
            observed_at=observed_at,
            snapshot_id=fact.snapshot_id,
            source_url=source_url,  # type: ignore[arg-type]
        )
        resolved_change_type = (
            change_type
            if change_type is not None
            else _infer_change_type(previous.value, fact.value)
        )
        change = Change(
            id=new_id(),
            change_set_id=change_set_id_factory(),
            subject=subject,
            field=fact.field,
            change_type=resolved_change_type,
            previous=previous_observation,
            current=current_observation,
            confidence=confidence,
        )

        # The Change constructed and validated -- only now advance the store.
        record.history.append(previous)
        record.advance_current(fact, source_url=source_url, observed_at=observed_at)
        return change
