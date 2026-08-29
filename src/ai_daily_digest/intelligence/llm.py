"""Single wrapper around the Anthropic SDK for all of intelligence's direct
LLM calls. Multi-step workflows (retrieval + extraction + generation) are
expected to move to LangGraph per docs/adr — see intelligence/CLAUDE.md —
but every LangGraph node that calls the model still goes through
call_structured() here, so retries, logging, and model choice stay in one
place instead of scattered across nodes.

Requires: anthropic, pydantic>=2, python-dotenv
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    # Only for the _client() return-type annotation below -- the runtime
    # import stays inside _client() so this module remains importable
    # without the anthropic package installed (see its docstring).
    import anthropic

logger = logging.getLogger("intelligence.llm")

# Model ids — keep in sync with intelligence/CLAUDE.md's model choice table.
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
OPUS = "claude-opus-5"


class StructuredCallFailedError(Exception):
    """Raised when the model fails to produce schema-valid output twice.
    Callers must not silently fall back to prose — catch this and either
    skip the item (resolution) or fail the run loudly (generation)."""


@functools.lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    # Cached: this previously built a brand new anthropic.Anthropic()
    # (and its own connection pool) on every call_structured() call.
    # lru_cache only caches a *successful* return -- it never caches a
    # raised exception, so a missing API key still raises fresh on every
    # call rather than getting stuck behind a cached failure. The cached
    # client does persist for the process's lifetime, so it won't pick
    # up an API key that's rotated at runtime without a process restart
    # -- an accepted tradeoff of caching a singleton client, same as any
    # other cached connection/client object.
    #
    # Imported lazily so this module can be importable (e.g. by tests
    # that only check prompt files exist) without the anthropic package
    # installed.
    import anthropic  # pylint: disable=import-outside-toplevel

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — copy .env.example to .env and fill it in.")
    return anthropic.Anthropic(api_key=api_key)


def call_structured[T: BaseModel](  # pylint: disable=too-many-arguments
    # All keyword-only: model/system/prompt/response_model are the call
    # itself, max_tokens is provider-call tuning -- every LLM call site in
    # intelligence/ goes through this one function (see module docstring)
    # specifically so that knob lives in one place instead of being
    # duplicated per call site.
    #
    # PEP 695 generic syntax (requires Python >=3.12, this project's
    # target -- see pyproject.toml's requires-python). An earlier version
    # of this function used `TypeVar` instead because the machine it was
    # written on only had Python 3.11 installed; verified and switched to
    # this syntax once run against a real 3.12 interpreter (`uv`'s
    # managed toolchain, via `uv run --no-editable`).
    *,
    model: str,
    system: str,
    prompt: str,
    response_model: type[T],
    max_tokens: int = 2048,
) -> T:
    """Call the model, parse its response as JSON, validate it against
    `response_model`. On validation failure, retry once with the error
    appended to the prompt. On a second failure, raise
    StructuredCallFailedError — never return unvalidated data.

    No `temperature` parameter: sampling controls (temperature/top_p/
    top_k) are removed on the current-generation models this file's
    HAIKU/SONNET/OPUS constants point at (they return 400 if sent) --
    verified against the real installed SDK, not assumed. An earlier
    version of this function accepted temperature=0.0 hoping for
    deterministic output; that call would have failed against the real
    API the first time it ran live (every test here uses an injected
    fake, so nothing caught it). Reproducibility now comes from this
    function's own schema-validation retry loop and from each call
    site's own grounding checks (extract_facts.py, compare_subjects.py,
    ...), not from a sampling knob.
    """
    client = _client()

    for attempt in range(2):
        # Never log the raw prompt/system text -- repo-root AGENTS.md:
        # "Never place subscriber email addresses, credentials, or raw
        # prompts in logs." Collected page content flows into the prompt
        # (extract_facts, compare_subjects, ...), so logging it verbatim
        # would put scraped article text into whatever log retention
        # system picks this up. A short content hash is enough to
        # correlate "this attempt, this prompt" across log lines without
        # persisting the content itself.
        prompt_fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        logger.info(
            "llm_call attempt=%s model=%s prompt_chars=%s prompt_fingerprint=%s",
            attempt,
            model,
            len(prompt),
            prompt_fingerprint,
        )
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
        try:
            data = json.loads(raw_text)
            return response_model.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            # Never log str(exc)/repr(exc) directly for a ValidationError:
            # pydantic embeds each failing field's actual input_value
            # verbatim in its default string form, which can include raw
            # scraped article text flowing through from the model's own
            # malformed response -- exactly what AGENTS.md's "never log
            # raw prompts/content" rule exists to prevent (same reasoning
            # as the prompt_fingerprint hash above, not the prompt
            # itself). errors(include_input=False) gives the same
            # type/location/message shape for debugging without the
            # actual value. json.JSONDecodeError's default str() doesn't
            # embed the source document, so it's logged as-is.
            if isinstance(exc, ValidationError):
                logger.warning(
                    "llm_validation_failed attempt=%s error_type=%s errors=%s",
                    attempt,
                    type(exc).__name__,
                    exc.errors(include_url=False, include_context=False, include_input=False),
                )
            else:
                logger.warning(
                    "llm_validation_failed attempt=%s error_type=%s error=%s",
                    attempt,
                    type(exc).__name__,
                    exc,
                )
            prompt = (
                f"{prompt}\n\n"
                f"Your previous response failed validation with this error:\n{exc}\n"
                f"Return ONLY valid JSON matching the required schema, nothing else."
            )

    raise StructuredCallFailedError(
        f"Model failed to produce valid {response_model.__name__} after 2 attempts."
    )
