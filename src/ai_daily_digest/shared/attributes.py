"""The closed set of comparable fact fields (docs/API_CONTRACT.md's
`field` on ExtractedFact/Change). Closed on purpose: an open set can't be
compared. Field names follow the contract's own example
("context_window_tokens") — unit-suffixed where the unit isn't obvious.

Still Draft v0.1 alongside the rest of the contract — extend only by team
agreement (shared/, same CODEOWNERS sign-off rule as schemas.py).
"""

from __future__ import annotations

COMPARABLE_FIELDS: dict[str, str] = {
    # field: human-readable label, used in prompts and rendered UI/email.
    "context_window_tokens": "Context window",
    "input_price_usd": "Input price (USD)",
    "output_price_usd": "Output price (USD)",
    "benchmark_scores": "Named benchmark scores",
    "availability_regions": "Availability regions",
    "licence_terms": "Licence terms",
    "modalities": "Modalities",
}
