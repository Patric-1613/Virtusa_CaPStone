import uuid
from datetime import UTC, datetime

from ai_daily_digest.intelligence.loaders import FixtureLoader
from ai_daily_digest.intelligence.resolve import load_alias_table, resolve_deterministic
from ai_daily_digest.shared.schemas import SourceItem, Subject

# The fixture pack's known subjects (see tests/fixtures/contracts/README.md)
KNOWN_SUBJECTS = [
    Subject(company="OpenAI", product="GPT-4o"),
    Subject(company="Anthropic", product="Claude"),
]

TR_ITEM_UNRELATED = uuid.UUID("01a01e2f-23e8-78c0-bb25-d9ea47beb168")
TR_ITEM_AMB = uuid.UUID("01a01e2f-27d0-7780-af0c-cf29b575940f")
TR_ITEM_O1 = uuid.UUID("01a01e2f-2bb8-7191-8561-23188072a595")
TR_ITEM_O100 = uuid.UUID("01a01e2f-2fa0-7290-a1ff-1f0214d73621")
TR_ITEM_X = uuid.UUID("01a01e2f-3388-77b0-a5b7-cc071d465b25")


def _item(item_id: uuid.UUID, title: str) -> SourceItem:
    return SourceItem(
        id=item_id,
        dedupe_key=f"sha256:{item_id}",
        source_id="test-source",
        publisher="Test Publisher",
        title=title,
        canonical_url="https://example.com/a",  # type: ignore[arg-type]
        first_fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def test_all_fixture_items_resolve_with_zero_false_merges() -> None:
    """A false merge is worse than a miss, so this is checked explicitly
    per item against the fixture pack's real content, not just counted."""
    loader = FixtureLoader()
    items = loader.load_items()
    alias_table = load_alias_table()

    # The fixture pack's real, frozen UUID v7 ids (see
    # tests/fixtures/contracts/README.md) -- items 1-3 are OpenAI/GPT-4o,
    # item 4 is Anthropic/Claude.
    expected_by_publisher_hint = {
        uuid.UUID("01a01e6a-a260-7f03-9481-0c52e1f35714"): Subject(
            company="OpenAI", product="GPT-4o"
        ),
        uuid.UUID("01a01ef8-8a80-7271-a956-b9f73c508585"): Subject(
            company="OpenAI", product="GPT-4o"
        ),
        uuid.UUID("019e8798-d240-77d2-b10f-c6ab78299ae1"): Subject(
            company="OpenAI", product="GPT-4o"
        ),
        uuid.UUID("01a01a5b-82c0-72c1-aff1-dce965ea56ff"): Subject(
            company="Anthropic", product="Claude"
        ),
    }

    for item in items:
        text = loader.snapshot_text(item.latest_snapshot_id) if item.latest_snapshot_id else ""
        result = resolve_deterministic(item, KNOWN_SUBJECTS, alias_table, item_text=text)
        expected = expected_by_publisher_hint.get(item.id)
        if expected is not None:
            assert result.subject == expected, (
                f"{item.id} resolved to {result.subject!r}, expected {expected!r}"
            )


def test_unrelated_item_does_not_match() -> None:
    alias_table = load_alias_table()
    item = _item(TR_ITEM_UNRELATED, "Local bakery wins regional award")
    result = resolve_deterministic(
        item,
        KNOWN_SUBJECTS,
        alias_table,
        item_text="A bakery in town has won an award for its sourdough.",
    )
    assert result.subject is None
    assert result.method == "no_match"


def test_ambiguous_when_two_subjects_both_match() -> None:
    shared = Subject(company="Shared Co", product="Shared Product")
    other = Subject(company="Other Co", product="Shared Product")
    item = _item(TR_ITEM_AMB, "Shared Product gets an update")
    # Force both to be findable by giving them the same aliasable product name.
    result = resolve_deterministic(
        item,
        [shared, other],
        alias_table=[],
        item_text="Details about Shared Product follow.",
    )
    assert result.subject is None
    assert result.method == "ambiguous"
    assert set(result.candidate_subjects) == {shared, other}


def test_short_two_character_product_names_still_match() -> None:
    """Real OpenAI models are literally named "o1"/"o3" -- a 2-character
    candidate must still be matchable as a whole, space-bounded token."""
    subject = Subject(company="OpenAI", product="o1")
    item = _item(TR_ITEM_O1, "o1 launches today")
    result = resolve_deterministic(
        item, [subject], alias_table=[], item_text="OpenAI's o1 model is now generally available."
    )
    assert result.subject == subject


def test_short_product_name_does_not_match_as_a_substring_of_a_longer_word() -> None:
    """ "o1" must not match inside "o100" or similar -- word-boundary
    matching, not a loose substring check."""
    subject = Subject(company="OpenAI", product="o1")
    item = _item(TR_ITEM_O100, "o100 launches today")
    result = resolve_deterministic(
        item,
        [subject],
        alias_table=[],
        item_text="A completely different product called o100 was announced.",
    )
    assert result.subject is None


def test_no_match_returns_all_known_subjects_as_candidates_for_llm_fallback() -> None:
    item = _item(TR_ITEM_X, "Totally unrelated headline")
    result = resolve_deterministic(
        item, KNOWN_SUBJECTS, alias_table=[], item_text="Nothing about tracked subjects here."
    )
    assert result.method == "no_match"
    assert set(result.candidate_subjects) == set(KNOWN_SUBJECTS)
