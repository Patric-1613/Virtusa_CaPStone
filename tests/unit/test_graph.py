"""Tests the compiled graph end-to-end with injected fake call_fns for
every LLM call site — no network/API key needed. This exercises the
wiring (state passing, conditional routing) on top of logic that's
already covered by each node's own unit tests."""

from datetime import UTC, datetime

from ai_daily_digest.intelligence.extract_facts import FactCandidate, FactExtractionResponse
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.graph import build_graph
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse
from ai_daily_digest.shared.schemas import DocumentSnapshot, SourceItem, Subject

OPENAI_GPT4O = Subject(company="OpenAI", product="GPT-4o")


def _item(item_id, title, canonical_url="https://openai.com/a"):
    return SourceItem(
        id=item_id,
        dedupe_key=f"sha256:{item_id}",
        source_id="openai_news",
        publisher="OpenAI",
        title=title,
        canonical_url=canonical_url,
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


def _extraction_fake(field, value, quoted_span, confidence=0.95):
    def fake_call(system, prompt):
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field=field, value=value, quoted_span=quoted_span, confidence=confidence
                )
            ]
        )

    return fake_call


def test_first_observation_produces_no_claims():
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)  # already tracked -- deterministic matching needs this
    graph = build_graph(
        store,
        alias_table=[],
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )
    item = _item("item_launch", "Introducing GPT-4o")
    snapshot = _snapshot(
        "snap_launch",
        "item_launch",
        "OpenAI is launching GPT-4o with a 128,000 token context window.",
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    result = graph.invoke({"item": item, "snapshot": snapshot})

    assert result["subject"] == OPENAI_GPT4O
    assert result["claims"] == []  # first observation, not a change
    assert store.get_current_fact(OPENAI_GPT4O, "context_window_tokens").value == "128000"


def test_changed_value_flows_through_to_a_supported_claim():
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)  # already tracked -- deterministic matching needs this
    launch_graph = build_graph(
        store,
        alias_table=[],
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )
    launch_item = _item("item_launch", "Introducing GPT-4o")
    launch_snapshot = _snapshot(
        "snap_launch",
        "item_launch",
        "OpenAI is launching GPT-4o with a 128,000 token context window.",
        datetime(2026, 6, 2, tzinfo=UTC),
    )
    launch_graph.invoke({"item": launch_item, "snapshot": launch_snapshot})

    update_graph = build_graph(
        store,
        alias_table=[],
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "256000", "increased to 256,000 tokens"
        ),
    )
    update_item = _item("item_256k", "GPT-4o now supports a 256k token context window")
    update_snapshot = _snapshot(
        "snap_256k",
        "item_256k",
        "OpenAI today announced GPT-4o's context window has been increased to 256,000 tokens.",
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    result = update_graph.invoke({"item": update_item, "snapshot": update_snapshot})

    assert result["subject"] == OPENAI_GPT4O
    assert len(result["changes"]) == 1
    assert result["changes"][0].previous.value == "128000"
    assert result["changes"][0].current.value == "256000"
    assert len(result["claims"]) == 1
    claim = result["claims"][0]
    assert "256000" in claim.text
    assert claim.validation_status == "supported"
    assert set(claim.citation_snapshot_ids) == {"snap_launch", "snap_256k"}


def test_llm_fallback_resolves_when_deterministic_matching_fails():
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)  # known, but not findable by phrase match below

    def resolve_fake(system, prompt):
        return ResolveLLMResponse(company="OpenAI", product="GPT-4o", confidence=0.9)

    graph = build_graph(
        store,
        alias_table=[],
        resolve_llm_call_fn=resolve_fake,
        extract_call_fn=_extraction_fake("benchmark_scores", "71.2", "scored 71.2"),
    )
    item = _item("item_ambiguous", "New model scores 71.2 on ReasonBench")
    snapshot = _snapshot(
        "snap_amb",
        "item_ambiguous",
        "The new model scored 71.2 on the ReasonBench suite.",
        datetime(2026, 8, 19, tzinfo=UTC),
    )
    result = graph.invoke({"item": item, "snapshot": snapshot})

    assert result["subject"] == OPENAI_GPT4O
    assert result["resolution"].method == "llm_resolved"


def test_unresolvable_item_ends_early_with_no_facts_or_claims():
    store = FactStore()

    def resolve_fake(system, prompt):
        return ResolveLLMResponse(confidence=0.9)  # no company/product proposed

    graph = build_graph(store, alias_table=[], resolve_llm_call_fn=resolve_fake)
    item = _item("item_unknown", "Completely unrelated headline")
    snapshot = _snapshot(
        "snap_unknown",
        "item_unknown",
        "Nothing about a tracked subject here.",
        datetime(2026, 8, 19, tzinfo=UTC),
    )
    result = graph.invoke({"item": item, "snapshot": snapshot})

    assert result["subject"] is None
    assert result.get("facts", []) == []
    assert result.get("claims", []) == []
