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


def _fact(field, value, snapshot_id, fact_id="f1"):
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=value,
        extraction_method="llm_structured_output",
        extraction_model="claude-sonnet-5",
        prompt_version="v1",
    )


def _store_with_data():
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


def _rows():
    store = _store_with_data()
    return build_fact_table(
        store,
        [OPENAI_GPT4O, ANTHROPIC_CLAUDE],
        ["context_window_tokens", "benchmark_scores"],
    )


def test_build_fact_table_marks_missing_fields_not_disclosed():
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


def test_well_grounded_comparison_is_accepted():
    def fake_call(system, prompt):
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


def test_fabricated_snapshot_citation_is_rejected():
    def fake_call(system, prompt):
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


def test_citation_borrowed_from_an_unrelated_subject_field_is_rejected():
    """A real snapshot id that exists in the table, but supports a
    *different* subject/field than the one being claimed about, must not
    count as grounding -- this is the specific gap a real citation cross-
    check has to catch, not just "is this id real somewhere"."""

    def fake_call(system, prompt):
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


def test_unknown_field_is_rejected():
    def fake_call(system, prompt):
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


def test_unknown_subject_is_rejected():
    intruder = Subject(company="MadeUp Inc", product="Ghost Model")

    def fake_call(system, prompt):
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


def test_single_subject_claim_is_rejected():
    """A "comparison" naming only one subject isn't a comparison."""

    def fake_call(system, prompt):
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


def test_sparse_table_yields_abstention_not_a_fabricated_claim():
    """Adversarial check per the original design: feed a sparse table and
    confirm the (simulated) model declining to compare is accepted as
    correct output, not treated as a failure."""

    def fake_call(system, prompt):
        return ComparisonResponse(claims=[])

    empty_rows = [FactRow(subject=OPENAI_GPT4O, field="context_window_tokens")]
    assert compare_subjects(empty_rows, call_fn=fake_call) == []
