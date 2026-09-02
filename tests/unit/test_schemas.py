"""Direct tests for shared/schemas.py's validation primitives -- as
opposed to tests/contract/, which protects the fixture pack, this is
about the Python model definitions themselves."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from ai_daily_digest.shared.schemas import Change, ExtractedFact, FactObservation, Subject

SUBJECT = Subject(company="OpenAI", product="GPT-4o")

_Builder = Callable[[float], ExtractedFact | Change]


def _extracted_fact(confidence: float) -> ExtractedFact:
    return ExtractedFact(
        id="f1",
        snapshot_id="snap_1",
        field="context_window_tokens",
        value="256000",
        extraction_method="llm_structured_output",
        quoted_span="256,000 tokens",
        confidence=confidence,
    )


def _change(confidence: float) -> Change:
    return Change(
        id="c1",
        change_set_id="cs1",
        subject=SUBJECT,
        field="context_window_tokens",
        change_type="changed",
        current=FactObservation(value="256000"),
        confidence=confidence,
    )


@pytest.mark.parametrize("build", [_extracted_fact, _change])
def test_confidence_rejects_nan(build: _Builder) -> None:
    with pytest.raises(ValidationError):
        build(float("nan"))


@pytest.mark.parametrize("build", [_extracted_fact, _change])
def test_confidence_rejects_infinity(build: _Builder) -> None:
    with pytest.raises(ValidationError):
        build(float("inf"))


@pytest.mark.parametrize("build", [_extracted_fact, _change])
def test_confidence_rejects_out_of_range(build: _Builder) -> None:
    with pytest.raises(ValidationError):
        build(1.5)
    with pytest.raises(ValidationError):
        build(-0.1)


@pytest.mark.parametrize("build", [_extracted_fact, _change])
def test_confidence_accepts_valid_range(build: _Builder) -> None:
    assert build(0.0).confidence == 0.0
    assert build(1.0).confidence == 1.0
    assert build(0.6).confidence == 0.6


# --- ADR 0004's accepted clarification: LLM-extracted facts must carry
# their evidence at the model level, not just via extraction code and
# contract tests. ---


def test_llm_extracted_fact_without_quoted_span_is_rejected() -> None:
    with pytest.raises(ValidationError, match="quoted_span"):
        ExtractedFact(
            id="f1",
            snapshot_id="snap_1",
            field="context_window_tokens",
            value="256000",
            extraction_method="llm_structured_output",
            confidence=0.9,
        )


def test_llm_extracted_fact_without_confidence_is_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        ExtractedFact(
            id="f1",
            snapshot_id="snap_1",
            field="context_window_tokens",
            value="256000",
            extraction_method="llm_structured_output",
            quoted_span="256,000 tokens",
        )


def test_deterministic_fact_without_evidence_is_still_allowed() -> None:
    """The invariant is scoped to extraction_method="llm_structured_output"
    only -- deterministic facts don't always have a natural quote to
    attach (see ExtractedFact's own docstring), and must stay unaffected."""
    fact = ExtractedFact(
        id="f1",
        snapshot_id="snap_1",
        field="context_window_tokens",
        value="256000",
        extraction_method="deterministic",
    )
    assert fact.quoted_span is None
    assert fact.confidence is None


# --- ADR 0006: "unknown" vs. "not disclosed" are different claims --
# disclosure_status/value invariants on ExtractedFact. ---


def test_not_disclosed_fact_with_grounded_evidence_is_accepted() -> None:
    fact = ExtractedFact(
        id="f1",
        snapshot_id="snap_1",
        field="input_price_usd",
        value=None,
        disclosure_status="not_disclosed",
        extraction_method="llm_structured_output",
        extraction_model="claude-sonnet-5",
        prompt_version="v1",
        quoted_span="pricing has not yet been announced",
        confidence=0.9,
    )
    assert fact.value is None
    assert fact.disclosure_status == "not_disclosed"


def test_not_disclosed_fact_with_a_value_is_rejected() -> None:
    """A fact can't simultaneously state a value and claim none was
    given -- the exact contradiction this ADR calls out."""
    with pytest.raises(ValidationError, match="not_disclosed"):
        ExtractedFact(
            id="f1",
            snapshot_id="snap_1",
            field="input_price_usd",
            value="5",
            disclosure_status="not_disclosed",
            extraction_method="llm_structured_output",
            extraction_model="claude-sonnet-5",
            prompt_version="v1",
            quoted_span="pricing has not yet been announced",
            confidence=0.9,
        )


def test_not_disclosed_fact_without_quoted_span_is_rejected() -> None:
    """ "Not disclosed" is a groundable claim, not a default inferred from
    silence -- it needs a citation the same as any other extracted fact,
    regardless of extraction_method (unlike the LLM-only evidence
    requirement above)."""
    with pytest.raises(ValidationError, match="quoted_span"):
        ExtractedFact(
            id="f1",
            snapshot_id="snap_1",
            field="input_price_usd",
            value=None,
            disclosure_status="not_disclosed",
            extraction_method="deterministic",
        )


def test_not_disclosed_fact_with_empty_quoted_span_is_rejected() -> None:
    with pytest.raises(ValidationError, match="quoted_span"):
        ExtractedFact(
            id="f1",
            snapshot_id="snap_1",
            field="input_price_usd",
            value=None,
            disclosure_status="not_disclosed",
            extraction_method="deterministic",
            quoted_span="",
        )


def test_disclosed_fact_with_explicit_none_value_is_rejected() -> None:
    """disclosure_status="disclosed" is the default -- explicitly passing
    value=None with it must not silently produce a fact that claims to
    be disclosed while stating nothing."""
    with pytest.raises(ValidationError, match="disclosed"):
        ExtractedFact(
            id="f1",
            snapshot_id="snap_1",
            field="context_window_tokens",
            value=None,
            extraction_method="deterministic",
        )


def test_disclosed_fact_with_empty_string_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="disclosed"):
        ExtractedFact(
            id="f1",
            snapshot_id="snap_1",
            field="context_window_tokens",
            value="",
            extraction_method="deterministic",
        )


def test_disclosed_fact_with_a_real_value_is_accepted() -> None:
    """A non-empty value is accepted for disclosure_status="disclosed"
    (the default) -- the positive case the two rejections above are the
    negative side of."""
    fact = ExtractedFact(
        id="f1",
        snapshot_id="snap_1",
        field="context_window_tokens",
        value="256000",
        extraction_method="deterministic",
    )
    assert fact.value == "256000"
    assert fact.disclosure_status == "disclosed"


# --- `value` has no default on ExtractedFact (ADR 0006 revision
# requested by Person A) -- a construction site that omits it entirely
# must be rejected, for either disclosure_status, never silently
# defaulted to a value that means something specific (previously None,
# i.e. "not disclosed"). ---


def test_extracted_fact_omitting_value_entirely_is_rejected_when_disclosed() -> None:
    with pytest.raises(ValidationError):
        ExtractedFact(  # type: ignore[call-arg]
            id="f1",
            snapshot_id="snap_1",
            field="context_window_tokens",
            extraction_method="deterministic",
        )


def test_extracted_fact_omitting_value_entirely_is_rejected_when_not_disclosed() -> None:
    with pytest.raises(ValidationError):
        ExtractedFact(  # type: ignore[call-arg]
            id="f1",
            snapshot_id="snap_1",
            field="context_window_tokens",
            disclosure_status="not_disclosed",
            extraction_method="deterministic",
            quoted_span="pricing has not been announced",
        )
