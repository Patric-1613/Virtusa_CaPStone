"""Tests for the shared retry/validation wrapper every LLM call site
depends on. Every other test file that touches this behavior explicitly
defers coverage here (see e.g. test_resolve_llm.py's module docstring) --
this is that file. `_client()` is monkeypatched with a fake Anthropic
client so nothing here needs a real API key or network access."""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import BaseModel

from ai_daily_digest.intelligence import llm


class _Response(BaseModel):
    value: str


class _FakeBlock:
    def __init__(self, text: str, block_type: str = "text") -> None:
        self.text = text
        self.type = block_type


class _FakeMessage:
    def __init__(self, content: list[_FakeBlock]) -> None:
        self.content = content


class _FakeMessages:
    """Queues up canned raw-text responses, one per call to .create()."""

    def __init__(self, raw_texts: list[str]) -> None:
        self._raw_texts = list(raw_texts)
        self.calls = 0

    def create(self, **_kwargs: object) -> _FakeMessage:
        self.calls += 1
        return _FakeMessage([_FakeBlock(self._raw_texts.pop(0))])


class _FakeClient:
    def __init__(self, raw_texts: list[str]) -> None:
        self.messages = _FakeMessages(raw_texts)


def _patch_client(monkeypatch: pytest.MonkeyPatch, raw_texts: list[str]) -> _FakeClient:
    fake = _FakeClient(raw_texts)
    monkeypatch.setattr(llm, "_client", lambda: fake)
    return fake


def test_succeeds_on_first_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_client(monkeypatch, ['{"value": "ok"}'])
    result = llm.call_structured(
        model=llm.HAIKU, system="sys", prompt="p", response_model=_Response
    )
    assert result.value == "ok"
    assert fake.messages.calls == 1


def test_retries_once_on_malformed_json_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_client(monkeypatch, ["not json at all", '{"value": "ok"}'])
    result = llm.call_structured(
        model=llm.HAIKU, system="sys", prompt="p", response_model=_Response
    )
    assert result.value == "ok"
    assert fake.messages.calls == 2


def test_retries_once_on_schema_validation_failure_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # valid JSON, but missing the required "value" field the first time
    fake = _patch_client(monkeypatch, ["{}", '{"value": "ok"}'])
    result = llm.call_structured(
        model=llm.HAIKU, system="sys", prompt="p", response_model=_Response
    )
    assert result.value == "ok"
    assert fake.messages.calls == 2


def test_fails_loudly_after_two_malformed_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_client(monkeypatch, ["not json", "still not json"])
    with pytest.raises(llm.StructuredCallFailedError):
        llm.call_structured(model=llm.HAIKU, system="sys", prompt="p", response_model=_Response)
    assert fake.messages.calls == 2


def test_fails_loudly_after_two_schema_validation_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_client(monkeypatch, ["{}", "{}"])
    with pytest.raises(llm.StructuredCallFailedError):
        llm.call_structured(model=llm.HAIKU, system="sys", prompt="p", response_model=_Response)
    assert fake.messages.calls == 2


def test_non_text_blocks_are_ignored_when_assembling_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic responses can include non-text blocks (tool use, etc.);
    call_structured must only concatenate the text ones -- exercised
    through the real function, not a re-implementation of its filter."""
    fake = _FakeClient([])
    fake.messages = _FakeMessages([])
    mixed_message = _FakeMessage(
        [_FakeBlock("ignored", block_type="tool_use"), _FakeBlock('{"value": "ok"}')]
    )
    monkeypatch.setattr(fake.messages, "create", lambda **_kwargs: mixed_message)
    monkeypatch.setattr(llm, "_client", lambda: fake)

    result = llm.call_structured(
        model=llm.HAIKU, system="sys", prompt="p", response_model=_Response
    )
    assert result.value == "ok"


def test_missing_api_key_raises_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm._client.cache_clear()  # a client cached by an earlier test must not mask this
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm._client()


def test_client_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per review: _client() previously built a new anthropic.Anthropic()
    (and its own connection pool) on every call_structured() call.
    Verified via object identity, not just "it doesn't raise" -- a bug
    here wouldn't otherwise be observable from the outside."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    llm._client.cache_clear()
    try:
        first = llm._client()
        second = llm._client()
        assert first is second
    finally:
        llm._client.cache_clear()  # don't leak a cached client into other tests


def test_validation_failure_log_does_not_leak_raw_input_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Per review: pydantic's ValidationError.__str__() embeds the
    invalid input_value verbatim -- logging the exception object
    directly (logger.warning(..., exc)) could leak raw scraped article
    text flowing through from the model's own malformed response,
    violating AGENTS.md's "never log raw prompts/content" rule.

    Marker length matters here: pydantic truncates a long input_value
    repr in str(exc) (verified directly -- a ~55-char marker never
    appears even against the OLD, unfixed code, which would make this
    test pass regardless of whether the fix is present). Kept short and
    confirmed to survive untruncated, so this test actually distinguishes
    fixed from unfixed code."""
    sensitive_text = "SECRET-ARTICLE-TEXT"
    # 'value' must be a string; a nested object containing the sensitive
    # text fails validation, and pydantic's ValidationError would embed
    # it verbatim in str(exc) if that were logged directly.
    raw_texts = [
        json.dumps({"value": {"nested": sensitive_text}}),
        '{"value": "ok"}',
    ]
    _patch_client(monkeypatch, raw_texts)

    with caplog.at_level(logging.WARNING):
        result = llm.call_structured(
            model=llm.HAIKU, system="sys", prompt="p", response_model=_Response
        )

    assert result.value == "ok"
    assert sensitive_text not in caplog.text
