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
