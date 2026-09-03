"""End-to-end test of the orchestrator with injected fake call_fns for
every LLM call site — no network/API key needed. This is the closest
thing to a real run this test suite has: a batch of items goes in, a
Digest comes out.

ADR 0005: compare_fake functions below propose structured
ComparisonAssertion(subject_a, subject_b, field) triples only -- no text,
no snapshot ids, no values. compare_subjects() looks those up and renders
the claim deterministically; see test_compare_subjects.py for the
guardrail-level coverage of that resolution. This file only needs to
prove daily_run.py wires everything together end to end.

Routing note: extract_fake() functions below route their fake response
by checking whether a given snapshot's id (its canonical string form)
appears in the rendered prompt -- extract_facts.py embeds
`str(snapshot.id)` verbatim (see its own "Snapshot id: {{snapshot_id}}"
prompt line), so this works the same way the old "snap_launch" in prompt
substring checks did pre-ADR-0007, just keyed by UUID string instead of
a descriptive label.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from ai_daily_digest.intelligence.compare_subjects import ComparisonAssertion, ComparisonResponse
from ai_daily_digest.intelligence.daily_run import BatchItem, run_daily
from ai_daily_digest.intelligence.extract_facts import FactCandidate, FactExtractionResponse
from ai_daily_digest.intelligence.facts import FactStore
from ai_daily_digest.intelligence.resolve_llm import ResolveLLMResponse
from ai_daily_digest.shared.schemas import (
    DisclosureStatus,
    DocumentSnapshot,
    ExtractedFact,
    ExtractionMethod,
    SourceItem,
    Subject,
)
from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver

# The orchestrator's injected batch detection time (ADR 0008 section 5.A) --
# .250000 microseconds on purpose, so tests can assert it survives intact.
TDR_DETECTED_AT = datetime(2026, 8, 20, 12, 0, 0, 250000, tzinfo=UTC)

OPENAI_GPT4O = Subject(company="OpenAI", product="GPT-4o")
ANTHROPIC_CLAUDE = Subject(company="Anthropic", product="Claude")

TDR_ITEM_LAUNCH = uuid.UUID("019e860f-2500-7e53-b080-0bf532479738")
TDR_ITEM_256K = uuid.UUID("01a01ce5-8900-7521-a4d7-58c6aad9845b")
TDR_ITEM_ANTHROPIC_CTX = uuid.UUID("01a017bf-2d00-7d90-87ae-c7d1f45b0aef")
TDR_ITEM_OPENAI_CTX = uuid.UUID("01a01ce5-8ce8-7ce3-8010-7c1413b263ba")
TDR_ITEM_OPENAI_R = uuid.UUID("01a01ce5-90d0-7621-97bb-289825462537")
TDR_ITEM_ANTHROPIC_R = uuid.UUID("01a01ce5-94b8-7f90-b54e-ab80d9776453")
TDR_ITEM_BROKEN = uuid.UUID("01a017bf-3ca0-7613-b3f4-74644c73f757")
TDR_ITEM_CONFLICT_2 = uuid.UUID("01a01ce5-9c88-78d1-a43a-d19b89f9e9be")
TDR_ITEM_VALID_1 = uuid.UUID("01a01ce5-a070-75d3-bc7a-2a152ecd575e")
TDR_ITEM_VALID_3 = uuid.UUID("01a01ce5-a458-7630-beaa-c2a57f85a2c9")
TDR_ITEM_MYSTERY = uuid.UUID("01a017bf-4c40-7e01-a75b-d7a77cf43130")
TDR_ITEM_OPENAI_SEED = uuid.UUID("019e80e8-c900-7de0-a271-4d5b63107bda")
TDR_ITEM_ANTHROPIC_SEED = uuid.UUID("019e80e8-cce8-7f52-baaf-4325a80dcbe3")
TDR_ITEM_PRE_EXISTING = uuid.UUID("019fbb0c-b500-78b0-92c3-b711e0bb6845")
TDR_SNAP_LAUNCH = uuid.UUID("019e8610-0f60-7f30-a0ba-af7311a9e28a")
TDR_SNAP_256K = uuid.UUID("01a01ce6-7360-7a02-94e9-c39b2b3a0884")
TDR_SNAP_ANTHROPIC_CTX = uuid.UUID("01a017c0-1760-78f0-b9fa-3780439bc199")
TDR_SNAP_OPENAI_CTX = uuid.UUID("01a01ce6-7748-7100-9cb5-1cdc564c20d9")
TDR_SNAP_OPENAI_R = uuid.UUID("01a01ce6-7b30-7ac2-836f-0c1a3baaa614")
TDR_SNAP_ANTHROPIC_R = uuid.UUID("01a01ce6-7f18-7721-9135-cf50d16daaf7")
TDR_SNAP_BROKEN = uuid.UUID("01a017c0-2700-7541-a153-86cab3f7b6ae")
TDR_SNAP_CONFLICT = uuid.UUID("01a01ce6-86e8-77e1-8fe8-0c393493c7c1")
TDR_SNAP_VALID_1 = uuid.UUID("01a01ce6-8ad0-7ad0-9088-fc23421c52a4")
TDR_SNAP_VALID_3 = uuid.UUID("01a01ce6-8eb8-7f43-b7aa-76111e0f9b54")
TDR_SNAP_MYSTERY = uuid.UUID("01a017c0-36a0-72e2-b179-ce4e377b5772")
TDR_SNAP_OPENAI_SEED = uuid.UUID("019e80e9-b360-7a42-8916-1c171b5523c1")
TDR_SNAP_ANTHROPIC_SEED = uuid.UUID("019e80e9-b748-7550-bba4-a161098e8a99")
TDR_SNAP_FROM_A_PREVIOUS_RUN = uuid.UUID("01a01299-bb60-7680-a8eb-eb8abc6ffb35")
TDR_FACT_SEED_OPENAI = uuid.UUID("019e80ea-9dc0-7fd0-ae5e-3e5417994010")
TDR_FACT_SEED_ANTHROPIC = uuid.UUID("019e80ea-a1a8-7400-8b12-c32efea1184c")
TDR_FACT_SEED = uuid.UUID("01a01ce7-6590-7752-b47a-3191db7228dd")
TDR_ITEM_PRICE_WITHHELD = uuid.UUID("01a0627c-d1c0-78f3-85b3-d9ca2134d302")
TDR_SNAP_PRICE_WITHHELD = uuid.UUID("01a0627c-d1c0-78f3-85b3-d9daad3ad57c")
TDR_FACT_PRICE_WITHHELD = uuid.UUID("01a0627c-d1c1-7e32-a155-f03173402ee9")
TDR_ITEM_PRICE_DISCLOSED = uuid.UUID("01a0627c-d1c1-7e32-a155-f04a6b9f5fac")
TDR_SNAP_PRICE_DISCLOSED = uuid.UUID("01a0627c-d1c1-7e32-a155-f05321a1db2b")


def _item(
    item_id: uuid.UUID,
    title: str,
    publisher: str = "OpenAI",
    source_id: str = "openai_news",
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


def _snapshot(
    snap_id: uuid.UUID, item_id: uuid.UUID, text: str, fetched_at: datetime
) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=snap_id,
        source_item_id=item_id,
        fetched_at=fetched_at,
        content_hash=f"sha256:{snap_id}",
        content_text=text,
    )


def _fact(
    field: str, value: str, snapshot_id: uuid.UUID, fact_id: uuid.UUID = TDR_FACT_SEED
) -> ExtractedFact:
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=value,
        extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
        extraction_model="claude-sonnet-5",
        prompt_version="v1",
        quoted_span=f"quote containing {value}",
        confidence=0.9,
    )


def _not_disclosed_fact(
    field: str, snapshot_id: uuid.UUID, fact_id: uuid.UUID = TDR_FACT_SEED
) -> ExtractedFact:
    return ExtractedFact(
        id=fact_id,
        snapshot_id=snapshot_id,
        field=field,
        value=None,
        disclosure_status=DisclosureStatus.NOT_DISCLOSED,
        extraction_method=ExtractionMethod.LLM_STRUCTURED_OUTPUT,
        extraction_model="claude-sonnet-5",
        prompt_version="v1",
        quoted_span="pricing has not been announced",
        confidence=0.9,
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


def _compare_fake(
    subject_a: Subject, subject_b: Subject, field: str
) -> Callable[[str, str], ComparisonResponse]:
    def fake_call(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(
            assertions=[ComparisonAssertion(subject_a=subject_a, subject_b=subject_b, field=field)]
        )

    return fake_call


def test_batch_produces_a_digest_with_a_change_and_a_comparison() -> None:
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    known_snapshot_ids: set[uuid.UUID] = set()

    # Extraction responses differ per item -- route by which snapshot is being processed.
    extraction_responses = {
        TDR_SNAP_LAUNCH: FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="128000",
                    quoted_span="128,000 token context window",
                    confidence=0.95,
                )
            ]
        ),
        TDR_SNAP_256K: FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="256000",
                    quoted_span="increased to 256,000 tokens",
                    confidence=0.95,
                )
            ]
        ),
        TDR_SNAP_ANTHROPIC_CTX: FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="200000",
                    quoted_span="context window is 200,000 tokens",
                    confidence=0.9,
                )
            ]
        ),
    }

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        for snap_id, response in extraction_responses.items():
            if str(snap_id) in prompt:
                return response
        return FactExtractionResponse(facts=[])

    batch = [
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(TDR_ITEM_256K, "GPT-4o now supports a 256k token context window"),
            _snapshot(
                TDR_SNAP_256K,
                TDR_ITEM_256K,
                "OpenAI announced GPT-4o's context window has been increased to 256,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(
                TDR_ITEM_ANTHROPIC_CTX,
                "Claude context window disclosed",
                publisher="Anthropic",
                source_id="anthropic_news",
            ),
            _snapshot(
                TDR_SNAP_ANTHROPIC_CTX,
                TDR_ITEM_ANTHROPIC_CTX,
                "Anthropic disclosed that Claude's context window is 200,000 tokens.",
                datetime(2026, 8, 19, tzinfo=UTC),
            ),
        ),
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=extract_fake,
        compare_call_fn=_compare_fake(OPENAI_GPT4O, ANTHROPIC_CLAUDE, "context_window_tokens"),
    )

    assert set(result.resolved_subjects) == {OPENAI_GPT4O, ANTHROPIC_CLAUDE}
    assert result.unresolved_item_ids == []

    # one "changed" claim (128k -> 256k) + one deterministically-rendered
    # comparison claim (256k vs 200k) = 2. Anthropic's own context window
    # is a first-time observation (200k, no previous value) -- FactStore
    # still records it (compare_subjects() can see and cite it), but
    # per facts.py::update_fact()'s own docstring, a first observation
    # never becomes its own DigestClaim -- only a real Change does.
    assert len(result.digest.claims) == 2
    assert all(c.validation_status == "supported" for c in result.digest.claims)
    comparison_claim = next(c for c in result.digest.claims if "than" in c.text)
    assert comparison_claim.text == (
        "OpenAI's GPT-4o has a higher context window (256000) than Anthropic's Claude (200000)."
    )
    # Interim safety policy: a digest containing ANY comparison claim
    # never auto-publishes, even when every claim in it individually
    # validates as "supported" -- see
    # daily_run.py::_never_auto_publish_comparisons.
    assert result.digest.status == "review"

    # the one Change (128k -> 256k) is grouped into one ChangeSet, with a
    # real change_set_id allocated lazily -- never a placeholder (ADR 0007)
    assert len(result.change_sets) == 1
    change_set = result.change_sets[0]
    assert change_set.subject == OPENAI_GPT4O
    assert len(change_set.changes) == 1
    assert change_set.changes[0].change_set_id == change_set.id


def test_well_grounded_comparison_still_never_auto_publishes() -> None:
    """The interim safety policy (_never_auto_publish_comparisons) is
    intentionally conservative: even a comparison claim that is fully
    correct, deterministically rendered, and content-grounded by
    construction (ADR 0005) must still route the digest to review, not
    auto-publish. Compare_subjects() closing the swapped-value and
    qualitative-claim fabrication classes at the source doesn't relax
    this policy -- it's a second, independent line of defense, not a
    stand-in for the first."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    known_snapshot_ids: set[uuid.UUID] = set()

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        if str(TDR_SNAP_OPENAI_CTX) in prompt:
            return FactExtractionResponse(
                facts=[
                    FactCandidate(
                        field="context_window_tokens",
                        value="256000",
                        quoted_span="context window of 256,000 tokens",
                        confidence=0.9,
                    )
                ]
            )
        if str(TDR_SNAP_ANTHROPIC_CTX) in prompt:
            return FactExtractionResponse(
                facts=[
                    FactCandidate(
                        field="context_window_tokens",
                        value="128000",
                        quoted_span="context window of 128,000 tokens",
                        confidence=0.9,
                    )
                ]
            )
        return FactExtractionResponse(facts=[])

    batch = [
        BatchItem(
            _item(TDR_ITEM_OPENAI_CTX, "GPT-4o context window"),
            _snapshot(
                TDR_SNAP_OPENAI_CTX,
                TDR_ITEM_OPENAI_CTX,
                "OpenAI's GPT-4o has a context window of 256,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(
                TDR_ITEM_ANTHROPIC_CTX,
                "Claude context window",
                publisher="Anthropic",
                source_id="anthropic_news",
            ),
            _snapshot(
                TDR_SNAP_ANTHROPIC_CTX,
                TDR_ITEM_ANTHROPIC_CTX,
                "Anthropic's Claude has a context window of 128,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=extract_fake,
        compare_call_fn=_compare_fake(OPENAI_GPT4O, ANTHROPIC_CLAUDE, "context_window_tokens"),
    )

    # Both subjects' context windows are first-time observations (no
    # Change claim -- see facts.py::update_fact()'s docstring), so the
    # only claim in the digest is the comparison itself.
    assert len(result.digest.claims) == 1
    assert all(c.validation_status == "supported" for c in result.digest.claims)
    assert result.digest.status == "review"  # never auto-published, however well-grounded


def test_reversed_pair_proposal_still_renders_the_correct_non_swapped_text() -> None:
    """The exact fabrication class the pre-ADR-0005 design could only
    document as a known gap (a model attributing two real numbers to the
    wrong subject) is now structurally impossible end to end: the model
    can propose the pair in whichever order it likes, but it never gets
    to say which number belongs to which subject -- code always does,
    from the real stored values."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    known_snapshot_ids: set[uuid.UUID] = set()

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        if str(TDR_SNAP_OPENAI_R) in prompt:
            return FactExtractionResponse(
                facts=[
                    FactCandidate(
                        field="context_window_tokens",
                        value="256000",
                        quoted_span="context window of 256,000 tokens",
                        confidence=0.9,
                    )
                ]
            )
        if str(TDR_SNAP_ANTHROPIC_R) in prompt:
            return FactExtractionResponse(
                facts=[
                    FactCandidate(
                        field="context_window_tokens",
                        value="128000",
                        quoted_span="context window of 128,000 tokens",
                        confidence=0.9,
                    )
                ]
            )
        return FactExtractionResponse(facts=[])

    batch = [
        BatchItem(
            _item(TDR_ITEM_OPENAI_R, "GPT-4o context window"),
            _snapshot(
                TDR_SNAP_OPENAI_R,
                TDR_ITEM_OPENAI_R,
                "OpenAI's GPT-4o has a context window of 256,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(
                TDR_ITEM_ANTHROPIC_R,
                "Claude context window",
                publisher="Anthropic",
                source_id="anthropic_news",
            ),
            _snapshot(
                TDR_SNAP_ANTHROPIC_R,
                TDR_ITEM_ANTHROPIC_R,
                "Anthropic's Claude has a context window of 128,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
    ]

    # The proposal names Anthropic first, OpenAI second -- reversed from
    # the previous test -- yet the rendered relation must still be
    # correct: Anthropic (128000) really is lower than OpenAI (256000).
    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=extract_fake,
        compare_call_fn=_compare_fake(ANTHROPIC_CLAUDE, OPENAI_GPT4O, "context_window_tokens"),
    )

    comparison_claim = next(c for c in result.digest.claims if "than" in c.text)
    assert comparison_claim.text == (
        "Anthropic's Claude has a lower context window (128000) than OpenAI's GPT-4o (256000)."
    )
    assert result.digest.status == "review"  # never auto-published, swapped or not


def test_known_snapshot_content_is_threaded_through_to_the_final_publish_gate() -> None:
    """run_daily builds an InMemorySnapshotResolver from the batch's real
    DocumentSnapshots and threads it to assemble_digest()/validate.py
    (ADR 0005) -- a regression test for the plumbing itself (the
    adversarial content-grounding cases are covered directly in
    test_validate.py; this confirms run_daily actually wires it up, not
    just that validate.py's own check works in isolation)."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[uuid.UUID] = set()

    batch = [
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(TDR_ITEM_256K, "GPT-4o now supports a 256k token context window"),
            _snapshot(
                TDR_SNAP_256K,
                TDR_ITEM_256K,
                "OpenAI announced GPT-4o's context window has been increased to 256,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
    ]

    # Each snapshot needs its own grounded extraction response -- a
    # single fixed quoted_span for both would fail TDR_SNAP_256K's own
    # grounding check (its text doesn't contain "128,000 token context
    # window" verbatim), so route by which snapshot's id is in the
    # prompt, the same way the first test in this file does.
    extraction_responses = {
        TDR_SNAP_LAUNCH: FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="context_window_tokens",
                    value="128000",
                    quoted_span="128,000 token context window",
                    confidence=0.95,
                )
            ]
        ),
        TDR_SNAP_256K: FactExtractionResponse(
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
            if str(snap_id) in prompt:
                return response
        return FactExtractionResponse(facts=[])

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
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
    known_snapshot_ids: set[uuid.UUID] = set()

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        if str(TDR_SNAP_BROKEN) in prompt:
            raise RuntimeError("simulated transient extraction failure")
        if str(TDR_SNAP_LAUNCH) in prompt:
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
            _item(TDR_ITEM_BROKEN, "GPT-4o update"),
            _snapshot(
                TDR_SNAP_BROKEN,
                TDR_ITEM_BROKEN,
                "OpenAI GPT-4o details.",
                datetime(2026, 8, 19, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        ),
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=extract_fake,
    )

    assert result.failed_item_ids == [TDR_ITEM_BROKEN]
    # the second item still resolved and its fact was recorded, despite
    # the first item raising
    current = store.get_current_fact(OPENAI_GPT4O, "context_window_tokens")
    assert current is not None
    assert current.value == "128000"


def test_conflicting_snapshot_fails_only_that_item_and_rest_of_batch_still_runs() -> None:
    """Sixth review: InMemorySnapshotResolver.add() raises ValueError on
    a conflicting snapshot id (shared/snapshot_resolver.py) -- that must
    be caught by _process_item()'s existing broad except, the same as any
    other per-item failure, not crash the whole batch. Middle item's
    snapshot id collides with one already in the caller's resolver but
    carries different content; the item before and after it must still
    process normally."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    # Both seed snapshot ids are pre-known too, exactly as a real caller
    # threading known_snapshot_ids across daily runs would have them
    # (see validate.py's own docstring) -- otherwise the seed citations
    # would fail the plain existence check before content grounding is
    # even reached.
    known_snapshot_ids: set[uuid.UUID] = {TDR_SNAP_OPENAI_SEED, TDR_SNAP_ANTHROPIC_SEED}

    # Seed each subject's "previous" value directly, as if recorded by
    # an earlier day's run -- both seed snapshots are pre-registered in
    # the resolver too, so the real Change claims below have citations
    # that actually resolve.
    seed_openai_snapshot = _snapshot(
        TDR_SNAP_OPENAI_SEED,
        TDR_ITEM_OPENAI_SEED,
        "OpenAI's context window is 128,000 tokens.",
        datetime(2026, 6, 1, tzinfo=UTC),
    )
    seed_anthropic_snapshot = _snapshot(
        TDR_SNAP_ANTHROPIC_SEED,
        TDR_ITEM_ANTHROPIC_SEED,
        "Anthropic's context window is 64,000 tokens.",
        datetime(2026, 6, 1, tzinfo=UTC),
    )
    store.update_fact(
        OPENAI_GPT4O,
        _fact("context_window_tokens", "128000", TDR_SNAP_OPENAI_SEED, TDR_FACT_SEED_OPENAI),
        source_url="https://openai.com/a",
        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        change_set_id_factory=lambda: uuid.uuid4(),
        detected_at=TDR_DETECTED_AT,
    )
    store.update_fact(
        ANTHROPIC_CLAUDE,
        _fact("context_window_tokens", "64000", TDR_SNAP_ANTHROPIC_SEED, TDR_FACT_SEED_ANTHROPIC),
        source_url="https://anthropic.com/a",
        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        change_set_id_factory=lambda: uuid.uuid4(),
        detected_at=TDR_DETECTED_AT,
    )

    resolver = InMemorySnapshotResolver(
        {
            TDR_SNAP_OPENAI_SEED: seed_openai_snapshot,
            TDR_SNAP_ANTHROPIC_SEED: seed_anthropic_snapshot,
            # Pre-registered under the SAME id item_conflict_2's own
            # snapshot will use below, but with different content --
            # that mismatch is exactly what add() must reject.
            TDR_SNAP_CONFLICT: _snapshot(
                TDR_SNAP_CONFLICT,
                TDR_ITEM_PRE_EXISTING,
                "Pre-existing, unrelated content already in the resolver.",
                datetime(2026, 8, 1, tzinfo=UTC),
            ),
        }
    )

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        if str(TDR_SNAP_VALID_1) in prompt:
            return FactExtractionResponse(
                facts=[
                    FactCandidate(
                        field="context_window_tokens",
                        value="256000",
                        quoted_span="increased to 256,000 tokens",
                        confidence=0.95,
                    )
                ]
            )
        if str(TDR_SNAP_VALID_3) in prompt:
            return FactExtractionResponse(
                facts=[
                    FactCandidate(
                        field="context_window_tokens",
                        value="96000",
                        quoted_span="increased to 96,000 tokens",
                        confidence=0.95,
                    )
                ]
            )
        return FactExtractionResponse(facts=[])

    batch = [
        BatchItem(
            _item(TDR_ITEM_VALID_1, "GPT-4o context window update"),
            _snapshot(
                TDR_SNAP_VALID_1,
                TDR_ITEM_VALID_1,
                "OpenAI's GPT-4o context window increased to 256,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(TDR_ITEM_CONFLICT_2, "Unrelated update", publisher="Anthropic"),
            _snapshot(
                TDR_SNAP_CONFLICT,  # same id as the resolver's pre-existing entry
                TDR_ITEM_CONFLICT_2,
                "Different content than what the resolver already has for this id.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(
                TDR_ITEM_VALID_3,
                "Claude context window update",
                publisher="Anthropic",
                source_id="anthropic_news",
            ),
            _snapshot(
                TDR_SNAP_VALID_3,
                TDR_ITEM_VALID_3,
                "Anthropic's Claude context window increased to 96,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=resolver,
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=extract_fake,
    )

    # Only the conflicting item failed -- the whole batch did not crash.
    assert result.failed_item_ids == [TDR_ITEM_CONFLICT_2]
    assert set(result.resolved_subjects) == {OPENAI_GPT4O, ANTHROPIC_CLAUDE}
    assert len(result.digest.claims) == 2
    assert all(c.validation_status == "supported" for c in result.digest.claims)
    claim_texts = {c.text for c in result.digest.claims}
    assert any("256000" in text for text in claim_texts)
    assert any("96000" in text for text in claim_texts)
    # The resolver's original, pre-existing content for TDR_SNAP_CONFLICT
    # survived the rejected conflicting add() untouched.
    conflict_content = resolver.get_content(TDR_SNAP_CONFLICT)
    assert conflict_content is not None
    assert (
        conflict_content.content_text == "Pre-existing, unrelated content already in the resolver."
    )


def test_unresolvable_item_is_recorded_not_dropped() -> None:
    store = FactStore()
    known_snapshot_ids: set[uuid.UUID] = set()

    def resolve_fake(system: str, prompt: str) -> ResolveLLMResponse:
        return ResolveLLMResponse(confidence=0.9)  # no proposal

    batch = [
        BatchItem(
            _item(TDR_ITEM_MYSTERY, "Completely unrelated headline"),
            _snapshot(
                TDR_SNAP_MYSTERY,
                TDR_ITEM_MYSTERY,
                "Nothing about a tracked subject here.",
                datetime(2026, 8, 19, tzinfo=UTC),
            ),
        )
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        resolve_llm_call_fn=resolve_fake,
    )

    assert result.unresolved_item_ids == [TDR_ITEM_MYSTERY]
    assert result.resolved_subjects == []
    assert result.digest.status == "draft"  # nothing to report
    assert result.digest.claims == []


def test_comparison_skipped_with_fewer_than_two_resolved_subjects() -> None:
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[uuid.UUID] = set()

    batch = [
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        )
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
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
    known_snapshot_ids: set[uuid.UUID] = {TDR_SNAP_FROM_A_PREVIOUS_RUN}

    batch = [
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        )
    ]

    run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )

    assert TDR_SNAP_FROM_A_PREVIOUS_RUN in known_snapshot_ids
    assert TDR_SNAP_LAUNCH in known_snapshot_ids


def test_snapshot_resolver_is_required() -> None:
    """Fourth review, blocker 1: run_daily() must not build its own
    InMemorySnapshotResolver internally -- a resolver's usefulness comes
    from covering more than just this one batch (e.g. a real, persistent
    ingestion-store-backed resolver spanning many days), so the caller
    must supply one explicitly. Proven here by the call itself failing,
    not by behavior."""
    store = FactStore()
    known_snapshot_ids: set[uuid.UUID] = set()
    batch: list[BatchItem] = []

    with pytest.raises(TypeError):
        run_daily(store, known_snapshot_ids, batch, date(2026, 8, 20), alias_table=[])  # type: ignore[call-arg]


def test_caller_supplied_snapshot_resolver_is_reused_not_replaced() -> None:
    """run_daily() must register each item's snapshot into the CALLER's
    own resolver instance (via .add(), when supported) rather than
    building and discarding its own -- proven by passing a resolver in,
    then confirming that same instance can resolve a batch snapshot
    afterward, with no separate internal resolver involved."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[uuid.UUID] = set()
    resolver = InMemorySnapshotResolver()

    batch = [
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        )
    ]

    run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=resolver,
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )

    assert resolver.get_content(TDR_SNAP_LAUNCH) is not None


def test_resolver_without_add_is_left_to_manage_its_own_contents() -> None:
    """A SnapshotResolver Protocol only guarantees get_content() -- a
    real, persistent-store-backed resolver may have no add() at all
    (e.g. it's populated by ingestion's own write path, not by
    daily_run.py). run_daily() must not assume every resolver supports
    registration; it should run to completion regardless, simply leaving
    such a resolver's own contents unchanged."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[uuid.UUID] = set()

    class _ReadOnlyResolver:
        def get_content(self, snapshot_id: uuid.UUID) -> DocumentSnapshot | None:
            return None

    batch = [
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        )
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=_ReadOnlyResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )

    # No exception from the missing add() -- the run completes normally.
    assert result.resolved_subjects == [OPENAI_GPT4O]


# --- ADR 0007: a new daily_run() call gets a fresh ChangeSet allocator,
# so the same recurring subject receives a NEW change_set_id, never a
# previous run's cached value. ---


def test_recurring_subject_gets_a_new_change_set_id_on_a_later_run() -> None:
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[uuid.UUID] = set()

    # First run: establish a baseline value (128000) as a first
    # observation -- no Change/ChangeSet yet.
    run_daily(
        store,
        known_snapshot_ids,
        [
            BatchItem(
                _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
                _snapshot(
                    TDR_SNAP_LAUNCH,
                    TDR_ITEM_LAUNCH,
                    "OpenAI is launching GPT-4o with a 128,000 token context window.",
                    datetime(2026, 6, 2, tzinfo=UTC),
                ),
            )
        ],
        date(2026, 6, 2),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "128000", "128,000 token context window"
        ),
    )

    # Second run, a later day: the value changes, producing a real
    # Change/ChangeSet for OPENAI_GPT4O.
    first_run_result = run_daily(
        store,
        known_snapshot_ids,
        [
            BatchItem(
                _item(TDR_ITEM_256K, "GPT-4o now supports a 256k token context window"),
                _snapshot(
                    TDR_SNAP_256K,
                    TDR_ITEM_256K,
                    "OpenAI announced GPT-4o's context window has increased to 256,000 tokens.",
                    datetime(2026, 8, 20, tzinfo=UTC),
                ),
            )
        ],
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "256000", "increased to 256,000 tokens"
        ),
    )
    assert len(first_run_result.change_sets) == 1
    first_change_set_id = first_run_result.change_sets[0].id

    # Third run, yet another day: another change for the SAME subject
    # must get a DIFFERENT change_set_id -- proving the allocator is
    # freshly built per run_daily() call, not cached on FactStore.
    second_run_result = run_daily(
        store,
        known_snapshot_ids,
        [
            BatchItem(
                _item(TDR_ITEM_VALID_1, "GPT-4o context window update"),
                _snapshot(
                    TDR_SNAP_VALID_1,
                    TDR_ITEM_VALID_1,
                    "OpenAI's GPT-4o context window increased to 512,000 tokens.",
                    datetime(2026, 9, 1, tzinfo=UTC),
                ),
            )
        ],
        date(2026, 9, 1),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=_extraction_fake(
            "context_window_tokens", "512000", "increased to 512,000 tokens"
        ),
    )
    assert len(second_run_result.change_sets) == 1
    assert second_run_result.change_sets[0].id != first_change_set_id


# --- ADR 0006/0007: a genuine disclosure-status transition flows all
# the way through run_daily() as a real Change/DigestClaim, dual-cited
# against both the withheld and the disclosing snapshot. ---


def test_disclosure_transition_integrated_pipeline_run() -> None:
    """End-to-end proof that a genuine not_disclosed -> disclosed
    transition flows all the way through run_daily(): FactStore seeded
    with an existing withheld price, a new batch item discloses it for
    the first time, and the resulting digest carries a real,
    content-grounded, dual-cited DigestClaim for the transition."""
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.update_fact(
        OPENAI_GPT4O,
        _not_disclosed_fact("input_price_usd", TDR_SNAP_PRICE_WITHHELD, TDR_FACT_PRICE_WITHHELD),
        source_url="https://openai.com/news/pricing-tbd",
        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        change_set_id_factory=lambda: uuid.uuid4(),
        detected_at=TDR_DETECTED_AT,
    )
    known_snapshot_ids: set[uuid.UUID] = {TDR_SNAP_PRICE_WITHHELD}
    resolver = InMemorySnapshotResolver(
        {
            TDR_SNAP_PRICE_WITHHELD: _snapshot(
                TDR_SNAP_PRICE_WITHHELD,
                TDR_ITEM_PRICE_WITHHELD,
                "OpenAI's GPT-4o pricing has not been announced yet.",
                datetime(2026, 6, 1, tzinfo=UTC),
            )
        }
    )

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        return FactExtractionResponse(
            facts=[
                FactCandidate(
                    field="input_price_usd",
                    value="5.00",
                    quoted_span="Input pricing is $5.00 per million tokens.",
                    confidence=0.95,
                )
            ]
        )

    batch = [
        BatchItem(
            _item(TDR_ITEM_PRICE_DISCLOSED, "GPT-4o pricing disclosed"),
            _snapshot(
                TDR_SNAP_PRICE_DISCLOSED,
                TDR_ITEM_PRICE_DISCLOSED,
                "OpenAI's GPT-4o: Input pricing is $5.00 per million tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        )
    ]

    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=resolver,
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=extract_fake,
    )

    assert len(result.change_sets) == 1
    change_set = result.change_sets[0]
    assert len(change_set.changes) == 1
    assert change_set.changes[0].change_type == "disclosed"
    assert change_set.changes[0].current.value == "5.00"
    assert change_set.changes[0].previous is not None
    assert change_set.changes[0].previous.value is None

    assert len(result.digest.claims) == 1
    claim = result.digest.claims[0]
    # NOTE: field_label("input_price_usd") renders COMPARABLE_FIELDS's
    # own curated label ("Input price (USD)" -> "input price (USD)"),
    # not the shorter "input price" -- asserting the real rendered text,
    # verified directly against shared/attributes.py, not assumed.
    assert claim.text == "OpenAI's GPT-4o's input price (USD) is now disclosed as 5.00."
    assert set(claim.citation_snapshot_ids) == {TDR_SNAP_PRICE_DISCLOSED, TDR_SNAP_PRICE_WITHHELD}
    assert claim.validation_status == "supported"


# ---------------------------------------------------------------------------
# ADR 0008 section 5.A: run_daily() takes a required, caller-injected batch
# detection time, validates it once up front, and every Change the run
# produces carries exactly that instant as its detected_at.
# ---------------------------------------------------------------------------


def _seed_context_window(store: FactStore, subject: Subject, value: str, snap: uuid.UUID) -> None:
    store.update_fact(
        subject,
        _fact("context_window_tokens", value, snap),
        source_url="https://example.com/seed",
        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        change_set_id_factory=lambda: uuid.uuid4(),
        detected_at=TDR_DETECTED_AT,
    )


def test_every_change_in_a_batch_shares_the_injected_detected_at() -> None:
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    store.register_subject(ANTHROPIC_CLAUDE)
    _seed_context_window(store, OPENAI_GPT4O, "128000", TDR_SNAP_OPENAI_SEED)
    _seed_context_window(store, ANTHROPIC_CLAUDE, "64000", TDR_SNAP_ANTHROPIC_SEED)
    known_snapshot_ids: set[uuid.UUID] = {TDR_SNAP_OPENAI_SEED, TDR_SNAP_ANTHROPIC_SEED}

    responses = {
        TDR_SNAP_OPENAI_CTX: ("context_window_tokens", "256000", "to 256,000 tokens"),
        TDR_SNAP_ANTHROPIC_CTX: ("context_window_tokens", "200000", "now 200,000 tokens"),
    }

    def extract_fake(system: str, prompt: str) -> FactExtractionResponse:
        for snap_id, (field, value, span) in responses.items():
            if str(snap_id) in prompt:
                return FactExtractionResponse(
                    facts=[
                        FactCandidate(field=field, value=value, quoted_span=span, confidence=0.95)
                    ]
                )
        return FactExtractionResponse(facts=[])

    batch = [
        BatchItem(
            _item(TDR_ITEM_OPENAI_CTX, "GPT-4o context window"),
            _snapshot(
                TDR_SNAP_OPENAI_CTX,
                TDR_ITEM_OPENAI_CTX,
                "OpenAI increased GPT-4o's context window to 256,000 tokens.",
                datetime(2026, 8, 20, tzinfo=UTC),
            ),
        ),
        BatchItem(
            _item(
                TDR_ITEM_ANTHROPIC_CTX,
                "Claude context window",
                publisher="Anthropic",
                source_id="anthropic_news",
            ),
            _snapshot(
                TDR_SNAP_ANTHROPIC_CTX,
                TDR_ITEM_ANTHROPIC_CTX,
                "Anthropic said Claude's context window is now 200,000 tokens.",
                datetime(2026, 8, 19, tzinfo=UTC),
            ),
        ),
    ]

    def no_comparisons(system: str, prompt: str) -> ComparisonResponse:
        return ComparisonResponse(assertions=[])

    injected = datetime(2026, 8, 20, 6, 30, 0, 987654, tzinfo=UTC)
    result = run_daily(
        store,
        known_snapshot_ids,
        batch,
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=injected,
        extract_call_fn=extract_fake,
        compare_call_fn=no_comparisons,
    )

    assert result.unresolved_item_ids == []
    assert result.failed_item_ids == []
    all_changes = [c for cs in result.change_sets for c in cs.changes]
    assert len(all_changes) == 2
    assert {c.subject for c in all_changes} == {OPENAI_GPT4O, ANTHROPIC_CLAUDE}
    for change in all_changes:
        assert change.detected_at == injected
        assert change.detected_at.microsecond == 987654


def test_run_daily_rejects_a_naive_batch_detected_at_before_processing_anything() -> None:
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[uuid.UUID] = set()
    extract_calls: list[str] = []

    def extract_spy(system: str, prompt: str) -> FactExtractionResponse:
        extract_calls.append(prompt)
        return FactExtractionResponse(facts=[])

    batch = [
        BatchItem(
            _item(TDR_ITEM_LAUNCH, "Introducing GPT-4o"),
            _snapshot(
                TDR_SNAP_LAUNCH,
                TDR_ITEM_LAUNCH,
                "OpenAI is launching GPT-4o with a 128,000 token context window.",
                datetime(2026, 6, 2, tzinfo=UTC),
            ),
        )
    ]

    with pytest.raises(ValidationError):
        run_daily(
            store,
            known_snapshot_ids,
            batch,
            date(2026, 8, 20),
            alias_table=[],
            snapshot_resolver=InMemorySnapshotResolver(),
            batch_detected_at=datetime(2026, 8, 20, 12, 0, 0),
            extract_call_fn=extract_spy,
        )

    assert extract_calls == []  # failed before any item was processed
    assert store.get_current_fact(OPENAI_GPT4O, "context_window_tokens") is None


def test_digest_date_is_a_real_date_end_to_end() -> None:
    store = FactStore()
    store.register_subject(OPENAI_GPT4O)
    known_snapshot_ids: set[uuid.UUID] = set()

    result = run_daily(
        store,
        known_snapshot_ids,
        [],
        date(2026, 8, 20),
        alias_table=[],
        snapshot_resolver=InMemorySnapshotResolver(),
        batch_detected_at=TDR_DETECTED_AT,
        extract_call_fn=_extraction_fake("context_window_tokens", "1", "1"),
    )
    assert result.digest.digest_date == date(2026, 8, 20)
    assert isinstance(result.digest.digest_date, date)
    assert result.digest.model_dump(mode="json")["digest_date"] == "2026-08-20"
