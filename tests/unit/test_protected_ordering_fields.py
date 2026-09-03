"""Static guard for ADR 0008 section 5.D: no production code path may
mutate a pagination ordering-tuple component via `model_copy(update=...)`,
which bypasses Pydantic validation and the `Field(frozen=True)` guard.

The six protected columns are the `(business_sort_value, id)` pairs the
three cursor-paginated list endpoints sort on:

  SourceItem.id, SourceItem.first_fetched_at
  Change.id,     Change.detected_at
  Digest.id,     Digest.digest_date

This is deliberately narrow -- it does not forbid `model_copy(update=...)`
in general (production legitimately uses it to advance
`DigestClaim.validation_status` and `Digest.status`), only an `update=`
mapping that names one of the protected fields.
"""

from __future__ import annotations

import ast
from pathlib import Path

import ai_daily_digest

SRC_ROOT = Path(ai_daily_digest.__file__).parent

PROTECTED_FIELDS = frozenset(
    {
        "id",
        "first_fetched_at",
        "detected_at",
        "digest_date",
    }
)


def _model_copy_update_keys(tree: ast.AST) -> list[tuple[int, str]]:
    """Every literal key passed to a `.model_copy(update={...})` call in
    `tree`, as (lineno, key) pairs. A non-literal `update=` argument (a
    variable, a comprehension) is reported as the sentinel key
    ``"<dynamic>"`` so the test fails loudly rather than silently missing
    a bypass it cannot see."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "model_copy"):
            continue
        for kw in node.keywords:
            if kw.arg != "update":
                continue
            if isinstance(kw.value, ast.Dict):
                for key in kw.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.append((node.lineno, key.value))
                    else:
                        found.append((node.lineno, "<dynamic>"))
            else:
                found.append((node.lineno, "<dynamic>"))
    return found


def test_no_production_model_copy_mutates_a_protected_ordering_field() -> None:
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SRC_ROOT.parent)
        for lineno, key in _model_copy_update_keys(tree):
            if key == "<dynamic>":
                offenders.append(
                    f"{rel}:{lineno}: model_copy(update=<non-literal>) -- cannot statically "
                    "prove it does not touch a protected ordering field"
                )
            elif key in PROTECTED_FIELDS:
                offenders.append(
                    f"{rel}:{lineno}: model_copy(update=...) sets protected ordering field {key!r}"
                )
    assert not offenders, "\n".join(offenders)


def test_the_guard_actually_sees_model_copy_update_calls() -> None:
    """Negative control: the AST matcher finds a known
    `model_copy(update=...)` in production, so a green
    `test_no_production_model_copy_...` means "scanned and clean", not
    "scanned nothing"."""
    all_keys = {
        key
        for path in SRC_ROOT.rglob("*.py")
        for _, key in _model_copy_update_keys(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
    }
    assert all_keys, "expected at least one model_copy(update=...) somewhere in production code"
    assert all_keys & {"status", "claims", "validation_status"}, (
        f"matcher found update keys {all_keys} but not the known-good ones -- matcher may be broken"
    )
