"""Direct tests for shared/schemas.py's validation primitives -- as
opposed to tests/contract/, which protects the fixture pack, this is
about the Python model definitions themselves."""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from enum import StrEnum

import pytest
from pydantic import ValidationError

from ai_daily_digest.shared.schemas import (
    Change,
    ClaimValidationStatus,
    Digest,
    DigestClaim,
    DigestStatus,
    DisclosureStatus,
    ExtractedFact,
    ExtractionMethod,
    FactObservation,
    SourceItem,
    Subject,
)
from tests.uuid_samples import (
    CHANGE_1,
    CHANGE_SET_1,
    CLAIM_1,
    DIGEST_1,
    FACT_1,
    ITEM_1,
    SNAPSHOT_1,
    SNAPSHOT_2,
    SNAPSHOT_3,
)

SUBJECT = Subject(company="OpenAI", product="GPT-4o")

# ADR 0008: a fixed, deterministic detection time -- every test that needs
# a real Change.detected_at uses this same value, so tests never depend on
# wall-clock time.
DETECTED_AT = datetime(2026, 8, 20, 9, 5, tzinfo=UTC)

_Builder = Callable[[float], ExtractedFact | Change]


def _extracted_fact(confidence: float) -> ExtractedFact:
    return ExtractedFact(
        id=FACT_1,
        snapshot_id=SNAPSHOT_1,
        field="context_window_tokens",
        value="256000",
        extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
        quoted_span="256,000 tokens",
        confidence=confidence,
    )


def _change(confidence: float) -> Change:
    return Change(
        id=CHANGE_1,
        change_set_id=CHANGE_SET_1,
        subject=SUBJECT,
        field="context_window_tokens",
        change_type="changed",
        detected_at=DETECTED_AT,
        previous=FactObservation(value="128000", snapshot_id=SNAPSHOT_1),
        current=FactObservation(value="256000", snapshot_id=SNAPSHOT_2),
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
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="context_window_tokens",
            value="256000",
            extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
            confidence=0.9,
        )


def test_llm_extracted_fact_without_confidence_is_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        ExtractedFact(
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="context_window_tokens",
            value="256000",
            extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
            quoted_span="256,000 tokens",
        )


def test_deterministic_fact_without_evidence_is_still_allowed() -> None:
    """The invariant is scoped to extraction_method="llm_structured_output"
    only -- deterministic facts don't always have a natural quote to
    attach (see ExtractedFact's own docstring), and must stay unaffected."""
    fact = ExtractedFact(
        id=FACT_1,
        snapshot_id=SNAPSHOT_1,
        field="context_window_tokens",
        value="256000",
        extraction_method=ExtractionMethod.DETERMINISTIC,
    )
    assert fact.quoted_span is None
    assert fact.confidence is None


# --- ADR 0006: "unknown" vs. "not disclosed" are different claims --
# disclosure_status/value invariants on ExtractedFact. ---


def test_not_disclosed_fact_with_grounded_evidence_is_accepted() -> None:
    fact = ExtractedFact(
        id=FACT_1,
        snapshot_id=SNAPSHOT_1,
        field="input_price_usd",
        value=None,
        disclosure_status=DisclosureStatus.NOT_DISCLOSED,
        extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
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
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="input_price_usd",
            value="5",
            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
            extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
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
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="input_price_usd",
            value=None,
            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )


def test_not_disclosed_fact_with_empty_quoted_span_is_rejected() -> None:
    with pytest.raises(ValidationError, match="quoted_span"):
        ExtractedFact(
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="input_price_usd",
            value=None,
            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            quoted_span="",
        )


def test_disclosed_fact_with_explicit_none_value_is_rejected() -> None:
    """disclosure_status="disclosed" is the default -- explicitly passing
    value=None with it must not silently produce a fact that claims to
    be disclosed while stating nothing."""
    with pytest.raises(ValidationError, match="disclosed"):
        ExtractedFact(
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="context_window_tokens",
            value=None,
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )


def test_disclosed_fact_with_empty_string_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="disclosed"):
        ExtractedFact(
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="context_window_tokens",
            value="",
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )


def test_disclosed_fact_with_a_real_value_is_accepted() -> None:
    """A non-empty value is accepted for disclosure_status="disclosed"
    (the default) -- the positive case the two rejections above are the
    negative side of."""
    fact = ExtractedFact(
        id=FACT_1,
        snapshot_id=SNAPSHOT_1,
        field="context_window_tokens",
        value="256000",
        extraction_method=ExtractionMethod.DETERMINISTIC,
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
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="context_window_tokens",
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )


def test_extracted_fact_omitting_value_entirely_is_rejected_when_not_disclosed() -> None:
    with pytest.raises(ValidationError):
        ExtractedFact(  # type: ignore[call-arg]
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="context_window_tokens",
            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            quoted_span="pricing has not been announced",
        )


# --- ADR 0007: id-shaped fields are typed `Uuid7Id` (a re-export of
# pydantic.UUID7). A well-formed UUID of the wrong version must be
# rejected specifically for its version, which is a different failure
# than a malformed string that cannot be parsed at all. ---


@pytest.mark.parametrize("as_string", [False, True], ids=["uuid_object", "uuid_string"])
def test_uuid_v4_is_rejected_by_a_uuid7_typed_field_for_its_version(as_string: bool) -> None:
    """A syntactically valid UUID v4 -- passed either as a `uuid.UUID`
    object or its canonical string -- does not satisfy a `Uuid7Id`-typed
    field. The rejection is specifically a version mismatch
    (`uuid_version`), not a parse failure (`uuid_parsing`): the version
    constraint is what does the work here, not incidental syntax
    strictness."""
    v4 = uuid.UUID("4e2b4d9a-0c1f-4b6e-9d3a-1f2e3c4d5b6a")
    assert v4.version == 4  # guard: the fixture really is a v4

    with pytest.raises(ValidationError) as exc_info:
        ExtractedFact(
            id=FACT_1,
            snapshot_id=(str(v4) if as_string else v4),  # type: ignore[arg-type]
            field="context_window_tokens",
            value="256000",
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

    error_types = {error["type"] for error in exc_info.value.errors()}
    assert error_types == {"uuid_version"}
    assert "uuid_parsing" not in error_types


def test_malformed_uuid_is_rejected_by_a_uuid7_typed_field_as_a_parse_failure() -> None:
    """Contrast case for the test above: a genuinely malformed value fails
    with `uuid_parsing`, never reaching the version check -- so the two
    rejection reasons stay distinguishable."""
    with pytest.raises(ValidationError) as exc_info:
        ExtractedFact(
            id=FACT_1,
            snapshot_id="not-a-uuid",  # type: ignore[arg-type]
            field="context_window_tokens",
            value="256000",
            extraction_method=ExtractionMethod.DETERMINISTIC,
        )

    error_types = {error["type"] for error in exc_info.value.errors()}
    assert error_types == {"uuid_parsing"}
    assert "uuid_version" not in error_types


# --- Change's own invariant validator (ADR 0006 follow-up, reviewed) --
# the exact required observation shape per change_type, enforced at
# construction on ANY Change, not just ones FactStore.update_fact()
# happens to build. ---


def _valid_change(**overrides: object) -> Change:
    """A baseline "changed" Change with a fully valid shape -- tests
    below override just the field(s) under test."""
    defaults: dict[str, object] = {
        "id": CHANGE_1,
        "change_set_id": CHANGE_SET_1,
        "subject": SUBJECT,
        "field": "input_price_usd",
        "change_type": "changed",
        "detected_at": DETECTED_AT,
        "previous": FactObservation(value="10", snapshot_id=SNAPSHOT_1),
        "current": FactObservation(value="5", snapshot_id=SNAPSHOT_2),
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return Change(**defaults)  # type: ignore[arg-type]


def test_valid_not_disclosed_change_passes_validation() -> None:
    change = _valid_change(
        change_type="not_disclosed",
        previous=FactObservation(value="5", snapshot_id=SNAPSHOT_2),
        current=FactObservation(value=None, snapshot_id=SNAPSHOT_3),
    )
    assert change.change_type == "not_disclosed"
    assert change.current.value is None


def test_invalid_not_disclosed_change_fails_validation() -> None:
    # current.value is not None
    with pytest.raises(ValidationError, match="not_disclosed"):
        _valid_change(
            change_type="not_disclosed",
            previous=FactObservation(value="5", snapshot_id=SNAPSHOT_2),
            current=FactObservation(value="5", snapshot_id=SNAPSHOT_3),
        )
    # previous is None
    with pytest.raises(ValidationError, match="not_disclosed"):
        _valid_change(
            change_type="not_disclosed",
            previous=None,
            current=FactObservation(value=None, snapshot_id=SNAPSHOT_3),
        )
    # previous.value is None
    with pytest.raises(ValidationError, match="not_disclosed"):
        _valid_change(
            change_type="not_disclosed",
            previous=FactObservation(value=None, snapshot_id=SNAPSHOT_2),
            current=FactObservation(value=None, snapshot_id=SNAPSHOT_3),
        )
    # missing snapshot_id on previous
    with pytest.raises(ValidationError, match="not_disclosed"):
        _valid_change(
            change_type="not_disclosed",
            previous=FactObservation(value="5", snapshot_id=None),
            current=FactObservation(value=None, snapshot_id=SNAPSHOT_3),
        )
    # missing snapshot_id on current
    with pytest.raises(ValidationError, match="not_disclosed"):
        _valid_change(
            change_type="not_disclosed",
            previous=FactObservation(value="5", snapshot_id=SNAPSHOT_2),
            current=FactObservation(value=None, snapshot_id=None),
        )


def test_valid_disclosed_change_passes_validation() -> None:
    # first disclosure -- previous is None entirely
    first = _valid_change(
        change_type="disclosed",
        previous=None,
        current=FactObservation(value="5", snapshot_id=SNAPSHOT_2),
    )
    assert first.previous is None

    # transition from not_disclosed -- previous exists with value=None
    transition = _valid_change(
        change_type="disclosed",
        previous=FactObservation(value=None, snapshot_id=SNAPSHOT_3),
        current=FactObservation(value="5", snapshot_id=SNAPSHOT_2),
    )
    assert transition.previous is not None
    assert transition.previous.value is None


def test_invalid_disclosed_change_fails_validation() -> None:
    # current.value is None
    with pytest.raises(ValidationError, match="disclosed"):
        _valid_change(
            change_type="disclosed",
            previous=None,
            current=FactObservation(value=None, snapshot_id=SNAPSHOT_2),
        )
    # previous.value is not None (a real transition must come from
    # not_disclosed, i.e. previous.value=None -- a previous with a real
    # value belongs to "increased"/"decreased"/"changed", not "disclosed")
    with pytest.raises(ValidationError, match="disclosed"):
        _valid_change(
            change_type="disclosed",
            previous=FactObservation(value="3", snapshot_id=SNAPSHOT_1),
            current=FactObservation(value="5", snapshot_id=SNAPSHOT_2),
        )


def test_invalid_numeric_change_fails_when_observation_missing_or_none() -> None:
    for change_type in ("increased", "decreased", "changed"):
        with pytest.raises(ValidationError, match=change_type):
            _valid_change(change_type=change_type, previous=None)
        with pytest.raises(ValidationError, match=change_type):
            _valid_change(
                change_type=change_type,
                current=FactObservation(value=None, snapshot_id=SNAPSHOT_2),
            )


def test_unrecognised_change_type_still_gets_generic_shape_validation() -> None:
    """The validator's final branch is a catch-all `else`, not a closed
    `elif change_type in ("increased", "decreased", "changed")` -- an
    open/unknown change_type string (a typo, or a legitimate future
    change_type this model doesn't know about yet) must still be held to
    the generic "both sides are real observations" shape, not silently
    skip validation because it matched none of the named branches."""
    valid = _valid_change(change_type="unsupported_type")
    assert valid.change_type == "unsupported_type"

    with pytest.raises(ValidationError, match="unsupported_type"):
        _valid_change(change_type="unsupported_type", previous=None)
    with pytest.raises(ValidationError, match="unsupported_type"):
        _valid_change(
            change_type="unsupported_type",
            current=FactObservation(value=None, snapshot_id=SNAPSHOT_2),
        )
    with pytest.raises(ValidationError, match="unsupported_type"):
        _valid_change(
            change_type="unsupported_type",
            previous=FactObservation(value="10", snapshot_id=None),
        )


# --- ADR 0009: Phase 1 shared StrEnums -- DisclosureStatus/ExtractionMethod
# (ExtractedFact), ClaimValidationStatus (DigestClaim), DigestStatus
# (Digest). Each is a closed, deliberately narrow set: a valid member is
# accepted and round-trips as the exact lowercase wire string it always
# was; an invalid, misspelled, or wrong-case string is now rejected at
# the model boundary instead of only by convention. ---


def _disclosed_fact(disclosure_status: object, extraction_method: object) -> ExtractedFact:
    """A "disclosed" ExtractedFact shape (real value, no not_disclosed
    evidence rule in play) -- isolates disclosure_status/extraction_method
    field-type validation from _require_valid_disclosure_state's separate
    shape rule."""
    return ExtractedFact(
        id=FACT_1,
        snapshot_id=SNAPSHOT_1,
        field="context_window_tokens",
        value="256000",
        disclosure_status=disclosure_status,  # type: ignore[arg-type]
        extraction_method=extraction_method,  # type: ignore[arg-type]
        quoted_span="256,000 tokens",
        confidence=0.9,
    )


@pytest.mark.parametrize("member", list(DisclosureStatus))
def test_disclosure_status_accepts_every_valid_member(member: DisclosureStatus) -> None:
    if member is DisclosureStatus.NOT_DISCLOSED:
        fact = ExtractedFact(
            id=FACT_1,
            snapshot_id=SNAPSHOT_1,
            field="input_price_usd",
            value=None,
            disclosure_status=member,
            extraction_method=ExtractionMethod.DETERMINISTIC,
            quoted_span="pricing has not yet been announced",
        )
    else:
        fact = _disclosed_fact(member, ExtractionMethod.DETERMINISTIC)
    assert fact.disclosure_status is member


@pytest.mark.parametrize("bad_value", ["DISCLOSED", "Disclosed", "unknown", "not-disclosed", ""])
def test_disclosure_status_rejects_invalid_strings(bad_value: str) -> None:
    with pytest.raises(ValidationError):
        _disclosed_fact(bad_value, ExtractionMethod.DETERMINISTIC)


@pytest.mark.parametrize("member", list(ExtractionMethod))
def test_extraction_method_accepts_every_valid_member(member: ExtractionMethod) -> None:
    fact = _disclosed_fact(DisclosureStatus.DISCLOSED, member)
    assert fact.extraction_method is member


@pytest.mark.parametrize(
    "bad_value", ["DETERMINISTIC", "Llm_Structured_Output", "llm", "manual", ""]
)
def test_extraction_method_rejects_invalid_strings(bad_value: str) -> None:
    with pytest.raises(ValidationError):
        _disclosed_fact(DisclosureStatus.DISCLOSED, bad_value)


def _digest_claim(validation_status: object) -> DigestClaim:
    return DigestClaim(
        id=CLAIM_1,
        text="Example claim.",
        citation_snapshot_ids=[SNAPSHOT_1],
        validation_status=validation_status,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("member", list(ClaimValidationStatus))
def test_claim_validation_status_accepts_every_valid_member(
    member: ClaimValidationStatus,
) -> None:
    assert _digest_claim(member).validation_status is member


def test_claim_validation_status_defaults_to_pending() -> None:
    claim = DigestClaim(id=CLAIM_1, text="Example claim.", citation_snapshot_ids=[SNAPSHOT_1])
    assert claim.validation_status is ClaimValidationStatus.PENDING


@pytest.mark.parametrize("bad_value", ["PENDING", "Supported", "unsure", "rejected", ""])
def test_claim_validation_status_rejects_invalid_strings(bad_value: str) -> None:
    with pytest.raises(ValidationError):
        _digest_claim(bad_value)


def _digest(status: object) -> Digest:
    return Digest(
        id=DIGEST_1,
        digest_date=date(2026, 9, 2),
        status=status,  # type: ignore[arg-type]
        title="Test digest",
    )


@pytest.mark.parametrize("member", list(DigestStatus))
def test_digest_status_accepts_every_valid_member(member: DigestStatus) -> None:
    assert _digest(member).status is member


def test_digest_status_defaults_to_draft() -> None:
    digest = Digest(id=DIGEST_1, digest_date=date(2026, 9, 2), title="Test digest")
    assert digest.status is DigestStatus.DRAFT


@pytest.mark.parametrize("bad_value", ["DRAFT", "Published", "archived", "live", ""])
def test_digest_status_rejects_invalid_strings(bad_value: str) -> None:
    with pytest.raises(ValidationError):
        _digest(bad_value)


# --- JSON wire format: an Enum-backed field still emits the exact same
# lowercase string on the wire, round-trips through JSON, and its
# model_json_schema() enum list is exactly its members -- ADR 0009's
# "unchanged wire format" guarantee, verified rather than assumed. ---


def test_extracted_fact_enum_fields_serialize_to_exact_lowercase_strings() -> None:
    fact = _disclosed_fact(DisclosureStatus.DISCLOSED, ExtractionMethod.LLM_STRUCTURED_OUTPUT)
    dumped = fact.model_dump(mode="json")
    assert dumped["disclosure_status"] == "disclosed"
    assert dumped["extraction_method"] == "llm_structured_output"
    assert isinstance(dumped["disclosure_status"], str)
    assert isinstance(dumped["extraction_method"], str)
    assert '"disclosure_status":"disclosed"' in fact.model_dump_json()
    assert '"extraction_method":"llm_structured_output"' in fact.model_dump_json()


def test_extracted_fact_enum_fields_round_trip_through_json() -> None:
    fact = _disclosed_fact(DisclosureStatus.DISCLOSED, ExtractionMethod.LLM_STRUCTURED_OUTPUT)
    restored = ExtractedFact.model_validate_json(fact.model_dump_json())
    assert restored.disclosure_status is DisclosureStatus.DISCLOSED
    assert restored.extraction_method is ExtractionMethod.LLM_STRUCTURED_OUTPUT
    assert restored == fact


def test_digest_claim_and_digest_enum_fields_serialize_to_exact_lowercase_strings() -> None:
    claim = _digest_claim(ClaimValidationStatus.SUPPORTED)
    assert claim.model_dump(mode="json")["validation_status"] == "supported"
    assert '"validation_status":"supported"' in claim.model_dump_json()

    digest = _digest(DigestStatus.PUBLISHED)
    assert digest.model_dump(mode="json")["status"] == "published"
    assert '"status":"published"' in digest.model_dump_json()


def test_digest_claim_and_digest_enum_fields_round_trip_through_json() -> None:
    claim = _digest_claim(ClaimValidationStatus.SUPPORTED)
    restored_claim = DigestClaim.model_validate_json(claim.model_dump_json())
    assert restored_claim.validation_status is ClaimValidationStatus.SUPPORTED
    assert restored_claim == claim

    digest = _digest(DigestStatus.PUBLISHED)
    restored_digest = Digest.model_validate_json(digest.model_dump_json())
    assert restored_digest.status is DigestStatus.PUBLISHED
    assert restored_digest == digest


@pytest.mark.parametrize(
    ("model", "field_name", "enum_cls"),
    [
        (ExtractedFact, "disclosure_status", DisclosureStatus),
        (ExtractedFact, "extraction_method", ExtractionMethod),
        (DigestClaim, "validation_status", ClaimValidationStatus),
        (Digest, "status", DigestStatus),
    ],
)
def test_model_json_schema_exposes_exact_enum_members(
    model: type[ExtractedFact | DigestClaim | Digest], field_name: str, enum_cls: type[StrEnum]
) -> None:
    """model_json_schema() is what FastAPI/OpenAPI would generate an
    `enum` schema from (ADR 0009/0010) -- assert the exact member list,
    not just that *an* enum shows up, so a future member added to one
    Enum without updating the other can't slip through unnoticed."""
    schema = model.model_json_schema()
    # Pydantic emits a StrEnum as a named $defs entry referenced via $ref,
    # not an inline "enum" list on the field itself.
    enum_def = schema["$defs"][enum_cls.__name__]
    assert enum_def["enum"] == [member.value for member in enum_cls]
    field_schema = schema["properties"][field_name]
    ref = field_schema.get("$ref") or field_schema.get("allOf", [{}])[0].get("$ref")
    assert ref == f"#/$defs/{enum_cls.__name__}"


# --- model_copy(update=...) invariant (ADR 0009): it does not re-validate
# -- callers must pass real Enum members, which these tests do, proving
# the intended usage actually produces a real Enum member on the copy,
# not a plain unvalidated string. ---


def test_model_copy_with_enum_member_produces_a_real_enum_on_the_copy() -> None:
    claim = _digest_claim(ClaimValidationStatus.PENDING)
    updated = claim.model_copy(update={"validation_status": ClaimValidationStatus.SUPPORTED})
    assert updated.validation_status is ClaimValidationStatus.SUPPORTED
    assert isinstance(updated.validation_status, ClaimValidationStatus)

    digest = _digest(DigestStatus.DRAFT)
    published = digest.model_copy(update={"status": DigestStatus.PUBLISHED})
    assert published.status is DigestStatus.PUBLISHED
    assert isinstance(published.status, DigestStatus)


# --- Cross-model handoff: DisclosureStatus (ExtractedFact) into
# FactRow.disclosure_status (compare_subjects.py's intelligence-local,
# deliberately wider three-state Literal, ADR 0006/0009). ---


def test_disclosure_status_member_satisfies_fact_row_literal() -> None:
    # Imported here, not at module level: FactRow is intelligence-local
    # (compare_subjects.py), not part of this file's shared/schemas.py
    # subject matter -- this one test exists specifically to prove the
    # cross-boundary handoff, not to pull compare_subjects.py into every
    # test in this file.
    from ai_daily_digest.intelligence.compare_subjects import FactRow

    for member in DisclosureStatus:
        row = FactRow(
            subject=SUBJECT,
            field="context_window_tokens",
            value="256000" if member is DisclosureStatus.DISCLOSED else None,
            disclosure_status=member.value,
            snapshot_id=SNAPSHOT_1,
        )
        assert row.disclosure_status == member.value


# --- ADR 0008: pagination ordering-field invariants -- Change.detected_at,
# SourceItem.first_fetched_at (both timezone-aware, UTC-normalized,
# microsecond-preserving, frozen), and Digest.digest_date (a real `date`,
# frozen). Every ordering field is also guarded against model_copy(update=
# ...), the one path `frozen=True` alone doesn't cover. ---


def _source_item(first_fetched_at: object) -> SourceItem:
    return SourceItem(
        id=ITEM_1,
        dedupe_key="sha256:x",
        source_id="openai_news",
        publisher="OpenAI",
        title="Example",
        canonical_url="https://example.com/a",  # type: ignore[arg-type]
        first_fetched_at=first_fetched_at,  # type: ignore[arg-type]
    )


NON_UTC_TZ = timezone(timedelta(hours=5, minutes=30))


def test_change_detected_at_accepts_aware_utc_and_preserves_microseconds() -> None:
    value = datetime(2026, 8, 20, 9, 5, 0, 123456, tzinfo=UTC)
    change = _valid_change(detected_at=value)
    assert change.detected_at == value
    assert change.detected_at.microsecond == 123456


def test_change_detected_at_normalizes_aware_non_utc_to_utc() -> None:
    value = datetime(2026, 8, 20, 14, 35, 0, 123456, tzinfo=NON_UTC_TZ)
    change = _valid_change(detected_at=value)
    assert change.detected_at == value  # same instant
    assert change.detected_at.utcoffset() == timedelta(0)
    assert change.detected_at.microsecond == 123456


def test_change_detected_at_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-naive"):
        _valid_change(detected_at=datetime(2026, 8, 20, 9, 5, 0))


def test_source_item_first_fetched_at_accepts_aware_utc_and_preserves_microseconds() -> None:
    value = datetime(2026, 8, 20, 9, 5, 0, 123456, tzinfo=UTC)
    item = _source_item(value)
    assert item.first_fetched_at == value
    assert item.first_fetched_at.microsecond == 123456


def test_source_item_first_fetched_at_normalizes_aware_non_utc_to_utc() -> None:
    value = datetime(2026, 8, 20, 14, 35, 0, 123456, tzinfo=NON_UTC_TZ)
    item = _source_item(value)
    assert item.first_fetched_at == value
    assert item.first_fetched_at.utcoffset() == timedelta(0)


def test_source_item_first_fetched_at_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-naive"):
        _source_item(datetime(2026, 8, 20, 9, 5, 0))


def test_digest_date_accepts_date_and_valid_iso_string() -> None:
    from_date = Digest(id=DIGEST_1, digest_date=date(2026, 9, 2), title="t")
    from_string = Digest(id=DIGEST_1, digest_date="2026-09-02", title="t")  # type: ignore[arg-type]
    assert from_date.digest_date == date(2026, 9, 2)
    assert from_string.digest_date == date(2026, 9, 2)


@pytest.mark.parametrize("bad_value", ["2026-13-40", "", "not-a-date", "2026/09/02"])
def test_digest_date_rejects_invalid_strings(bad_value: str) -> None:
    with pytest.raises(ValidationError):
        Digest(id=DIGEST_1, digest_date=bad_value, title="t")  # type: ignore[arg-type]


def test_digest_date_serializes_to_yyyy_mm_dd() -> None:
    digest = Digest(id=DIGEST_1, digest_date=date(2026, 9, 2), title="t")
    assert digest.model_dump(mode="json")["digest_date"] == "2026-09-02"
    assert '"digest_date":"2026-09-02"' in digest.model_dump_json()


# --- Model immutability: plain attribute reassignment on a protected
# ordering field raises ValidationError (Field(frozen=True)); model_copy(
# update=...) on the same field raises ValueError (the guard, since
# `frozen` alone has no effect on that path). ---


def test_reassigning_a_protected_change_field_raises() -> None:
    change = _valid_change()
    with pytest.raises(ValidationError):
        change.id = CHANGE_SET_1
    with pytest.raises(ValidationError):
        change.detected_at = DETECTED_AT + timedelta(days=1)


def test_reassigning_a_protected_source_item_field_raises() -> None:
    item = _source_item(DETECTED_AT)
    with pytest.raises(ValidationError):
        item.id = CHANGE_SET_1
    with pytest.raises(ValidationError):
        item.first_fetched_at = DETECTED_AT + timedelta(days=1)


def test_reassigning_a_protected_digest_field_raises() -> None:
    digest = _digest(DigestStatus.DRAFT)
    with pytest.raises(ValidationError):
        digest.id = CLAIM_1
    with pytest.raises(ValidationError):
        digest.digest_date = date(2026, 9, 3)


def test_model_copy_on_a_protected_change_field_raises() -> None:
    change = _valid_change()
    with pytest.raises(ValueError, match="protected ordering field"):
        change.model_copy(update={"id": CHANGE_SET_1})
    with pytest.raises(ValueError, match="protected ordering field"):
        change.model_copy(update={"detected_at": DETECTED_AT + timedelta(days=1)})
    # A non-protected field still works.
    copied = change.model_copy(update={"review_status": "validated"})
    assert copied.review_status == "validated"


def test_model_copy_on_a_protected_source_item_field_raises() -> None:
    item = _source_item(DETECTED_AT)
    with pytest.raises(ValueError, match="protected ordering field"):
        item.model_copy(update={"id": CHANGE_SET_1})
    with pytest.raises(ValueError, match="protected ordering field"):
        item.model_copy(update={"first_fetched_at": DETECTED_AT + timedelta(days=1)})
    copied = item.model_copy(update={"title": "New title"})
    assert copied.title == "New title"


def test_model_copy_on_a_protected_digest_field_raises() -> None:
    digest = _digest(DigestStatus.DRAFT)
    with pytest.raises(ValueError, match="protected ordering field"):
        digest.model_copy(update={"id": CLAIM_1})
    with pytest.raises(ValueError, match="protected ordering field"):
        digest.model_copy(update={"digest_date": date(2026, 9, 3)})
    copied = digest.model_copy(update={"status": DigestStatus.REVIEW})
    assert copied.status == DigestStatus.REVIEW


def test_model_copy_with_no_update_is_a_plain_no_op_copy() -> None:
    """The `if not update: return` branch in _reject_protected_field_update()
    -- model_copy() called with no update at all (the common "just clone
    this object" case) must not be treated as touching a protected field."""
    change = _valid_change()
    assert change.model_copy() == change
    assert change.model_copy(update=None) == change
    assert change.model_copy(update={}) == change
