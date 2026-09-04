"""Integration test for Alembic migration upgrade/downgrade lifecycle — ADR 0002 §12.5."""
# pylint: disable=wrong-import-order

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from typing import Any

import alembic.command
import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_daily_digest.shared.db.engine import create_engine


def run_async(coro_fn: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """Run async test in event loop."""

    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(coro_fn(*args, **kwargs))

    return wrapper


pytestmark = pytest.mark.integration

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("set DATABASE_URL to run PostgreSQL integration tests", allow_module_level=True)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://") :]
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://") :]


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
    "trg_protect_source_items_immutability",
    "trg_protect_document_snapshots_immutable_update",
    "trg_protect_document_snapshots_immutable_delete",
    "trg_protect_document_snapshots_immutable_truncate",
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


@run_async
async def test_alembic_upgrade_downgrade_lifecycle() -> None:
    """Verify upgrade -> downgrade base -> upgrade head lifecycle against PostgreSQL."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(DATABASE_URL))

    engine: AsyncEngine = create_engine(str(DATABASE_URL), echo=False)
    try:
        # Step 1: Ensure we can downgrade cleanly to base
        alembic.command.downgrade(config, "base")

        async with engine.connect() as conn:
            tables_after_downgrade = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            # All our tables should be gone (only alembic_version might remain)
            assert EXPECTED_TABLES.isdisjoint(tables_after_downgrade)

        # Step 2: Upgrade back to head
        alembic.command.upgrade(config, "head")

        async with engine.connect() as conn:
            tables_after_upgrade = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            assert EXPECTED_TABLES.issubset(tables_after_upgrade)

            # Verify triggers exist
            triggers_res = await conn.execute(
                text("SELECT trigger_name FROM information_schema.triggers;")
            )
            found_triggers = {row[0] for row in triggers_res.fetchall()}
            assert EXPECTED_TRIGGERS.issubset(found_triggers)

            # Verify composite FK on source_items
            fks = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_foreign_keys("source_items")
            )
            composite_fk = next(
                (fk for fk in fks if fk.get("name") == "fk_source_items_latest_snapshot_composite"),
                None,
            )
            assert composite_fk is not None
            assert composite_fk["constrained_columns"] == ["latest_snapshot_id", "id"]
            assert composite_fk["referred_table"] == "document_snapshots"
            assert composite_fk["referred_columns"] == ["id", "source_item_id"]
    finally:
        await engine.dispose()
