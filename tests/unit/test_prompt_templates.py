"""Tests for the tiny prompt-file loader/renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_daily_digest.intelligence.prompt_templates import load_prompt, render


def test_load_prompt_reads_and_splits_a_real_prompt_file() -> None:
    system, user_template = load_prompt("resolve")
    assert "Output JSON only" in system
    assert "{{item_title}}" in user_template


def test_load_prompt_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per review: load_prompt() previously re-read and re-parsed the
    prompt file from disk on every call, even though the file content is
    immutable for the life of the process."""
    load_prompt.cache_clear()
    read_calls: list[Path] = []
    original_read_text = Path.read_text

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        read_calls.append(self)
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    try:
        load_prompt("resolve")
        load_prompt("resolve")
        assert len(read_calls) == 1  # second call served from cache, not re-read
    finally:
        load_prompt.cache_clear()  # don't leak a cached-with-a-patched-read entry


def test_render_substitutes_every_placeholder() -> None:
    rendered = render("Hello {{name}}, your score is {{score}}.", name="World", score="71.2")
    assert rendered == "Hello World, your score is 71.2."


def test_render_leaves_unmatched_placeholders_untouched() -> None:
    rendered = render("Hello {{name}}.", other="unused")
    assert rendered == "Hello {{name}}."
