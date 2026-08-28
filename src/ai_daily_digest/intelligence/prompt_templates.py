"""Tiny loader/renderer for the `SYSTEM` / `USER TEMPLATE` convention used
by every file in intelligence/prompts/. Deliberately minimal — no
Jinja dependency, just `{{name}}` substitution — since these are short,
reviewed-by-hand templates, not user-facing rendering.
"""

from __future__ import annotations

import functools
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@functools.cache
def load_prompt(name: str) -> tuple[str, str]:
    """Load prompts/<name>.txt, split into (system, user_template) on the
    SYSTEM / USER TEMPLATE section markers. Cached: prompt file content
    is immutable for the life of the process, so re-reading and
    re-parsing the same file from disk on every call (every resolve_llm/
    extract_facts/compare_subjects invocation) was pure overhead."""
    path = PROMPTS_DIR / f"{name}.txt"
    text = path.read_text(encoding="utf-8")
    if "USER TEMPLATE" not in text:
        raise ValueError(f"{path} is missing the 'USER TEMPLATE' section marker")
    system_part, user_part = text.split("USER TEMPLATE", 1)
    system = system_part.replace("SYSTEM", "", 1).strip()
    return system, user_part.strip()


def render(template: str, **values: str) -> str:
    """Plain string substitution, `{{key}}` -> value, one pass per
    kwarg. No escaping, no loops/conditionals — if a template ever needs
    those, that's the signal to pull in a real templating library rather
    than growing this function ad hoc."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered
