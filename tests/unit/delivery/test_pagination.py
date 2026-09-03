"""Unit tests for the pure pagination cursor codec (ADR 0008 PR 3).

No FastAPI, no database, no network, no wall-clock time. Every datetime used
here is an explicit constant.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from ai_daily_digest.delivery.api import pagination
from ai_daily_digest.delivery.api.pagination import (
    CURSOR_VERSION,
    MAX_ENCODED_CURSOR_LENGTH,
    MIN_SIGNING_KEY_BYTES,
    CanonicalFilters,
    CursorCodec,
    CursorKey,
    CursorPayload,
    FilterValidationError,
    HalfOpenRange,
    InvalidCursorError,
    Page,
    RangeValidationError,
    build_cursor_payload,
    canonicalize_filters,
    normalize_aware_datetime,
    normalize_calendar_date,
    normalize_filter_string,
    validate_half_open_range,
)
from tests.uuid_samples import SNAPSHOT_1

SIGNING_KEY = b"\x11" * MIN_SIGNING_KEY_BYTES
OTHER_KEY = b"\x22" * MIN_SIGNING_KEY_BYTES
VALID_V7 = SNAPSHOT_1  # a real UUID v7 (uuid.UUID), from the shared frozen samples
V4_ID = "4e2b4d9a-0c1f-4b6e-9d3a-1f2e3c4d5b6a"  # a syntactically valid UUID v4
V4_UUID = uuid.UUID(V4_ID)
UPPERCASE_V7 = "0192F0C4-1A2B-7C3D-8E4F-2B1C0D9E8F7A"  # parseable v7, non-canonical casing
TS = datetime(2026, 9, 2, 10, 0, 0, 123456, tzinfo=UTC)
CANONICAL_TS = "2026-09-02T10:00:00.123456Z"

UPDATES_SORT = "first_fetched_at:desc,id:desc"
DIGESTS_SORT = "digest_date:desc,id:desc"


def _updates_filters(**overrides: Any) -> CanonicalFilters:
    params: dict[str, Any] = {
        "resource": "updates",
        "sort": UPDATES_SORT,
        "filters": {"publisher": "OpenAI"},
    }
    params.update(overrides)
    return canonicalize_filters(**params)


def _payload(**overrides: Any) -> CursorPayload:
    filters = overrides.pop("filters", _updates_filters())
    return build_cursor_payload(
        filters=filters,
        last_sort_value=overrides.pop("last_sort_value", CANONICAL_TS),
        last_id=overrides.pop("last_id", VALID_V7),
        **overrides,
    )


def _forge(body: dict[str, Any] | bytes, *, key: bytes = SIGNING_KEY) -> str:
    """Craft a token from an arbitrary body — used to build deliberately
    malformed payloads that the strict models would refuse to construct."""
    body_bytes = (
        body if isinstance(body, bytes) else pagination._canonical_json(body).encode("utf-8")
    )
    signature = hmac.new(key, body_bytes, hashlib.sha256).digest()
    return f"{pagination._b64url_encode(body_bytes)}.{pagination._b64url_encode(signature)}"


# --------------------------------------------------------------------------
# Round trip and canonical encoding
# --------------------------------------------------------------------------


def test_deterministic_encode_decode_round_trip() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    payload = _payload(filters=filters)

    token = codec.encode(payload)
    restored = codec.decode(token, filters=filters)

    assert restored == payload
    assert restored.k.t == CANONICAL_TS
    assert restored.k.id == VALID_V7
    assert isinstance(restored.k.id, uuid.UUID)
    assert restored.v == CURSOR_VERSION


def test_canonical_encoding_is_byte_identical() -> None:
    codec = CursorCodec(SIGNING_KEY)
    payload = _payload()
    assert codec.encode(payload) == codec.encode(payload)


def test_encoded_token_is_url_safe_and_unpadded() -> None:
    token = CursorCodec(SIGNING_KEY).encode(_payload())
    assert "=" not in token
    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.")
    assert token.count(".") == 1


def test_datetime_microseconds_preserved_exactly() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    precise = datetime(2026, 9, 2, 10, 0, 0, 1, tzinfo=UTC)  # 1 microsecond
    payload = build_cursor_payload(filters=filters, last_sort_value=precise, last_id=VALID_V7)

    restored = codec.decode(codec.encode(payload), filters=filters)

    assert restored.k.t == "2026-09-02T10:00:00.000001Z"


def test_calendar_date_round_trips() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = canonicalize_filters(resource="digests", sort=DIGESTS_SORT, filters={})
    payload = build_cursor_payload(
        filters=filters, last_sort_value=date(2026, 8, 20), last_id=VALID_V7
    )

    restored = codec.decode(codec.encode(payload), filters=filters)

    assert restored.k.t == "2026-08-20"


# --------------------------------------------------------------------------
# Tampering and malformed tokens
# --------------------------------------------------------------------------


def _flip_char(text: str, index: int) -> str:
    replacement = "A" if text[index] != "A" else "B"
    return text[:index] + replacement + text[index + 1 :]


def test_payload_tampering_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    token = codec.encode(_payload(filters=filters))
    body_segment = token.split(".")[0]
    tampered = _flip_char(body_segment, 5) + "." + token.split(".")[1]

    with pytest.raises(InvalidCursorError):
        codec.decode(tampered, filters=filters)


def test_signature_tampering_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    body_segment, signature_segment = codec.encode(_payload(filters=filters)).split(".")
    tampered = body_segment + "." + _flip_char(signature_segment, 3)

    with pytest.raises(InvalidCursorError):
        codec.decode(tampered, filters=filters)


def test_wrong_signing_key_is_rejected() -> None:
    filters = _updates_filters()
    token = CursorCodec(SIGNING_KEY).encode(_payload(filters=filters))

    with pytest.raises(InvalidCursorError):
        CursorCodec(OTHER_KEY).decode(token, filters=filters)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "onlyonesegment",
        "too.many.segments",
        "a.",
        ".b",
        "not$base64.aGVsbG8",
        "aGVsbG8.not$base64",
    ],
)
def test_structurally_malformed_tokens_are_rejected(token: str) -> None:
    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(token, filters=_updates_filters())


def test_truncated_token_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    token = codec.encode(_payload(filters=filters))

    with pytest.raises(InvalidCursorError):
        codec.decode(token[: len(token) // 2], filters=filters)


def test_validly_signed_but_non_json_body_is_rejected() -> None:
    token = _forge(b"this is not json")
    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(token, filters=_updates_filters())


def test_validly_signed_non_object_json_is_rejected() -> None:
    token = _forge(b"[1, 2, 3]")
    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(token, filters=_updates_filters())


def test_oversized_token_rejected_before_any_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_segment: str) -> bytes:
        raise AssertionError("decoding must not be attempted for an oversized token")

    monkeypatch.setattr(pagination, "_b64url_decode", _boom)
    oversized = "A" * (MAX_ENCODED_CURSOR_LENGTH + 1)

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(oversized, filters=_updates_filters())


def test_encode_rejects_a_payload_that_would_exceed_the_maximum_length() -> None:
    filters = canonicalize_filters(resource="updates", sort="s" * 600, filters={})
    payload = build_cursor_payload(filters=filters, last_sort_value=CANONICAL_TS, last_id=VALID_V7)

    with pytest.raises(ValueError, match="maximum length"):
        CursorCodec(SIGNING_KEY).encode(payload)


# --------------------------------------------------------------------------
# Strict payload validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["v", "r", "s", "f", "k"])
def test_missing_payload_field_is_rejected(missing: str) -> None:
    body = json.loads(pagination._canonical_json(_payload().model_dump(mode="json")))
    del body[missing]

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(_forge(body), filters=_updates_filters())


def test_unknown_payload_field_is_rejected() -> None:
    body = _payload().model_dump(mode="json")
    body["extra"] = "nope"

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(_forge(body), filters=_updates_filters())


def test_unknown_nested_key_field_is_rejected() -> None:
    body = _payload().model_dump(mode="json")
    body["k"]["extra"] = "nope"

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(_forge(body), filters=_updates_filters())


def test_unsupported_cursor_version_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    future = CursorPayload(
        v=CURSOR_VERSION + 1,
        r="updates",
        s=UPDATES_SORT,
        f=filters.fingerprint(),
        k=CursorKey(t=CANONICAL_TS, id=VALID_V7),
    )

    with pytest.raises(InvalidCursorError):
        codec.decode(codec.encode(future), filters=filters)


def test_resource_mismatch_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    token = codec.encode(_payload(filters=_updates_filters()))
    changes_filters = canonicalize_filters(
        resource="changes", sort=UPDATES_SORT, filters={"publisher": "OpenAI"}
    )

    with pytest.raises(InvalidCursorError):
        codec.decode(token, filters=changes_filters)


def test_sort_mismatch_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    token = codec.encode(_payload(filters=_updates_filters()))
    other_sort = canonicalize_filters(
        resource="updates", sort="published_at:desc,id:desc", filters={"publisher": "OpenAI"}
    )

    with pytest.raises(InvalidCursorError):
        codec.decode(token, filters=other_sort)


def test_filter_fingerprint_mismatch_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    token = codec.encode(_payload(filters=_updates_filters(filters={"publisher": "OpenAI"})))
    changed = _updates_filters(filters={"publisher": "Anthropic"})

    with pytest.raises(InvalidCursorError):
        codec.decode(token, filters=changed)


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "", "0192f0c4", V4_ID])
def test_malformed_or_non_v7_cursor_id_is_rejected(bad_id: str) -> None:
    body = _payload().model_dump(mode="json")
    body["k"]["id"] = bad_id

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(_forge(body), filters=_updates_filters())


@pytest.mark.parametrize(
    "bad_t",
    [
        "2026-13-40T00:00:00.000000Z",  # timestamp shape, impossible month/day
        "2026-09-02T10:00:00Z",  # seconds precision, not microseconds
        "2026-09-02T10:00:00.123Z",  # milliseconds, not microseconds
        "2026-09-02 10:00:00.000000Z",  # space separator
        "2026-13-40",  # date shape, impossible month/day
        "2026-9-2",  # unpadded date
        "not-a-timestamp",
    ],
)
def test_non_canonical_cursor_sort_value_is_rejected(bad_t: str) -> None:
    body = _payload().model_dump(mode="json")
    body["k"]["t"] = bad_t

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(_forge(body), filters=_updates_filters())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("r", ""),
        ("s", ""),
        ("f", "not-sixty-four-hex"),
        ("f", "A" * 64),  # uppercase hex is not the canonical lowercase digest
        ("v", 0),
    ],
)
def test_structurally_invalid_payload_fields_are_rejected(field: str, value: object) -> None:
    body = _payload().model_dump(mode="json")
    body[field] = value

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(_forge(body), filters=_updates_filters())


# --------------------------------------------------------------------------
# Signing key handling and secret hygiene
# --------------------------------------------------------------------------


@pytest.mark.parametrize("length", [0, 1, MIN_SIGNING_KEY_BYTES - 1])
def test_signing_key_shorter_than_minimum_is_rejected(length: int) -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        CursorCodec(b"\x00" * length)


def test_signing_key_must_be_bytes() -> None:
    with pytest.raises(TypeError):
        CursorCodec("a-string-key-that-is-definitely-long-enough")  # type: ignore[arg-type]


def test_repr_never_reveals_the_signing_key() -> None:
    codec = CursorCodec(b"S3CR3T-KEY-" + b"padding" * 4)
    assert "S3CR3T" not in repr(codec)
    assert repr(codec) == "CursorCodec(signing_key=<redacted>)"


def test_invalid_cursor_error_is_generic_and_leaks_nothing() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    body = _payload().model_dump(mode="json")
    body["k"]["id"] = "leak-me-not"
    token = _forge(body)

    with pytest.raises(InvalidCursorError) as exc_info:
        codec.decode(token, filters=filters)

    error = exc_info.value
    rendered = f"{error} {error!r}"
    assert str(error) == "The pagination cursor is invalid for this request."
    assert "leak-me-not" not in rendered
    assert token not in rendered
    assert SIGNING_KEY.hex() not in rendered
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_decode_uses_constant_time_signature_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real_compare = hmac.compare_digest

    def _spy(a: object, b: object) -> bool:
        calls.append(1)
        return bool(real_compare(a, b))  # type: ignore[call-overload]

    monkeypatch.setattr(hmac, "compare_digest", _spy)
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    token = codec.encode(_payload(filters=filters))
    codec.decode(token, filters=filters)

    assert calls, "decode() must verify the signature with hmac.compare_digest"


# --------------------------------------------------------------------------
# Canonical filters
# --------------------------------------------------------------------------


def test_filter_key_order_does_not_change_the_fingerprint() -> None:
    a = canonicalize_filters(
        resource="changes",
        sort="detected_at:desc,id:desc",
        filters={"company": "OpenAI", "product": "GPT-4o", "field": "context_window_tokens"},
    )
    b = canonicalize_filters(
        resource="changes",
        sort="detected_at:desc,id:desc",
        filters={"field": "context_window_tokens", "product": "GPT-4o", "company": "OpenAI"},
    )
    assert a.fingerprint() == b.fingerprint()
    assert a == b


def test_changing_a_bound_filter_changes_the_fingerprint() -> None:
    base = _updates_filters(filters={"publisher": "OpenAI"})
    assert base.fingerprint() != _updates_filters(filters={"publisher": "Anthropic"}).fingerprint()
    assert (
        base.fingerprint()
        != _updates_filters(filters={"publisher": "OpenAI", "source_id": "x"}).fingerprint()
    )


def test_resource_sort_and_version_are_part_of_the_fingerprint() -> None:
    base = _updates_filters()
    assert base.fingerprint() != _updates_filters(resource="changes").fingerprint()
    assert base.fingerprint() != _updates_filters(sort="other:desc,id:desc").fingerprint()
    assert base.fingerprint() != _updates_filters(version=CURSOR_VERSION + 1).fingerprint()


def test_absent_filter_is_distinct_from_an_explicit_null() -> None:
    absent = canonicalize_filters(resource="updates", sort=UPDATES_SORT, filters={})
    explicit_null = canonicalize_filters(
        resource="updates", sort=UPDATES_SORT, filters={"publisher": None}
    )
    assert absent.fingerprint() != explicit_null.fingerprint()
    assert "publisher" not in absent.as_dict()
    assert explicit_null.as_dict()["publisher"] is None


def test_surrounding_whitespace_is_trimmed_before_fingerprinting() -> None:
    trimmed = _updates_filters(filters={"publisher": "OpenAI"})
    padded = _updates_filters(filters={"publisher": "  OpenAI \t"})
    assert trimmed.fingerprint() == padded.fingerprint()
    assert padded.as_dict()["publisher"] == "OpenAI"


def test_nfc_equivalent_filter_values_are_identical() -> None:
    composed = _updates_filters(filters={"publisher": "Café"})  # é as one code point
    decomposed = _updates_filters(filters={"publisher": "Café"})  # e + combining acute
    assert composed.fingerprint() == decomposed.fingerprint()
    assert composed.as_dict()["publisher"] == decomposed.as_dict()["publisher"]


def test_case_different_filter_values_stay_distinct() -> None:
    assert (
        _updates_filters(filters={"publisher": "openai"}).fingerprint()
        != _updates_filters(filters={"publisher": "OpenAI"}).fingerprint()
    )


@pytest.mark.parametrize(
    "value", ["\U0001f680 rocket", "\u202eRTL override", "a\u0301 combining", "  \t  "]
)
def test_unusual_unicode_filter_values_are_handled_deterministically(value: str) -> None:
    first = _updates_filters(filters={"publisher": value})
    second = _updates_filters(filters={"publisher": value})
    assert first.fingerprint() == second.fingerprint()
    assert first.as_dict() == second.as_dict()


def test_hidden_constraints_are_folded_into_the_fingerprint() -> None:
    without = canonicalize_filters(resource="digests", sort=DIGESTS_SORT, filters={})
    with_flag = canonicalize_filters(
        resource="digests", sort=DIGESTS_SORT, filters={}, hidden={"published_only": True}
    )
    assert without.fingerprint() != with_flag.fingerprint()
    assert with_flag.as_dict()["published_only"] is True


def test_hidden_constraint_true_and_false_differ() -> None:
    on = canonicalize_filters(
        resource="digests", sort=DIGESTS_SORT, filters={}, hidden={"published_only": True}
    )
    off = canonicalize_filters(
        resource="digests", sort=DIGESTS_SORT, filters={}, hidden={"published_only": False}
    )
    assert on.fingerprint() != off.fingerprint()


def test_aware_datetime_filter_normalized_to_utc_with_microseconds() -> None:
    plus_two = timezone(timedelta(hours=2))
    local = _updates_filters(
        filters={"since": datetime(2026, 9, 2, 12, 0, 0, 500, tzinfo=plus_two)}
    )
    utc = _updates_filters(filters={"since": datetime(2026, 9, 2, 10, 0, 0, 500, tzinfo=UTC)})
    assert local.fingerprint() == utc.fingerprint()
    assert local.as_dict()["since"] == "2026-09-02T10:00:00.000500Z"


def test_naive_datetime_filter_is_rejected() -> None:
    with pytest.raises(FilterValidationError):
        _updates_filters(filters={"since": datetime(2026, 9, 2, 10, 0, 0)})


def test_date_filter_is_serialized_as_iso_date() -> None:
    filters = canonicalize_filters(
        resource="digests", sort=DIGESTS_SORT, filters={"date_from": date(2026, 8, 20)}
    )
    assert filters.as_dict()["date_from"] == "2026-08-20"


def test_cursor_and_limit_are_rejected_as_bound_filters() -> None:
    for forbidden in ("cursor", "limit"):
        with pytest.raises(FilterValidationError, match="must not be a bound filter"):
            _updates_filters(filters={forbidden: "20"})


def test_float_filter_values_are_rejected() -> None:
    with pytest.raises(FilterValidationError, match="unsupported filter value type"):
        _updates_filters(filters={"score": 1.5})


def test_hidden_constraint_colliding_with_a_request_filter_is_rejected() -> None:
    with pytest.raises(FilterValidationError, match="collides"):
        canonicalize_filters(
            resource="digests",
            sort=DIGESTS_SORT,
            filters={"published_only": "x"},
            hidden={"published_only": True},
        )


def test_non_boolean_hidden_constraint_value_is_rejected() -> None:
    with pytest.raises(FilterValidationError, match="must be booleans"):
        canonicalize_filters(
            resource="digests",
            sort=DIGESTS_SORT,
            filters={},
            hidden={"published_only": "yes"},  # type: ignore[dict-item]
        )


def test_canonical_filters_as_dict_is_a_detached_copy() -> None:
    filters = _updates_filters()
    filters.as_dict()["publisher"] = "mutated"
    assert filters.as_dict()["publisher"] == "OpenAI"


# --------------------------------------------------------------------------
# Normalization helpers (direct)
# --------------------------------------------------------------------------


def test_normalize_filter_string_trims_and_nfc_normalizes() -> None:
    assert normalize_filter_string("  OpenAI  ") == "OpenAI"
    assert normalize_filter_string("Café") == "Café"


def test_normalize_aware_datetime_rejects_naive() -> None:
    with pytest.raises(FilterValidationError):
        normalize_aware_datetime(datetime(2026, 9, 2, 10, 0, 0))


def test_normalize_aware_datetime_keeps_microseconds_and_converts_to_utc() -> None:
    value = datetime(2026, 9, 2, 12, 30, 0, 7, tzinfo=timezone(timedelta(hours=2, minutes=30)))
    assert normalize_aware_datetime(value) == "2026-09-02T10:00:00.000007Z"


def test_normalize_calendar_date_variants() -> None:
    assert normalize_calendar_date(date(2026, 8, 20)) == "2026-08-20"
    assert normalize_calendar_date(" 2026-08-20 ") == "2026-08-20"
    with pytest.raises(FilterValidationError):
        normalize_calendar_date("2026-13-40")
    with pytest.raises(FilterValidationError):
        normalize_calendar_date(datetime(2026, 8, 20, tzinfo=UTC))


# --------------------------------------------------------------------------
# Half-open range validation
# --------------------------------------------------------------------------


def test_valid_ranges_are_accepted() -> None:
    lo, hi = date(2026, 1, 1), date(2026, 2, 1)
    assert validate_half_open_range(lo, hi) == HalfOpenRange(lower=lo, upper=hi)
    assert validate_half_open_range(lo, None).upper is None
    assert validate_half_open_range(None, hi).lower is None
    assert validate_half_open_range(None, None) == HalfOpenRange(lower=None, upper=None)


def test_datetime_range_is_supported() -> None:
    lo = datetime(2026, 1, 1, tzinfo=UTC)
    hi = datetime(2026, 1, 2, tzinfo=UTC)
    assert validate_half_open_range(lo, hi).lower == lo


@pytest.mark.parametrize(
    ("lower", "upper"),
    [
        (date(2026, 1, 1), date(2026, 1, 1)),  # equal
        (date(2026, 2, 1), date(2026, 1, 1)),  # reversed
    ],
)
def test_equal_or_reversed_range_bounds_are_rejected(lower: date, upper: date) -> None:
    with pytest.raises(RangeValidationError):
        validate_half_open_range(lower, upper)


# --------------------------------------------------------------------------
# Page[T] and limit independence
# --------------------------------------------------------------------------


def test_cursor_payload_has_no_limit_field() -> None:
    assert "limit" not in CursorPayload.model_fields
    assert "limit" not in CursorKey.model_fields


def test_changing_limit_does_not_affect_the_cursor() -> None:
    # `limit` is neither in the payload nor the fingerprint, so a cursor issued
    # for one page size decodes unchanged for another.
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    token = codec.encode(_payload(filters=filters))
    # Simulating a later request with a different limit changes nothing here:
    assert codec.decode(token, filters=filters) == codec.decode(token, filters=filters)
    assert "limit" not in filters.as_dict()


class _Item(BaseModel):
    id: str


def test_page_serializes_to_exactly_items_and_next_cursor() -> None:
    page: Page[_Item] = Page(items=[_Item(id="a")], next_cursor="opaque-token")
    assert page.model_dump(mode="json") == {
        "items": [{"id": "a"}],
        "next_cursor": "opaque-token",
    }


def test_empty_page_has_a_null_next_cursor() -> None:
    page: Page[_Item] = Page()
    assert page.model_dump(mode="json") == {"items": [], "next_cursor": None}


def test_page_never_carries_pagination_metadata_fields() -> None:
    keys = set(Page[_Item].model_json_schema()["properties"])
    assert keys == {"items", "next_cursor"}
    assert Page[_Item].model_json_schema().get("additionalProperties") is False
    with pytest.raises(ValidationError):
        Page[_Item](items=[], total=3)  # type: ignore[call-arg]


# ==========================================================================
# Independent-review regression tests
# ==========================================================================

# --- Finding 1: outbound cursors can never contain an invalid ID ---------


def test_cursor_key_id_is_a_uuid_and_serializes_to_canonical_lowercase() -> None:
    key = CursorKey(t=CANONICAL_TS, id=UPPERCASE_V7)  # type: ignore[arg-type]
    assert isinstance(key.id, uuid.UUID)
    assert key.model_dump(mode="json")["id"] == UPPERCASE_V7.lower()


def test_build_cursor_payload_rejects_a_uuid_v4() -> None:
    with pytest.raises(ValidationError):
        build_cursor_payload(
            filters=_updates_filters(), last_sort_value=CANONICAL_TS, last_id=V4_UUID
        )


def test_build_cursor_payload_rejects_a_malformed_uuid() -> None:
    with pytest.raises(ValidationError):
        build_cursor_payload(
            filters=_updates_filters(),
            last_sort_value=CANONICAL_TS,
            last_id="not-a-uuid",  # type: ignore[arg-type]
        )


def test_next_cursor_cannot_emit_an_invalid_id() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    for bad in (V4_UUID, "not-a-uuid"):
        with pytest.raises(ValidationError):
            codec.next_cursor(filters=filters, last_sort_value=CANONICAL_TS, last_id=bad)  # type: ignore[arg-type]


def test_uppercase_uuid_v7_round_trips_as_canonical_lowercase() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    payload = build_cursor_payload(
        filters=filters,
        last_sort_value=CANONICAL_TS,
        last_id=uuid.UUID(UPPERCASE_V7),
    )
    token = codec.encode(payload)
    assert UPPERCASE_V7 not in token
    restored = codec.decode(token, filters=filters)
    assert restored.k.id == uuid.UUID(UPPERCASE_V7)
    assert restored.model_dump(mode="json")["k"]["id"] == UPPERCASE_V7.lower()


def test_a_cursor_emitted_by_the_codec_always_decodes_in_the_same_codec() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    token = codec.next_cursor(filters=filters, last_sort_value=TS, last_id=VALID_V7)
    assert codec.decode(token, filters=filters).k.id == VALID_V7


# --- Finding 2: non-canonical base64 aliases are rejected ----------------

_B64URL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _non_canonical_alias(segment: str) -> str | None:
    """Return a segment that base64-decodes to the *same* bytes as ``segment``
    but differs in its final character (a non-canonical alias), or ``None`` if
    the final character has no slack bits."""
    padded = segment + "=" * (-len(segment) % 4)
    target = base64.urlsafe_b64decode(padded)
    for char in _B64URL_ALPHABET:
        if char == segment[-1]:
            continue
        candidate = segment[:-1] + char
        cand_padded = candidate + "=" * (-len(candidate) % 4)
        try:
            if base64.urlsafe_b64decode(cand_padded) == target:
                return candidate
        except (binascii.Error, ValueError):
            continue
    return None


def test_non_canonical_signature_segment_alias_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    body_segment, signature_segment = codec.encode(_payload(filters=filters)).split(".")
    alias = _non_canonical_alias(signature_segment)
    assert alias is not None and alias != signature_segment
    # The alias decodes to the identical signature bytes, so without the
    # canonical-encoding guard it would pass verification:
    assert base64.urlsafe_b64decode(alias + "=" * (-len(alias) % 4)) == base64.urlsafe_b64decode(
        signature_segment + "=" * (-len(signature_segment) % 4)
    )
    with pytest.raises(InvalidCursorError):
        codec.decode(f"{body_segment}.{alias}", filters=filters)


def test_non_canonical_body_segment_alias_is_rejected() -> None:
    codec = CursorCodec(SIGNING_KEY)
    for suffix in range(3):  # cycles the body byte length through all residues mod 3
        filters = _updates_filters(resource="updates" + "x" * suffix)
        body_segment = codec.encode(_payload(filters=filters)).split(".")[0]
        alias = _non_canonical_alias(body_segment)
        if alias is None:
            continue
        decoded_body = base64.urlsafe_b64decode(body_segment + "=" * (-len(body_segment) % 4))
        assert base64.urlsafe_b64decode(alias + "=" * (-len(alias) % 4)) == decoded_body
        # Re-sign so the HMAC over the (identical) decoded body still verifies —
        # only the canonical-encoding guard can catch this:
        signature = hmac.new(SIGNING_KEY, decoded_body, hashlib.sha256).digest()
        aliased_token = f"{alias}.{pagination._b64url_encode(signature)}"
        with pytest.raises(InvalidCursorError):
            codec.decode(aliased_token, filters=filters)
        return
    pytest.fail("expected at least one candidate body length to have a slack-bit alias")


def test_ordinary_encoded_token_still_passes_the_canonical_base64_guard() -> None:
    codec = CursorCodec(SIGNING_KEY)
    filters = _updates_filters()
    token = codec.encode(_payload(filters=filters))
    assert codec.decode(token, filters=filters) == _payload(filters=filters)


# --- Finding 3: strict, consistent cursor version -----------------------


@pytest.mark.parametrize("bad_v", [True, 1.0, "1"])
def test_signed_payload_with_a_non_int_version_is_rejected(bad_v: object) -> None:
    body = _payload().model_dump(mode="json")
    body["v"] = bad_v

    with pytest.raises(InvalidCursorError):
        CursorCodec(SIGNING_KEY).decode(_forge(body), filters=_updates_filters())


def test_cursor_payload_v_field_is_strict() -> None:
    for bad_v in (True, 1.0, "1"):
        with pytest.raises(ValidationError):
            CursorPayload(
                v=bad_v,  # type: ignore[arg-type]
                r="updates",
                s=UPDATES_SORT,
                f=_updates_filters().fingerprint(),
                k=CursorKey(t=CANONICAL_TS, id=VALID_V7),
            )


def test_build_cursor_payload_refuses_an_unsupported_filter_version() -> None:
    future_filters = _updates_filters(version=CURSOR_VERSION + 1)
    with pytest.raises(ValueError, match="version"):
        build_cursor_payload(filters=future_filters, last_sort_value=CANONICAL_TS, last_id=VALID_V7)
    with pytest.raises(ValueError, match="version"):
        CursorCodec(SIGNING_KEY).next_cursor(
            filters=future_filters, last_sort_value=CANONICAL_TS, last_id=VALID_V7
        )


def test_build_cursor_payload_version_matches_filters_and_constant() -> None:
    payload = build_cursor_payload(
        filters=_updates_filters(), last_sort_value=CANONICAL_TS, last_id=VALID_V7
    )
    assert payload.v == CURSOR_VERSION == _updates_filters().version


def test_decode_rejects_a_payload_filter_version_mismatch() -> None:
    codec = CursorCodec(SIGNING_KEY)
    token = codec.encode(_payload(filters=_updates_filters()))
    mismatched = _updates_filters(version=CURSOR_VERSION + 1)

    with pytest.raises(InvalidCursorError):
        codec.decode(token, filters=mismatched)


def test_cursor_version_is_in_the_filter_fingerprint() -> None:
    assert (
        _updates_filters(version=CURSOR_VERSION).fingerprint()
        != _updates_filters(version=CURSOR_VERSION + 1).fingerprint()
    )


# --- Finding 4: cursor / limit are never hidden constraints -------------


@pytest.mark.parametrize("forbidden", ["cursor", "limit"])
def test_hidden_constraint_may_not_be_a_reserved_key(forbidden: str) -> None:
    with pytest.raises(FilterValidationError, match="must not be a hidden constraint"):
        canonicalize_filters(
            resource="updates",
            sort=UPDATES_SORT,
            filters={},
            hidden={forbidden: True},
        )


def test_legitimate_hidden_published_only_still_works() -> None:
    filters = canonicalize_filters(
        resource="digests", sort=DIGESTS_SORT, filters={}, hidden={"published_only": True}
    )
    assert filters.as_dict()["published_only"] is True
