"""Tests the code-level guardrails with an injected fake call_fn — no
network/API key needed. This is the adversarial suite: every test that
matters here is "the model tried something ungrounded and the code caught
it", not just "the happy path works"."""

from datetime import UTC, datetime

from ai_daily_digest.intelligence.compare_subjects import (
    ComparisonClaimCandidate,
    ComparisonResponse,
    FactRow,
    build_fact_table,
    compare_subjects,
)
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.shared.schemas import ExtractedFact, Subject

OPENAI_GPT4O = Subject(company="OpenAI", product="GPT-4o")
ANTHROPIC_CLAUDE = Subject(company="Anthropic", product="Claude")


def _fact(field: str, value: str, snapshot_id: str, fact_id: str = "f1") -> ExtractedFact:
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=value,
        extraction_method="llm_structured_output",
        extraction_model="claude-sonnet-5",
        prompt_version="v1",
    )


def _store_with_data() -> FactStore:
    store = FactStore()
    store.update_fact(
        OPENAI_GPT4O,
        _fact("context_window_tokens", "256000", "snap_openai_ctx"),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    store.update_fact(
        ANTHROPIC_CLAUDE,
        _fact("benchmark_scores", "71.2", "snap_anthropic_bench"),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    return store


def _rows() -> list[FactRow]:
    store = _store_with_data()
    return build_fact_table(
        store,
        [OPENAI_GPT4O, ANTHROPIC_CLAUDE],
        ["context_window_tokens", "benchmark_scores"],
    )


def test_build_fact_table_marks_missing_fields_not_disclosed() -> None:
    rows = _rows()
    openai_bench = next(
        r for r in rows if r.subject == OPENAI_GPT4O and r.field == "benchmark_scores"
    )
    assert openai_bench.value is None
    assert openai_bench.snapshot_id is None

    openai_ctx = next(
        r for r in rows if r.subject == OPENAI_GPT4O and r.field == "context_window_tokens"
    )
    assert openai_ctx.value == "256000"
    assert openai_ctx.snapshot_id == "snap_openai_ctx"


def test_well_grounded_comparison_is_accepted() -> None:
    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text=(
                        "OpenAI's GPT-4o has a 256,000-token context window; Anthropic's Claude "
                        "has not disclosed its context window in this update."
                    ),
                    subjects=[OPENAI_GPT4O, ANTHROPIC_CLAUDE],
                    fields=["context_window_tokens"],
                    snapshot_ids=["snap_openai_ctx"],
                )
            ]
        )

    claims = compare_subjects(_rows(), call_fn=fake_call)
    assert len(claims) == 1
    assert claims[0].citation_snapshot_ids == ["snap_openai_ctx"]
    assert claims[0].validation_status == "pending"


def test_fabricated_snapshot_citation_is_rejected() -> None:
    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="A confident-sounding but ungrounded claim.",
                    subjects=[OPENAI_GPT4O, ANTHROPIC_CLAUDE],
                    fields=["context_window_tokens"],
                    snapshot_ids=["snap_that_does_not_exist"],
                )
            ]
        )

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_citation_borrowed_from_an_unrelated_subject_field_is_rejected() -> None:
    """A real snapshot id that exists in the table, but supports a
    *different* subject/field than the one being claimed about, must not
    count as grounding -- this is the specific gap a real citation cross-
    check has to catch, not just "is this id real somewhere"."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="OpenAI's GPT-4o price beats Anthropic's Claude price.",
                    subjects=[OPENAI_GPT4O, ANTHROPIC_CLAUDE],
                    fields=["context_window_tokens"],
                    # snap_anthropic_bench is real, but it's Anthropic's
                    # benchmark_scores snapshot, not a context_window_tokens
                    # snapshot for either subject.
                    snapshot_ids=["snap_anthropic_bench"],
                )
            ]
        )

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_false_comparison_value_with_real_citation_is_rejected() -> None:
    """Adversarial case per the review: the citation id is real AND
    correctly owned by (OpenAI, context_window_tokens) -- the existing
    ownership check alone would accept this -- but the claim's prose
    states a number ("999999") the row never actually had."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="OpenAI's GPT-4o context window is 999999 tokens.",
                    subjects=[OPENAI_GPT4O, ANTHROPIC_CLAUDE],
                    fields=["context_window_tokens"],
                    snapshot_ids=["snap_openai_ctx"],
                )
            ]
        )

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_unknown_field_is_rejected() -> None:
    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="Comparing on a field never in the table.",
                    subjects=[OPENAI_GPT4O, ANTHROPIC_CLAUDE],
                    fields=["made_up_field"],
                    snapshot_ids=["snap_openai_ctx"],
                )
            ]
        )

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_unknown_subject_is_rejected() -> None:
    intruder = Subject(company="MadeUp Inc", product="Ghost Model")

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="Comparing against a subject never in the table.",
                    subjects=[OPENAI_GPT4O, intruder],
                    fields=["context_window_tokens"],
                    snapshot_ids=["snap_openai_ctx"],
                )
            ]
        )

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_swapped_real_values_are_not_yet_caught_known_gap() -> None:
    """KNOWN, DOCUMENTED GAP -- not a passing safety guarantee. Both
    numbers are real and both citations are correctly owned, but the
    prose attributes them to the WRONG subject (swapped). The current
    per-field numeric check only verifies "every number in the claim
    text is SOMEWHERE among the real values being compared" (a set
    union) -- it has no way to verify WHICH subject a number belongs to
    in the sentence, so a swap like this is currently accepted.

    This is exactly why docs/DESIGN_PROPOSAL_comparison_and_grounding.md
    proposes structured (subject, field) -> value assertions instead of
    free text (see its (a)/(b) sections) -- once that ships, this test's
    assertion should flip to == []. Until then, daily_run.py's
    _never_auto_publish_comparisons() is what actually stops a claim
    like this from reaching a subscriber -- see
    test_daily_run.py::test_swapped_comparison_never_auto_publishes_even_though_compare_subjects_accepts_it.
    """
    store = FactStore()
    store.update_fact(
        OPENAI_GPT4O,
        _fact("input_price_usd", "5", "snap_openai_price"),
        source_url="https://openai.com/pricing",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    store.update_fact(
        ANTHROPIC_CLAUDE,
        _fact("input_price_usd", "3", "snap_anthropic_price"),
        source_url="https://anthropic.com/pricing",
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    rows = build_fact_table(store, [OPENAI_GPT4O, ANTHROPIC_CLAUDE], ["input_price_usd"])

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        # Real: OpenAI=5, Anthropic=3. Claim states it backwards.
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="OpenAI's input price is 3; Anthropic's input price is 5.",
                    subjects=[OPENAI_GPT4O, ANTHROPIC_CLAUDE],
                    fields=["input_price_usd"],
                    snapshot_ids=["snap_openai_price", "snap_anthropic_price"],
                )
            ]
        )

    claims = compare_subjects(rows, call_fn=fake_call)
    assert len(claims) == 1  # accepted -- the gap this test documents


def test_subject_compared_to_itself_is_rejected() -> None:
    """Per the third review: reject comparisons where subject_a ==
    subject_b -- a subject can't be legitimately compared to itself."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="OpenAI's GPT-4o compared to OpenAI's GPT-4o.",
                    subjects=[OPENAI_GPT4O, OPENAI_GPT4O],
                    fields=["context_window_tokens"],
                    snapshot_ids=["snap_openai_ctx"],
                )
            ]
        )

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_single_subject_claim_is_rejected() -> None:
    """A "comparison" naming only one subject isn't a comparison."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text="Only about one subject.",
                    subjects=[OPENAI_GPT4O],
                    fields=["context_window_tokens"],
                    snapshot_ids=["snap_openai_ctx"],
                )
            ]
        )

    assert compare_subjects(_rows(), call_fn=fake_call) == []


def test_sparse_table_yields_abstention_not_a_fabricated_claim() -> None:
    """Adversarial check per the original design: feed a sparse table and
    confirm the (simulated) model declining to compare is accepted as
    correct output, not treated as a failure."""

    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(claims=[])

    empty_rows = [FactRow(subject=OPENAI_GPT4O, field="context_window_tokens")]
    assert compare_subjects(empty_rows, call_fn=fake_call) == []
