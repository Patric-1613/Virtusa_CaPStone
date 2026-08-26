"""Shared evidence-grounding primitives, used by every place in
intelligence/ that has to answer "is this specific value actually backed
by this specific piece of text" — extract_facts.py (value vs. quoted
span), compare_subjects.py (claim prose vs. the fact-table rows it's
allowed to cite), and validate.py (claim text vs. cited snapshot
content).

Deliberately narrow and deterministic, per docs/ARCHITECTURE.md's
"prefer deterministic code" rule: this is NOT a semantic entailment or
contradiction checker (that would need an LLM, and isn't built). What it
does check, precisely: does every *number* asserted in some text also
appear in some reference text, using formatting-tolerant, word/digit-
boundary-safe matching. That's a real, meaningful check for this
project's fields (context_window_tokens, prices, benchmark_scores are
all numeric) but it is a floor, not a ceiling — a claim could still be
numerically grounded and prose-wise wrong in some other way (e.g. it
could swap which subject a real, cited number belongs to, if nothing
else catches that first). Callers layer this on top of their own
subject/field/citation-ownership checks, not instead of them.
"""

from __future__ import annotations

import re

from ai_daily_digest.intelligence.facts import normalise_name

# Strips thousands separators and currency/percent symbols WITHOUT
# turning them into word-boundary spaces (unlike normalise_name's
# punctuation strip) -- "256,000" and "256000" must collapse to the same
# digit run, not split into two tokens ("256", "000").
_NUMBER_FORMATTING_RE = re.compile(r"[,$%]")

# A bare token of digits (optionally with one decimal point) -- what
# counts as "a number" for grounding purposes here. Excludes a digit run
# immediately touching a letter on either side: product names in this
# project routinely embed digits ("GPT-4o", "o1", "GPT-4") and must NOT
# be read as an asserted number just because the model/subject name
# appears in the same sentence as a real one.
_NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])")


def numbers_in(text: str) -> set[str]:
    """Every standalone numeric token in `text`, with formatting
    differences (commas, currency/percent symbols) normalised away so
    "$256,000" and "256000" are the same token. Digits embedded in an
    alphanumeric name (e.g. the "4" in "GPT-4o") are not numbers for this
    purpose -- see _NUMBER_TOKEN_RE. Used to compare "what numbers does
    this claim assert" against "what numbers does this evidence actually
    contain"."""
    stripped = _NUMBER_FORMATTING_RE.sub("", text)
    return set(_NUMBER_TOKEN_RE.findall(stripped))


def value_supported_by_quote(value: str, quoted_span: str) -> bool:
    """True if `value` is actually backed by `quoted_span` -- catches a
    model that quotes a real sentence from the source but attaches a
    different, invented value to it (the quote is grounded per
    extract_facts.py's existing check; the value inside it might not
    be). Two independent checks, either is sufficient:

    1. Non-numeric / exact-phrase match: `value` appears as a whole,
       space-bounded token/phrase inside the quote (word-boundary, not a
       loose substring -- mirrors resolve.py's _contains_phrase). Covers
       fields like licence_terms ("Apache-2.0") and modalities.
    2. Numeric match: `value`'s digit run appears in the quote's digit
       run, tolerant of comma/currency/percent formatting differences,
       but NOT as a substring of a *larger* number (value "20" must not
       match inside quote text containing "120").
    """
    normalised_value = normalise_name(value)
    normalised_quote = normalise_name(quoted_span)
    if normalised_value and f" {normalised_value} " in f" {normalised_quote} ":
        return True

    value_numbers = numbers_in(value)
    if not value_numbers:
        return False
    quote_numbers = numbers_in(quoted_span)
    return value_numbers <= quote_numbers
