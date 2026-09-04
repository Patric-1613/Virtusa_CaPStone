"""Contract tests: protect the shared models across all three modules.

This is the test suite that must keep passing if anyone touches
src/ai_daily_digest/shared/schemas.py or the tests/fixtures/contracts/
pack — it's what verifies intelligence's loader can actually consume what
docs/API_CONTRACT.md promises.
"""

import pytest

from ai_daily_digest.intelligence.loaders import FixtureLoader

pytestmark = pytest.mark.contract


def test_source_items_are_schema_valid() -> None:
    items = FixtureLoader().load_items()
    assert len(items) >= 1
    ids = [item.id for item in items]
    assert len(ids) == len(set(ids)), "duplicate item ids in fixtures"


def test_snapshots_are_schema_valid_and_reference_real_items() -> None:
    items = {item.id for item in FixtureLoader().load_items()}
    snapshots = FixtureLoader().load_snapshots()
    assert len(snapshots) >= 1
    for snapshot in snapshots:
        assert snapshot.source_item_id in items


def test_every_item_latest_snapshot_id_resolves() -> None:
    items = FixtureLoader().load_items()
    snapshot_ids = {s.id for s in FixtureLoader().load_snapshots()}
    for item in items:
        if item.latest_snapshot_id is not None:
            assert item.latest_snapshot_id in snapshot_ids


def test_extracted_facts_reference_real_snapshots() -> None:
    snapshot_ids = {s.id for s in FixtureLoader().load_snapshots()}
    facts = FixtureLoader().load_facts()
    assert len(facts) >= 1
    for fact in facts:
        assert fact.snapshot_id in snapshot_ids
        if fact.extraction_method == "llm_structured_output":
            assert fact.extraction_model, "LLM-extracted facts must record a model id"
            assert fact.prompt_version, "LLM-extracted facts must record a prompt version"
            # ADR 0004: LLM-extracted facts must keep their evidence, not
            # just a bare value, so grounding can be audited later.
            assert fact.quoted_span is not None and fact.quoted_span.strip(), (
                "LLM-extracted facts must record a non-empty quoted_span"
            )
            assert fact.confidence is not None, "LLM-extracted facts must record their confidence"


def test_change_set_citations_resolve_to_real_snapshots() -> None:
    snapshot_ids = {s.id for s in FixtureLoader().load_snapshots()}
    change_sets = FixtureLoader().load_change_sets()
    assert len(change_sets) >= 1
    for change_set in change_sets:
        for sid in change_set.previous_snapshot_ids + change_set.current_snapshot_ids:
            assert sid in snapshot_ids
        for change in change_set.changes:
            if change.previous is not None:
                assert change.previous.snapshot_id in snapshot_ids
            assert change.current.snapshot_id in snapshot_ids


def test_change_previous_null_only_when_not_disclosed_is_the_intent() -> None:
    """docs/API_CONTRACT.md: `previous` (the FactObservation object
    itself, not its nested `.value`) is null only for a first disclosure
    -- the fixture pack's real change carries a genuine previous
    observation, not this ADR-0006 edge case. This fixture pack doesn't
    exercise the first-disclosure case yet (see its README) — this test
    at least asserts every change we DO have carries a previous."""
    change_sets = FixtureLoader().load_change_sets()
    for change_set in change_sets:
        for change in change_set.changes:
            assert change.previous is not None


def test_digest_claims_have_resolvable_citations() -> None:
    snapshot_ids = {s.id for s in FixtureLoader().load_snapshots()}
    digests = FixtureLoader().load_digests()
    assert len(digests) >= 1
    for digest in digests:
        for claim in digest.claims:
            assert len(claim.citation_snapshot_ids) >= 1, "every claim needs >=1 citation"
            for sid in claim.citation_snapshot_ids:
                assert sid in snapshot_ids


def test_published_digest_has_no_unsupported_claims() -> None:
    digests = FixtureLoader().load_digests()
    for digest in digests:
        if digest.status == "published":
            for claim in digest.claims:
                assert claim.validation_status != "unsupported"
