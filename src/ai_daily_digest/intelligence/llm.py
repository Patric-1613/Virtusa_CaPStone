"""Single wrapper around the Anthropic SDK for all of intelligence's direct
LLM calls. Multi-step workflows (retrieval + extraction + generation) are
expected to move to LangGraph per docs/adr — see intelligence/CLAUDE.md —
but every LangGraph node that calls the model still goes through
call_structured() here, so retries, logging, and model choice stay in one
place instead of scattered across nodes.

Requires: anthropic, pydantic>=2, python-dotenv
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    # Only for the _client() return-type annotation below -- the runtime
    # import stays inside _client() so this module remains importable
    # without the anthropic package installed (see its docstring).
    import anthropic

logger = logging.getLogger("intelligence.llm")

# NOTE: PEP 695 `def call_structured[T: BaseModel](...)` is what
# ruff (target-version py312) actually wants here, and is what should ship
# once this runs on the team's real Python 3.12. Left as TypeVar for now
# because this dev machine only has Python 3.11.9 (see README.md's Setup
# note) and the PEP 695 syntax is a SyntaxError under 3.11 -- it would
# silently break every test that imports this module locally. Flip this
# once you're on 3.12; `ruff check` will flag UP047 until then.
T = TypeVar("T", bound=BaseModel)

# Model ids — keep in sync with intelligence/CLAUDE.md's model choice table.
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
OPUS = "claude-opus-5"


class StructuredCallFailedError(Exception):
    """Raised when the model fails to produce schema-valid output twice.
    Callers must not silently fall back to prose — catch this and either
    skip the item (resolution) or fail the run loudly (generation)."""


def _client() -> anthropic.Anthropic:
    # Imported lazily so this module can be imported (e.g. by tests that
    # only check prompt files exist) without the anthropic package installed.
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — copy .env.example to .env and fill it in.")
    return anthropic.Anthropic(api_key=api_key)


def call_structured(
    *,
    model: str,
    system: str,
    prompt: str,
    response_model: type[T],
    max_tokens: int = 2048,
    temperature: float = 0.0,
) -> T:
    """Call the model, parse its response as JSON, validate it against
    `response_model`. On validation failure, retry once with the error
    appended to the prompt. On a second failure, raise
    StructuredCallFailedError — never return unvalidated data.
    """
    client = _client()
    context_log = {"model": model, "system": system, "prompt": prompt}

    for attempt in range(2):
        logger.info("llm_call attempt=%s context=%s", attempt, json.dumps(context_log))
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
        try:
            data = json.loads(raw_text)
            return response_model.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("llm_validation_failed attempt=%s error=%s", attempt, exc)
            prompt = (
                f"{prompt}\n\n"
                f"Your previous response failed validation with this error:\n{exc}\n"
                f"Return ONLY valid JSON matching the required schema, nothing else."
            )

    raise StructuredCallFailedError(
        f"Model failed to produce valid {response_model.__name__} after 2 attempts."
    )
