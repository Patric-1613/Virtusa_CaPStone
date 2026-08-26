"""End-to-end test of the orchestrator with injected fake call_fns for
every LLM call site — no network/API key needed. This is the closest
thing to a real run this test suite has: a batch of items goes in, a
published Digest comes out."""

from datetime import UTC, datetime

from ai_daily_digest.intelligence.compare_subjects import (
    ComparisonClaimCandidate,
    ComparisonResponse,
)
from ai_daily_digest.intelligence.daily_run import BatchItem, run_daily
from ai_daily_digest.intelligence.extract_facts import FactCandidate, FactExtractionResponse
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse
from ai_daily_digest.shared.schemas import DocumentSnapshot, SourceItem, Subject

OPENAI_GPT4O = Subject(company="OpenAI", product="GPT-4o")
ANTHROPIC_CLAUDE = Subject(company="Anthropic", product="Claude")


def _item(item_id, title, publisher="OpenAI", source_id="openai_news"):
    return SourceItem(
        id=item_id,
        dedupe_key=f"sha256:{item_id}",
        source_id=source_id,
        publisher=publisher,
        title=title,
        canonical_url=f"https://example.com/{item_id}",
        first_fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def _snapshot(snap_id, item_id, text, fetched_at):
    return DocumentSnapshot(
        id=snap_id,
        source_item_id=item_id,
        fetched_at=fetched_at,
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def _extraction_fake(field, value, quoted_span):
    def fake_call(system, prompt):
        return FactExtractionResponse(
            facts=[
                FactCandidate(field=field, value=value, quoted_span=quoted_span, confidence=0.95)
            ]
        )

    return fake_call


def test_batch_produces_a_published_digest_with_a_change_and_a_comparison():
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    known_snapshot_ids: set[str] = set()

    # Extraction responses differ per item -- route by which snapshot is being processed.
    extraction_responses = {
        "snap_launch": FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="128000",
                    quoted_span="128,000 token context window",
                    confidence=0.95,
                )
            ]
        ),
        "snap_256k": FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="256000",
                    quoted_span="increased to 256,000 tokens",
                    confidence=0.95,
                )
            ]
        ),
        "snap_bench": FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="benchmark_scores",
                    value="71.2",
                    quoted_span="scored 71.2",
                    confidence=0.9,
                )
            ]
        ),
    }

    def extract_fake(system, prompt):
        for snap_id, response in extraction_responses.items():
            if snap_id in prompt:
                return response
        return FactExtractionResponse(facts=[])

    def compare_fake(system, prompt):
        return ComparisonResponse(
            claims=[
                ComparisonClaimCandidate(
                    text=(
                        "OpenAI's GPT-4o has a 256,000-token context window; Anthropic's Claude "
                        "has not disclosed its context window in this update."
                    ),
                    subjects=[OPENAI_GPT4O, ANTHROPIC_CLAUDE],
                    fields=["context_window_tokens"],
                    snapshot_ids=["snap_256k"],
                )
            ]
        )

    batch = [
        BatchItem(
            _item("item_launch", "Introducing GPT-4o"),
            _snapshot(
                "snap_launch",
                "item_launch",
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item("item_256k", "GPT-4o now supports a 256k token context window"),
            _snapshot(
                "snap_256k",
                "item_256k",
                "OpenAI announced GPT-4o's context window has been increased to 256,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(
                "item_bench",
                "Claude benchmark results published",
                publisher="Anthropic",
                source_id="anthropic_news",
            ),
            _snapshot(
                "snap_bench",
                "item_bench",
                "Anthropic published benchmark results; Claude scored 71.2 on ReasonBench.",
                datetime(2026, 8, 19, tzinfo=UTC),
            ),
        ),
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        "2026-08-20",
        alias_table=[],
        extract_call_fn=extract_fake,
        compare_call_fn=compare_fake,
    )

    assert set(result.resolved_subjects) == {OPENAI_GPT4O, ANTHROPIC_CLAUDE}
    assert result.unresolved_item_ids == []

    # one "changed" claim (128k -> 256k) + one comparison claim = 2
    assert len(result.digest.claims) == 2
    assert result.digest.status == "published"
    assert all(c.validation_status == "supported" for c in result.digest.claims)


def test_one_item_raising_does_not_abort_the_rest_of_the_batch():
    """A per-item failure (e.g. a real extract_facts call exhausting its
    retries) must not cost the claims already computed from other items
    in the same batch."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    known_snapshot_ids: set[str] = set()

    def extract_fake(system, prompt):
        if "snap_broken" in prompt:
            raise RuntimeError("simulated transient extraction failure")
        if "snap_launch" in prompt:
            return FactExtractionResponse(
                facts=[
                    FactCandidate(
                        field="context_window_tokens",
                        value="128000",
                        quoted_span="128,000 token context window",
                        confidence=0.95,
                    )
                ]
            )
        return FactExtractionResponse(facts=[])

    batch = [
        BatchItem(
            _item("item_broken", "GPT-4o update"),
            _snapshot(
                "snap_broken",
                "item_broken",
                "OpenAI GPT-4o details.",
                datetime(2026, 8, 19, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item("item_launch", "Introducing GPT-4o"),
            _snapshot(
                "snap_launch",
                "item_launch",
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        ),
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        "2026-08-20",
        alias_table=[],
        extract_call_fn=extract_fake,
    )

    assert result.failed_item_ids == ["item_broken"]
    # the second item still resolved and its fact was recorded, despite
    # the first item raising
    assert store.get_current_fact(OPENAI_GPT4O, "context_window_tokens").value == "128000"


def test_unresolvable_item_is_recorded_not_dropped():
    store = FactStore()
    known_snapshot_ids: set[str] = set()

    def resolve_fake(system, prompt):
        return ResolveLLMResponse(confidence=0.9)  # no proposal

    batch = [
        BatchItem(
            _item("item_mystery", "Completely unrelated headline"),
            _snapshot(
                "snap_mystery",
                "item_mystery",
                "Nothing about a tracked subject here.",
                datetime(2026, 8, 19, tzinfo=UTC),
            ),
        )
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        "2026-08-20",
        alias_table=[],
        resolve_llm_call_fn=resolve_fake,
    )

    assert result.unresolved_item_ids == ["item_mystery"]
    assert result.resolved_subjects == []
    assert result.digest.status == "draft"  # nothing to report
    assert result.digest.claims == []


def test_comparison_skipped_with_fewer_than_two_resolved_subjects():
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[str] = set()

    batch = [
        BatchItem(
            _item("item_launch", "Introducing GPT-4o"),
            _snapshot(
                "snap_launch",
                "item_launch",
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        )
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        "2026-08-20",
        alias_table=[],
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )

    assert result.resolved_subjects == [OPENAI_GPT4O]
    assert result.digest.claims == []  # first observation, no comparison possible either
    assert result.digest.status == "draft"


def test_known_snapshot_ids_accumulate_across_calls():
    """The caller is expected to thread the same known_snapshot_ids set
    across daily runs -- verify it actually grows rather than being
    reset each call."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[str] = {"snap_from_a_previous_run"}

    batch = [
        BatchItem(
            _item("item_launch", "Introducing GPT-4o"),
            _snapshot(
                "snap_launch",
                "item_launch",
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        )
    ]

    run_daily(
        store,
        known_snapshot_ids,
        batch,
        "2026-08-20",
        alias_table=[],
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )

    assert "snap_from_a_previous_run" in known_snapshot_ids
    assert "snap_launch" in known_snapshot_ids
