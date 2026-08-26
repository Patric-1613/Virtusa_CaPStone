"""End-to-end test of the orchestrator with injected fake call_fns for
every LLM call site — no network/API key needed. This is the closest
thing to a real run this test suite has: a batch of items goes in, a
published Digest comes out."""

from collections.abc import Callable
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


def _item(
    item_id: str, title: str, publisher: str = "OpenAI", source_id: str = "openai_news"
) -> SourceItem:
    return SourceItem(
        id=item_id,
        dedupe_key=f"sha256:{item_id}",
        source_id=source_id,
        publisher=publisher,
        title=title,
        canonical_url=f"https://example.com/{item_id}",  # type: ignore[arg-type]
        first_fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def _snapshot(snap_id: str, item_id: str, text: str, fetched_at: datetime) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id=item_id,
        fetched_at=fetched_at,
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def _extraction_fake(
    field: str, value: str, quoted_span: str
) -> Callable[[str, str], FactExtractionResponse]:
    def fake_call(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(field=field, value=value, quoted_span=quoted_span, confidence=0.95)
            ]
        )

    return fake_call


def test_batch_produces_a_published_digest_with_a_change_and_a_comparison() -> None:
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

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        for snap_id, response in extraction_responses.items():
            if snap_id in prompt:
                return response
        return FactExtractionResponse(facts=[])

    def compare_fake(system: str, prompt: str) -> ComparisonResponse:
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

    # the one Change (128k -> 256k) is grouped into one ChangeSet, with
    # its change_set_id backfilled -- not left empty (see change_sets.py)
    assert len(result.change_sets) == 1
    change_set = result.change_sets[0]
    assert change_set.subject == OPENAI_GPT4O
    assert len(change_set.changes) == 1
    assert change_set.changes[0].change_set_id == change_set.id
    assert change_set.changes[0].change_set_id != ""


def test_known_snapshot_content_is_threaded_through_to_the_final_publish_gate() -> None:
    """run_daily builds snapshots_by_id from the batch's real
    DocumentSnapshots and threads it to assemble_digest()/validate.py --
    a regression test for the plumbing itself (the adversarial content-
    grounding cases are covered directly in test_validate.py; this
    confirms run_daily actually wires it up, not just that validate.py's
    own check works in isolation)."""
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
    ]

    # Each snapshot needs its own grounded extraction response -- a
    # single fixed quoted_span for both would fail snap_256k's own
    # grounding check (its text doesn't contain "128,000 token context
    # window" verbatim), so route by which snapshot's text is in the
    # prompt, the same way the first test in this file does.
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
    }

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        for snap_id, response in extraction_responses.items():
            if snap_id in prompt:
                return response
        return FactExtractionResponse(facts=[])

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        "2026-08-20",
        alias_table=[],
        extract_call_fn=extract_fake,
    )

    # The real drafted "changed" claim (128k -> 256k) is correctly
    # grounded in the batch's own snapshot content, so it still
    # publishes with the content-aware gate active -- draft_claims.py's
    # claims are always genuinely grounded by construction, so a real
    # end-to-end run can't produce the adversarial case; see
    # test_validate.py for the direct test proving the content-aware
    # check actually rejects an ungrounded claim when it sees one.
    assert result.digest.status == "published"
    assert all(c.validation_status == "supported" for c in result.digest.claims)


def test_one_item_raising_does_not_abort_the_rest_of_the_batch() -> None:
    """A per-item failure (e.g. a real extract_facts call exhausting its
    retries) must not cost the claims already computed from other items
    in the same batch."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    known_snapshot_ids: set[str] = set()

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
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
    current = store.get_current_fact(OPENAI_GPT4O, "context_window_tokens")
    assert current is not None
    assert current.value == "128000"


def test_unresolvable_item_is_recorded_not_dropped() -> None:
    store = FactStore()
    known_snapshot_ids: set[str] = set()

    def resolve_fake(system: str, prompt: str) -> ResolveLLMResponse:
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


def test_comparison_skipped_with_fewer_than_two_resolved_subjects() -> None:
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


def test_known_snapshot_ids_accumulate_across_calls() -> None:
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
