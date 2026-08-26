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
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime

from ai_daily_digest.shared.ids import new_id
from ai_daily_digest.shared.schemas import Change, ExtractedFact, FactObservation, Subject

# Compiled once at import time rather than inside normalise_name(): this
# function runs on every candidate string for every subject for every
# item during resolution (resolve.py) plus every FactStore lookup here,
# so re-compiling the same two patterns on every call is pure overhead.
_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Deliberately
    simple and auditable — see intelligence/resolve.py for where this is
    used against the alias table."""
    lowered = name.lower()
    stripped = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


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
    current_snapshot_id: str | None = None
    current_source_url: str | None = None
    current_observed_at: datetime | None = None
    history: list[ExtractedFact] = dataclass_field(default_factory=list)


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

        Returns a Change if this differs from the currently known value —
        returns None for a first-time observation (new information, but
        not a change — see this project's second review: nothing
        downstream currently turns a first observation into its own
        digest content either; "reported elsewhere" was aspirational,
        not a real path, and is corrected here to say so) or an identical
        value. An identical value still refreshes the stored provenance
        (snapshot/source/observed_at) to this newer confirmation, so a
        fact re-confirmed many times doesn't keep citing its original,
        increasingly stale snapshot — it's a no-op for Change purposes,
        not a no-op for "what's the freshest evidence for this fact"."""
        self.register_subject(subject)
        key = (*_subject_key(subject), fact.field)
        record = self._fields.setdefault(key, _FieldRecord())

        previous = record.current
        previous_observation = None
        if previous is not None:
            if previous.value == fact.value:
                record.current = fact
                record.current_snapshot_id = fact.snapshot_id
                record.current_source_url = source_url
                record.current_observed_at = observed_at
                return None  # unchanged value, but provenance refreshed above
            previous_observation = FactObservation(
                value=previous.value,
                observed_at=record.current_observed_at,
                snapshot_id=record.current_snapshot_id,
                # pydantic validates/coerces str -> HttpUrl at runtime;
                # mypy doesn't model that coercion statically.
                source_url=record.current_source_url,  # type: ignore[arg-type]
            )
            record.history.append(previous)

        record.current = fact
        record.current_snapshot_id = fact.snapshot_id
        record.current_source_url = source_url
        record.current_observed_at = observed_at

        if previous is None:
            return None  # first observation, not a change

        resolved_change_type = (
            change_type
            if change_type is not None
            else _infer_change_type(previous.value, fact.value)
        )

        return Change(
            id=new_id(),
            change_set_id="",  # assigned by the caller grouping Changes into a ChangeSet
            subject=subject,
            field=fact.field,
            change_type=resolved_change_type,
            previous=previous_observation,
            current=FactObservation(
                value=fact.value,
                observed_at=observed_at,
                snapshot_id=fact.snapshot_id,
                source_url=source_url,  # type: ignore[arg-type]
            ),
            confidence=confidence,
        )
