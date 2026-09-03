"""Pure cursor-pagination layer for the Delivery API — ADR 0008 PR 3.

This module is deliberately framework-free: it imports only the standard
library, Pydantic, and the shared UUID boundary (`shared/ids.py`). It has
**no** FastAPI, database, network, or wall-clock dependency, so it can be
unit-tested in isolation and reused by every future list endpoint.

What lives here (all pure):

* ``Page`` — the generic ``{"items": [...], "next_cursor": null}`` envelope.
* ``CanonicalFilters`` / ``canonicalize_filters`` — the one immutable typed
  filter representation that feeds **both** the cursor fingerprint and a
  future repository query (ADR 0008 section 7).
* ``normalize_filter_string`` / ``normalize_aware_datetime`` /
  ``normalize_calendar_date`` — the value-normalization helpers.
* ``validate_half_open_range`` / ``HalfOpenRange`` — pure ``[from, to)``
  range validation.
* ``CursorKey`` / ``CursorPayload`` — the strictly validated opaque cursor
  contents (no ``limit``, ever — ADR 0008 sections 3 and 6).
* ``CursorCodec`` — HMAC-SHA256 signed, versioned, URL-safe-base64 cursor
  encode/decode with a single generic ``InvalidCursorError`` for every
  failure mode (ADR 0008 sections 6 and 9).

The HTTP status mapping for ``InvalidCursorError`` (400 / ``invalid_cursor``)
and for the ``FilterValidationError`` family (422) is **not** in this PR — it
belongs to the endpoint PRs (ADR 0008 section 14).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai_daily_digest.shared.ids import Uuid7Id

__all__ = [
    "CURSOR_VERSION",
    "DEFAULT_LIMIT",
    "MAX_ENCODED_CURSOR_LENGTH",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "MIN_SIGNING_KEY_BYTES",
    "CanonicalFilterValue",
    "CanonicalFilters",
    "CursorCodec",
    "CursorKey",
    "CursorPayload",
    "FilterInput",
    "FilterValidationError",
    "HalfOpenRange",
    "InvalidCursorError",
    "Page",
    "RangeValidationError",
    "build_cursor_payload",
    "canonicalize_filters",
    "normalize_aware_datetime",
    "normalize_calendar_date",
    "normalize_filter_string",
    "validate_half_open_range",
]

# --- Constants (ADR 0008 sections 3 and 6) ---------------------------------

CURSOR_VERSION = 1
"""The only cursor schema version this codec understands (ADR 0008 section 6)."""

MAX_ENCODED_CURSOR_LENGTH = 512
"""Maximum encoded token length, checked before any base64 decoding."""

MIN_SIGNING_KEY_BYTES = 32
"""Minimum signing-key length. The configuration provider is responsible for
startup validation; the codec re-checks it as defence in depth."""

DEFAULT_LIMIT = 20
MIN_LIMIT = 1
MAX_LIMIT = 100
"""Page-size bounds (ADR 0008 section 3). ``limit`` is never encoded in, or
fingerprinted by, the cursor — these constants exist for the endpoint PR and
for the contract document, not for anything in this module."""

_GENERIC_INVALID_CURSOR_MESSAGE = "The pagination cursor is invalid for this request."

# --- Exceptions -----------------------------------------------------------


class InvalidCursorError(Exception):
    """Every cursor decode or verification failure surfaces as this one error.

    The message is fixed and identical for all failure modes — malformed
    token, bad base64, bad signature, bad JSON, unknown or missing field,
    unsupported version, resource/sort/filter mismatch, malformed or non-v7
    UUID, unparseable sort value, oversized token. It never names the failing
    step and never carries the token, signature, decoded payload, or signing
    key (ADR 0008 section 9).
    """

    def __init__(self) -> None:
        super().__init__(_GENERIC_INVALID_CURSOR_MESSAGE)


class FilterValidationError(ValueError):
    """A pure filter/normalization helper rejected its input.

    Distinct from :class:`InvalidCursorError`: this is raised while building
    the canonical filters for a *fresh* request (e.g. a timezone-naive
    datetime, a non-calendar date). Mapping it to an HTTP 422 response is the
    endpoint PR's job, not this module's (ADR 0008 sections 3.1 and 14).
    """


class RangeValidationError(FilterValidationError):
    """A half-open range had equal or reversed bounds (ADR 0008 section 8.1)."""


# --- Value normalization helpers ----------------------------------------

type CanonicalFilterValue = str | bool | None
"""What a canonical filter entry may hold: a normalized string, a boolean for
a hidden constraint, or ``None`` for an explicitly-supplied null. Absent
filters are omitted entirely, so ``None`` is unambiguous."""

type FilterInput = str | bool | datetime | date | None
"""Accepted raw filter value types before canonicalization."""

_SHA256_HEX_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_B64URL_SEGMENT_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")
_CANONICAL_TIMESTAMP_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_CALENDAR_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_EXCLUDED_FILTER_KEYS = frozenset({"cursor", "limit"})


def normalize_filter_string(value: str) -> str:
    """Trim surrounding whitespace and apply Unicode NFC. Case is preserved,
    so ``"openai"`` and ``"OpenAI"`` stay distinct filters (ADR 0008
    section 7)."""
    return unicodedata.normalize("NFC", value.strip())


def normalize_aware_datetime(value: datetime) -> str:
    """Normalize a timezone-aware datetime to a canonical UTC string with
    exactly six fractional digits and a trailing ``Z``
    (``YYYY-MM-DDTHH:MM:SS.ffffffZ``), preserving microseconds exactly.

    A timezone-naive datetime is rejected — the service never guesses a zone
    (ADR 0008 sections 5.A and 7).
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise FilterValidationError("timezone-naive datetimes are not accepted")
    canonical = value.astimezone(UTC).isoformat(timespec="microseconds")
    return canonical.replace("+00:00", "Z")


def normalize_calendar_date(value: date | str) -> str:
    """Normalize a calendar date to ``YYYY-MM-DD``.

    A ``datetime`` is rejected (a calendar date carries no time); an
    unparseable or non-calendar string such as ``"2026-13-40"`` raises
    :class:`FilterValidationError`.
    """
    if isinstance(value, datetime):
        raise FilterValidationError("a calendar date must not carry a time component")
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise FilterValidationError("value is not a valid calendar date") from exc


# --- Half-open range validation ---------------------------------------


@dataclass(frozen=True)
class HalfOpenRange[BoundT: date]:
    """A validated ``[lower, upper)`` interval. Either bound (or both) may be
    ``None``; when both are present ``lower`` is strictly earlier than
    ``upper``."""

    lower: BoundT | None
    upper: BoundT | None


def validate_half_open_range[BoundT: date](
    lower: BoundT | None, upper: BoundT | None
) -> HalfOpenRange[BoundT]:
    """Validate a half-open ``[from, to)`` range (ADR 0008 section 8.1).

    A one-sided range, and no bounds at all, are valid. When both bounds are
    given, ``lower`` must be strictly earlier than ``upper``; equal or
    reversed bounds raise :class:`RangeValidationError`.
    """
    if lower is not None and upper is not None and lower >= upper:
        raise RangeValidationError(
            "range lower bound must be strictly earlier than the upper bound"
        )
    return HalfOpenRange(lower=lower, upper=upper)


# --- Canonical filters ------------------------------------------------


def _canonical_filter_value(value: FilterInput) -> CanonicalFilterValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return normalize_filter_string(value)
    if isinstance(value, datetime):
        return normalize_aware_datetime(value)
    if isinstance(value, date):
        return normalize_calendar_date(value)
    raise FilterValidationError("unsupported filter value type")


@dataclass(frozen=True)
class CanonicalFilters:
    """One immutable, already-normalized filter representation.

    The *same* instance is the input to both the cursor filter fingerprint
    (:meth:`fingerprint`) and, in a later PR, the repository query
    (:meth:`as_dict`). Raw request strings are never passed to the repository
    separately, so the two can never disagree about what was filtered
    (ADR 0008 section 7).

    ``items`` is sorted by key and includes any hidden constraints (e.g.
    ``("published_only", True)``). ``resource``, ``sort`` and ``version`` are
    part of the fingerprint input so that a change to any of them invalidates
    an existing cursor. ``limit`` is deliberately absent.
    """

    resource: str
    sort: str
    version: int
    items: tuple[tuple[str, CanonicalFilterValue], ...]

    def as_dict(self) -> dict[str, CanonicalFilterValue]:
        """A fresh plain ``dict`` of the canonical filter values, for a future
        repository query. Mutating it cannot affect this instance."""
        return dict(self.items)

    def fingerprint(self) -> str:
        """The lowercase SHA-256 hex digest bound into a cursor's ``f`` field."""
        payload: dict[str, Any] = {
            "filters": dict(self.items),
            "resource": self.resource,
            "sort": self.sort,
            "version": self.version,
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def canonicalize_filters(
    *,
    resource: str,
    sort: str,
    filters: dict[str, FilterInput],
    hidden: dict[str, bool] | None = None,
    version: int = CURSOR_VERSION,
) -> CanonicalFilters:
    """Canonicalize a request's membership-affecting filters exactly once.

    * strings are trimmed and NFC-normalized; case is preserved;
    * timezone-aware datetimes become canonical UTC strings with microseconds
      preserved; timezone-naive datetimes are rejected;
    * dates become ``YYYY-MM-DD``;
    * booleans pass through (for hidden constraints such as ``published_only``);
    * an explicit ``None`` is kept (distinct from an absent key, which the
      caller simply omits);
    * ``cursor`` and ``limit`` keys are rejected — they must never bind a cursor.

    ``resource``, ``sort``, the hidden constraints and ``version`` are all
    folded into the resulting :class:`CanonicalFilters` and therefore into its
    fingerprint (ADR 0008 section 7).
    """
    canonical: dict[str, CanonicalFilterValue] = {}
    for key, raw_value in filters.items():
        if key in _EXCLUDED_FILTER_KEYS:
            raise FilterValidationError(f"{key!r} must not be a bound filter")
        canonical[key] = _canonical_filter_value(raw_value)
    for key, flag in (hidden or {}).items():
        if key in _EXCLUDED_FILTER_KEYS:
            raise FilterValidationError(f"{key!r} must not be a hidden constraint")
        if not isinstance(flag, bool):
            raise FilterValidationError("hidden constraint values must be booleans")
        if key in canonical:
            raise FilterValidationError(f"hidden constraint {key!r} collides with a request filter")
        canonical[key] = flag
    items = tuple(sorted(canonical.items(), key=lambda entry: entry[0]))
    return CanonicalFilters(resource=resource, sort=sort, version=version, items=items)


# --- Cursor payload -------------------------------------------------


class CursorKey(BaseModel):
    """The last returned row's ordering position: the canonical sort value and
    the row's UUID v7 (ADR 0008 sections 4 and 6).

    ``id`` is the shared :data:`Uuid7Id` boundary (ADR 0007): the Python
    attribute is a ``uuid.UUID``, a non-v7 or malformed value is rejected at
    model validation, and ``model_dump(mode="json")`` emits the canonical
    lowercase hyphenated form regardless of input casing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    t: str
    id: Uuid7Id

    @field_validator("t")
    @classmethod
    def _validate_sort_value(cls, value: str) -> str:
        if _CANONICAL_TIMESTAMP_RE.fullmatch(value):
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError as exc:
                raise ValueError("cursor sort value is not a valid timestamp") from exc
            return value
        if _CALENDAR_DATE_RE.fullmatch(value):
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("cursor sort value is not a valid date") from exc
            return value
        raise ValueError("cursor sort value is not a canonical timestamp or date")


class CursorPayload(BaseModel):
    """The full opaque cursor contents. Note the absence of ``limit`` — page
    size is never encoded or fingerprinted (ADR 0008 sections 3 and 6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    v: int = Field(ge=1, strict=True)
    r: str
    s: str
    f: str
    k: CursorKey

    @field_validator("r", "s")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("cursor binding field must not be empty")
        return value

    @field_validator("f")
    @classmethod
    def _validate_fingerprint(cls, value: str) -> str:
        if not _SHA256_HEX_RE.fullmatch(value):
            raise ValueError("cursor filter fingerprint is malformed")
        return value


def build_cursor_payload(
    *,
    filters: CanonicalFilters,
    last_sort_value: datetime | date | str,
    last_id: uuid.UUID,
) -> CursorPayload:
    """Build the payload for the next page from the last **returned** row.

    ``last_sort_value`` may be an aware datetime, a date, or an
    already-canonical string. ``last_id`` is a ``uuid.UUID`` — a non-v7 or
    malformed value raises before any token is produced. The resource, sort
    identifier, version and filter fingerprint are all taken from ``filters``
    so the payload can never disagree with the fingerprint or be bound to the
    wrong context.

    Raises ``ValueError`` if ``filters.version`` is not the version this codec
    emits (:data:`CURSOR_VERSION`).
    """
    if filters.version != CURSOR_VERSION:
        raise ValueError(
            f"cannot build a cursor for filter version {filters.version!r}; "
            f"this codec emits version {CURSOR_VERSION}"
        )
    if isinstance(last_sort_value, datetime):
        sort_value = normalize_aware_datetime(last_sort_value)
    elif isinstance(last_sort_value, date):
        sort_value = normalize_calendar_date(last_sort_value)
    else:
        sort_value = last_sort_value
    return CursorPayload(
        v=filters.version,
        r=filters.resource,
        s=filters.sort,
        f=filters.fingerprint(),
        k=CursorKey(t=sort_value, id=last_id),
    )


# --- Codec ---------------------------------------------------------


def _canonical_json(obj: object) -> str:
    """Deterministic UTF-8 JSON: sorted keys, compact separators, no NaN/Inf,
    no incidental whitespace."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    """Strict, *canonical* URL-safe base64 without padding.

    Python's base64 decoder tolerates non-canonical aliases — a final character
    whose unused low bits are non-zero decodes to the same bytes as the
    canonical character. That would let an attacker mutate a token segment
    without changing what it decodes to. Guard against it by re-encoding the
    decoded bytes and requiring an exact round-trip.

    Raises ``ValueError`` on any non-alphabet character, an impossible length,
    or a non-canonical alias.
    """
    if not _B64URL_SEGMENT_RE.fullmatch(segment):
        raise ValueError("segment is not unpadded URL-safe base64")
    padded = segment + "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("segment is not valid base64") from exc
    if _b64url_encode(decoded) != segment:
        raise ValueError("segment is not canonical unpadded URL-safe base64")
    return decoded


class CursorCodec:
    """Encode and decode opaque, authenticated pagination cursors.

    The signing key is injected as ``bytes`` and never read from the
    environment; a key shorter than :data:`MIN_SIGNING_KEY_BYTES` is rejected
    as defence in depth (the configuration provider owns startup validation).
    The key is never stored in a way that ``repr`` or a log line can reveal.
    """

    __slots__ = ("_signing_key",)

    def __init__(self, signing_key: bytes) -> None:
        if not isinstance(signing_key, (bytes, bytearray)):
            raise TypeError("pagination cursor signing key must be bytes")
        if len(signing_key) < MIN_SIGNING_KEY_BYTES:
            raise ValueError(
                f"pagination cursor signing key must be at least {MIN_SIGNING_KEY_BYTES} bytes"
            )
        self._signing_key = bytes(signing_key)

    def __repr__(self) -> str:
        return "CursorCodec(signing_key=<redacted>)"

    def encode(self, payload: CursorPayload) -> str:
        """Serialize, sign, and encode ``payload`` as a two-segment token."""
        body = _canonical_json(payload.model_dump(mode="json")).encode("utf-8")
        signature = hmac.new(self._signing_key, body, hashlib.sha256).digest()
        token = f"{_b64url_encode(body)}.{_b64url_encode(signature)}"
        if len(token) > MAX_ENCODED_CURSOR_LENGTH:
            raise ValueError(
                "encoded pagination cursor exceeds the maximum length; "
                "the resource or sort identifier is too long"
            )
        return token

    def decode(self, token: str, *, filters: CanonicalFilters) -> CursorPayload:
        """Verify and decode ``token``, checking it is bound to this
        ``filters`` object's resource, sort and fingerprint.

        Every failure — oversized, malformed, bad signature, bad JSON,
        unknown/missing field, unsupported version, wrong binding, malformed
        or non-v7 UUID, unparseable sort value — raises
        :class:`InvalidCursorError` with the same generic message.
        """
        if not token or len(token) > MAX_ENCODED_CURSOR_LENGTH:
            raise InvalidCursorError

        parts = token.split(".")
        if len(parts) != 2:
            raise InvalidCursorError
        body_segment, signature_segment = parts

        try:
            body = _b64url_decode(body_segment)
            provided_signature = _b64url_decode(signature_segment)
        except ValueError:
            raise InvalidCursorError from None

        expected_signature = hmac.new(self._signing_key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise InvalidCursorError

        try:
            raw: Any = json.loads(body)
        except ValueError:
            raise InvalidCursorError from None
        if not isinstance(raw, dict):
            raise InvalidCursorError

        try:
            payload = CursorPayload.model_validate(raw)
        except ValidationError:
            raise InvalidCursorError from None

        if payload.v != CURSOR_VERSION or filters.version != CURSOR_VERSION:
            raise InvalidCursorError
        if payload.r != filters.resource or payload.s != filters.sort:
            raise InvalidCursorError
        if not hmac.compare_digest(payload.f, filters.fingerprint()):
            raise InvalidCursorError

        return payload

    def next_cursor(
        self,
        *,
        filters: CanonicalFilters,
        last_sort_value: datetime | date | str,
        last_id: uuid.UUID,
    ) -> str:
        """Convenience: :func:`build_cursor_payload` then :meth:`encode`."""
        return self.encode(
            build_cursor_payload(filters=filters, last_sort_value=last_sort_value, last_id=last_id)
        )


# --- Generic response envelope ---------------------------------------


class Page[T](BaseModel):
    """The one paginated-response envelope for every list endpoint.

    Serializes to exactly ``{"items": [...], "next_cursor": <string|null>}``
    and nothing else — no ``total``, ``page``, ``has_more`` or ``prev_cursor``
    (ADR 0008 section 2). ``next_cursor`` is always present and is ``null`` on
    the final page and on an empty result.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[T] = Field(default_factory=list)
    next_cursor: str | None = None
