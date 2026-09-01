"""Shared data contract — mirrors docs/API_CONTRACT.md exactly. Any
divergence between this file and that document is a bug; whichever is
wrong, fix it in the same PR (see API_CONTRACT.md's "Contract-change
process"). docs/API_CONTRACT.md is currently "Draft v0.1 for agreement by
all three module owners" — treat this file the same way: not yet final.

Scope note: this file covers the models intelligence produces/consumes
(SourceItem, DocumentSnapshot, Change/ChangeSet, ExtractedFact,
Digest/DigestClaim). Source/CollectionRun (ingestion) and
Subscription/EmailDelivery (delivery) aren't reproduced here yet — add
them when a module actually needs the shared type, per shared/README.md's
"smallest stable set... required across modules", not preemptively.

Requires: pydantic>=2
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

# A confidence score, everywhere one appears in this contract or in an LLM
# response model that feeds it (ExtractedFact.confidence, Change.confidence,
# and intelligence/extract_facts.py::FactCandidate,
# intelligence/resolve_llm.py::ResolveLLMResponse both import this rather
# than redeclaring `float`). bounded [0, 1] AND allow_inf_nan=False --
# ge/le alone already reject NaN (every comparison with NaN is False, so a
# bare `ge=0` constraint fails closed on it), but allow_inf_nan=False makes
# that rejection an explicit, intentional guarantee rather than a side
# effect of how IEEE754 comparisons happen to behave. Without this, a
# confidence=NaN response silently bypassed every "confidence < threshold"
# check in the codebase (NaN < 0.6 is also False) — verified as a real gap
# in review, not hypothetical.
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

# ---------------------------------------------------------------------------
# Ingestion output, intelligence input
# ---------------------------------------------------------------------------


class SourceItem(BaseModel):
    """The normalized identity and metadata of a published item. Content
    lives separately in DocumentSnapshot — an item may have multiple
    snapshots over time; this record never holds body text itself."""

    id: str  # UUID v4
    dedupe_key: str  # sha256 of the normalized canonical_url; DB-unique
    source_id: str
    publisher: str
    title: str
    canonical_url: HttpUrl
    published_at: datetime | None = None
    updated_at: datetime | None = None
    first_fetched_at: datetime
    latest_snapshot_id: str | None = None
    event_id: str | None = None  # nullable until items are grouped by event
    authors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    language: str = "en"


class DocumentSnapshot(BaseModel):
    """One immutable version of fetched content. content_text may be
    omitted from list responses (see API_CONTRACT.md) — treat it as
    Optional even though a stored snapshot always has one."""

    id: str  # UUID v4
    source_item_id: str
    fetched_at: datetime
    content_hash: str
    content_text: str | None = None
    raw_location: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    collector_version: str | None = None


# ---------------------------------------------------------------------------
# Intelligence-internal identity, not a standalone API resource
# ---------------------------------------------------------------------------


class Subject(BaseModel):
    """What a Change/ChangeSet is about. Embedded, not a top-level
    resource with its own opaque id — intelligence resolves a SourceItem
    to a Subject (see intelligence/resolve.py); there is no public
    "Entity" endpoint in docs/API_CONTRACT.md.

    frozen=True: a pure immutable value object, and hashable so callers
    can put Subjects in sets/dict keys (e.g. resolve.py's candidate
    dedup) without a workaround."""

    model_config = ConfigDict(frozen=True)

    company: str
    product: str


# ---------------------------------------------------------------------------
# Fact extraction (intelligence)
# ---------------------------------------------------------------------------


class ExtractedFact(BaseModel):
    """One field-level fact extracted from a single snapshot. Facts
    created by deterministic code use extraction_method="deterministic";
    facts created by an LLM must also record extraction_model and
    prompt_version so evaluations are reproducible.

    quoted_span/confidence: the evidence a fact was built from, kept
    (not discarded) so it can be audited later -- did this fact's value
    actually come from the text it claims, and how confident was the
    extraction. Optional because deterministic facts don't always have a
    natural "quote" to attach; LLM-extracted facts always populate both
    (see intelligence/extract_facts.py) and this is now enforced here,
    not just by extraction code and contract tests, per ADR 0004's
    accepted clarification -- see _require_evidence_for_llm_facts below.
    Added by docs/adr/0004-extracted-fact-keeps-evidence.md.

    disclosure_status/value: ADR 0006's "unknown" vs. "not disclosed" are
    different claims. `value=None` here means "the source explicitly
    states this is being withheld" (disclosure_status="not_disclosed"),
    itself a groundable claim needing its own citation -- NOT "no
    extraction ever found a value", which is represented by no
    ExtractedFact existing at all, never by one with a null value. See
    docs/adr/0006-disclosure-status-semantics.md."""

    id: str  # UUID v4
    snapshot_id: str
    field: str
    value: str | None = None
    disclosure_status: Literal["disclosed", "not_disclosed"] = "disclosed"
    extraction_method: str  # "deterministic" | "llm_structured_output"
    extraction_model: str | None = None
    prompt_version: str | None = None
    quoted_span: str | None = None
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def _require_evidence_for_llm_facts(self) -> ExtractedFact:
        """ADR 0004's accepted clarification: the requirement that
        LLM-extracted facts carry both quoted_span and confidence was
        previously enforced only by intelligence/extract_facts.py's own
        construction code and by tests/contract/test_fixture_contract.py
        -- nothing stopped a DIFFERENT construction path (a future
        extraction call site, a hand-built fixture) from creating an
        LLM-attributed fact with no evidence at all. This model-level
        invariant makes that impossible regardless of how the object is
        built. Deterministic facts are unaffected -- they don't always
        have a natural quote to attach (see this class's own docstring)."""
        if self.extraction_method == "llm_structured_output":
            if self.quoted_span is None:
                raise ValueError(
                    "ExtractedFact with extraction_method='llm_structured_output' "
                    "must have quoted_span set (ADR 0004)"
                )
            if self.confidence is None:
                raise ValueError(
                    "ExtractedFact with extraction_method='llm_structured_output' "
                    "must have confidence set (ADR 0004)"
                )
        return self

    @model_validator(mode="after")
    def _require_valid_disclosure_state(self) -> ExtractedFact:
        """ADR 0006: "not disclosed" is a groundable claim, not a default
        inferred from silence -- both invalid states below are rejected
        at construction, not just documented as a convention:
          - disclosure_status="not_disclosed" together with a non-null
            value -- contradictory: a fact can't simultaneously state a
            value and claim none was given.
          - disclosure_status="not_disclosed" without grounded evidence
            (a non-empty quoted_span citing the actual non-disclosure
            statement) -- the same evidence requirement ADR 0004
            established for a disclosed value applies here too; "not
            disclosed" needs a citation, not a default. Required
            regardless of extraction_method -- unlike
            _require_evidence_for_llm_facts above, ADR 0006 draws no
            deterministic-vs-LLM distinction here.
          - disclosure_status="disclosed" (the default) without a real,
            non-empty value -- the "normal" case's own invariant, now
            enforced at the model level now that `value` is Optional at
            the type level."""
        if self.disclosure_status == "not_disclosed":
            if self.value is not None:
                raise ValueError(
                    "ExtractedFact with disclosure_status='not_disclosed' must have "
                    "value=None (ADR 0006) -- a fact can't state a value and also "
                    "claim none was given"
                )
            if not self.quoted_span:
                raise ValueError(
                    "ExtractedFact with disclosure_status='not_disclosed' must have a "
                    "non-empty quoted_span citing the actual non-disclosure statement "
                    "(ADR 0006) -- 'not disclosed' is a groundable claim, not a "
                    "default inferred from silence"
                )
        elif not self.value:
            raise ValueError(
                "ExtractedFact with disclosure_status='disclosed' (the default) must "
                "have a non-empty value"
            )
        return self


# ---------------------------------------------------------------------------
# Change detection (intelligence)
# ---------------------------------------------------------------------------


class FactObservation(BaseModel):
    """The previous/current sub-object nested inside a Change — distinct
    from ExtractedFact, which is the persisted per-snapshot record this is
    derived from."""

    value: str | None = None
    observed_at: datetime | None = None
    snapshot_id: str | None = None
    source_url: HttpUrl | None = None


class Change(BaseModel):
    """One field-level change. `previous` is null only when the absence
    means "not previously disclosed" — never used to mean "unknown"."""

    id: str  # UUID v4
    change_set_id: str
    subject: Subject
    field: str
    change_type: str  # e.g. "increased", "decreased", "disclosed", "changed"
    previous: FactObservation | None = None
    current: FactObservation
    confidence: Confidence
    review_status: str = "pending"  # "pending" | "validated" | "rejected"


class ChangeSet(BaseModel):
    """The internal aggregate GET /v1/changes/{change_id} returns — one or
    more Changes about the same subject, grouped with their supporting
    snapshot ids."""

    id: str  # UUID v4
    subject: Subject
    changes: list[Change] = Field(default_factory=list)
    previous_snapshot_ids: list[str] = Field(default_factory=list)
    current_snapshot_ids: list[str] = Field(default_factory=list)
    review_status: str = "pending"


# ---------------------------------------------------------------------------
# Digest (intelligence produces, delivery renders unchanged)
# ---------------------------------------------------------------------------


class DigestClaim(BaseModel):
    """Every factual claim requires >=1 valid citation. A digest
    containing an unsupported claim cannot enter "published" status
    automatically."""

    id: str  # UUID v4
    text: str
    citation_snapshot_ids: list[str] = Field(default_factory=list)
    validation_status: str = "pending"  # "pending" | "supported" | "unsupported"


class Digest(BaseModel):
    id: str  # UUID v4
    digest_date: str  # YYYY-MM-DD
    status: str = "draft"  # "draft" | "review" | "published"
    title: str
    claims: list[DigestClaim] = Field(default_factory=list)
