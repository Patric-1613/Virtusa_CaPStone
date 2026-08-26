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
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

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
    (see intelligence/extract_facts.py). Added by
    docs/adr/0004-extracted-fact-keeps-evidence.md."""

    id: str  # UUID v4
    snapshot_id: str
    field: str
    value: str
    extraction_method: str  # "deterministic" | "llm_structured_output"
    extraction_model: str | None = None
    prompt_version: str | None = None
    quoted_span: str | None = None
    confidence: Confidence | None = None


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
