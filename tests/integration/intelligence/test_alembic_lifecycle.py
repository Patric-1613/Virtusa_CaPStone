"""Integration test for Alembic migration upgrade/downgrade lifecycle — ADR 0002 & ADR 0011."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import run_alembic_async

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "subjects",
    "source_items",
    "document_snapshots",
    "extracted_facts",
    "current_facts",
    "change_sets",
    "changes",
    "digests",
    "digest_claims",
    "digest_claim_citations",
}

EXPECTED_TRIGGERS = {
    "source_items_protect_identity_trigger",
    "document_snapshots_block_update_trigger",
    "document_snapshots_block_delete_trigger",
    "document_snapshots_block_truncate_trigger",
    "trg_validate_extracted_fact_observed_at",
    "trg_protect_extracted_facts_immutable_update",
    "trg_protect_extracted_facts_immutable_delete",
    "trg_protect_extracted_facts_immutable_truncate",
    "trg_validate_change_provenance",
    "trg_protect_changes_immutability",
    "trg_protect_changes_delete",
    "trg_protect_changes_truncate",
    "trg_protect_digests_immutability",
    "trg_enforce_digest_publication",
    "trg_protect_published_digest_claims_insert",
    "trg_protect_published_digest_claims_update",
    "trg_protect_published_digest_claims_delete",
    "trg_protect_published_digest_claims_truncate",
    "trg_protect_published_digest_claim_citations_insert",
    "trg_protect_published_digest_claim_citations_update",
    "trg_protect_published_digest_claim_citations_delete",
    "trg_protect_published_digest_claim_citations_truncate",
}


@pytest.mark.asyncio
async def test_alembic_upgrade_downgrade_lifecycle(own_temporary_database: str) -> None:
    """Verify upgrade -> downgrade base -> upgrade head lifecycle against PostgreSQL."""
    database_url = own_temporary_database

    # Step 1: Initial upgrade to head
    await run_alembic_async(database_url=database_url, target="head")

    engine = create_async_engine(database_url, echo=False)
    try:
        async with engine.connect() as conn:
            tables_after_upgrade = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            assert EXPECTED_TABLES.issubset(tables_after_upgrade)

            triggers_res = await conn.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal;")
            )
            found_triggers = {row[0] for row in triggers_res.fetchall()}
            assert EXPECTED_TRIGGERS.issubset(found_triggers)

        # Step 2: Ensure we can downgrade cleanly to base
        await run_alembic_async(database_url=database_url, target="base", downgrade=True)

        async with engine.connect() as conn:
            tables_after_downgrade = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            assert EXPECTED_TABLES.isdisjoint(tables_after_downgrade)

        # Step 3: Upgrade back to head
        await run_alembic_async(database_url=database_url, target="head")

        async with engine.connect() as conn:
            tables_after_second_upgrade = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            assert EXPECTED_TABLES.issubset(tables_after_second_upgrade)
    finally:
        await engine.dispose()
