"""Contract tests for the generic pagination envelope and cursor semantics
(ADR 0008 PR 3).

These protect the wire-level guarantees the documentation promises: the exact
``{items, next_cursor}`` envelope, the opaque signed cursor, and the fact that
``limit`` is never part of the cursor. They do not exercise any HTTP route —
that arrives with the first paginated endpoint PR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from ai_daily_digest.delivery.api.pagination import (
    CURSOR_VERSION,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    CursorCodec,
    CursorPayload,
    Page,
    build_cursor_payload,
    canonicalize_filters,
)
from tests.uuid_samples import SNAPSHOT_1

pytestmark = pytest.mark.contract

_CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "API_CONTRACT.md"
_SIGNING_KEY = b"contract-test-signing-key-32-bytes!!"


class _ExampleItem(BaseModel):
    id: str
    title: str


def test_generic_envelope_serializes_to_exactly_two_keys() -> None:
    page: Page[_ExampleItem] = Page(
        items=[_ExampleItem(id=str(SNAPSHOT_1), title="Example")],
        next_cursor="b2xkLXN0eWxlLW9wYXF1ZS10b2tlbg.c2ln",
    )
    dumped = page.model_dump(mode="json")
    assert list(dumped) == ["items", "next_cursor"]
    assert dumped["items"] == [{"id": str(SNAPSHOT_1), "title": "Example"}]
    assert isinstance(dumped["next_cursor"], str)


def test_final_and_empty_pages_use_a_null_next_cursor() -> None:
    assert Page[_ExampleItem]().model_dump(mode="json") == {"items": [], "next_cursor": None}
    final: Page[_ExampleItem] = Page(items=[_ExampleItem(id=str(SNAPSHOT_1), title="x")])
    assert final.model_dump(mode="json")["next_cursor"] is None


def test_envelope_schema_forbids_extra_pagination_metadata() -> None:
    schema = Page[_ExampleItem].model_json_schema()
    assert set(schema["properties"]) == {"items", "next_cursor"}
    assert schema["additionalProperties"] is False
    for forbidden in ("total", "total_count", "page", "has_more", "prev_cursor"):
        assert forbidden not in schema["properties"]


def test_cursor_is_an_opaque_versioned_string_that_round_trips() -> None:
    codec = CursorCodec(_SIGNING_KEY)
    filters = canonicalize_filters(
        resource="updates",
        sort="first_fetched_at:desc,id:desc",
        filters={"publisher": "OpenAI"},
    )
    token = codec.next_cursor(
        filters=filters,
        last_sort_value=datetime(2026, 9, 2, 9, 5, 0, tzinfo=UTC),
        last_id=SNAPSHOT_1,
    )
    assert isinstance(token, str)
    assert "OpenAI" not in token  # filters are fingerprinted, never embedded verbatim
    restored = codec.decode(token, filters=filters)
    assert restored.v == CURSOR_VERSION
    assert restored.r == "updates"
    # ADR 0007: the ID travels as a uuid.UUID and serializes canonical lowercase.
    assert restored.k.id == SNAPSHOT_1
    assert restored.model_dump(mode="json")["k"]["id"] == str(SNAPSHOT_1).lower()


def test_limit_is_not_part_of_the_cursor_contract() -> None:
    assert "limit" not in CursorPayload.model_json_schema()["properties"]
    filters = canonicalize_filters(
        resource="updates", sort="first_fetched_at:desc,id:desc", filters={}
    )
    payload = build_cursor_payload(
        filters=filters, last_sort_value="2026-09-02T09:05:00.000000Z", last_id=SNAPSHOT_1
    )
    assert "limit" not in payload.model_dump()


def test_contract_document_records_the_generic_pagination_rules() -> None:
    text = _CONTRACT.read_text(encoding="utf-8")
    assert "## Pagination" in text
    for token in (
        "keyset",
        "next_cursor",
        f"{DEFAULT_LIMIT}",
        f"{MIN_LIMIT}",
        f"{MAX_LIMIT}",
        "invalid_cursor",
        "[from, to)",
        "HMAC-SHA256",
        "first_fetched_at:desc,id:desc",
    ):
        assert token in text, f"docs/API_CONTRACT.md is missing pagination detail: {token!r}"
