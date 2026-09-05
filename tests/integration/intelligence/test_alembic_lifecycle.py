"""Integration test for Alembic migration upgrade/downgrade lifecycle — ADR 0002 §12.5."""
# pylint: disable=wrong-import-order

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import alembic.command
import pytest
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _is_safe_database_name(name: str) -> bool:
    """Safety guard: verify database name matches test/ci patterns."""
    lower = name.lower()
    return (
        lower.endswith("_test")
        or lower.endswith("_ci")
        or lower.startswith("test_")
        or lower.startswith("ci_")
        or lower in ("postgres", "template1")
    )


def _setup_scratch_database(base_url_str: str) -> tuple[str, str, str]:
    """Create a run-unique scratch PostgreSQL database per ADR 0002 §15.

    Returns (scratch_db_name, scratch_url, admin_url_sync).
    """
    url = make_url(base_url_str)
    original_db = url.database or ""

    # Safety guard on the base connection
    if not _is_safe_database_name(original_db):
        raise RuntimeError(
            f"Refusing to run destructive Alembic lifecycle test on database {original_db!r}: "
            "name must match *_test, *_ci, or test_*"
        )

    # Use standard postgres maintenance database for administrative DDL
    admin_url_obj = url.set(database="postgres")
    admin_url_sync = admin_url_obj.render_as_string(hide_password=False)

    scratch_db_name = f"test_alembic_{uuid.uuid4().hex[:12]}"
    scratch_url_obj = url.set(database=scratch_db_name)
    scratch_url = scratch_url_obj.render_as_string(hide_password=False)

    admin_engine = create_sync_engine(admin_url_sync, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db_name}";'))
    finally:
        admin_engine.dispose()

    return scratch_db_name, scratch_url, admin_url_sync


def _teardown_scratch_database(admin_url_sync: str, scratch_db_name: str) -> None:
    """Terminate active backends and drop the scratch database per ADR 0002 §15."""
    admin_engine = create_sync_engine(admin_url_sync, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :dbname AND pid <> pg_backend_pid();"
                ),
                {"dbname": scratch_db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db_name}";'))
    finally:
        admin_engine.dispose()


def test_alembic_lifecycle_safety_guard_rejects_unsafe_db() -> None:
    """Safety guard must reject databases that do not match test/ci patterns."""
    assert not _is_safe_database_name("ai_daily_digest_prod")
    assert not _is_safe_database_name("production")
    assert not _is_safe_database_name("ai_daily_digest_dev")
    assert _is_safe_database_name("ai_daily_digest_test")
    assert _is_safe_database_name("ai_daily_digest_ci")
    assert _is_safe_database_name("test_alembic_12345")
    assert _is_safe_database_name("postgres")

    with pytest.raises(RuntimeError, match="Refusing to run destructive Alembic lifecycle test"):
        _setup_scratch_database(
            "postgresql+psycopg://user:pass@localhost:5432/ai_daily_digest_prod"
        )


def run_in_new_loop(coro_fn: Callable[..., Coroutine[Any, Any, None]]) -> None:
    """Run one coroutine to completion in its own, fully isolated event loop.

    `alembic.command.upgrade()`/`.downgrade()` are themselves synchronous calls that internally
    manage their own asyncio event loop (see alembic/env.py::run_migrations_online). Wrapping
    the whole test body -- alembic calls included -- in one outer `asyncio.run(...)` (the
    `run_async` pattern used by the other async tests in this package) nests a second
    `asyncio.run()` inside the first the moment the test calls into Alembic, which asyncio
    forbids. Each async verification step here therefore gets its own short-lived loop via this
    helper, invoked only between synchronous Alembic calls -- never around them.
    """
    asyncio.run(coro_fn())


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


def test_alembic_upgrade_downgrade_lifecycle() -> None:
    """Verify upgrade -> downgrade base -> upgrade head lifecycle against PostgreSQL.

    Per ADR 0002 §15, this runs against an isolated run-unique scratch database created
    at the start and dropped in teardown, protecting developers' local and deployed databases.
    """
    original_database_url = os.environ.get("DATABASE_URL")
    scratch_db_name, scratch_url, admin_url_sync = _setup_scratch_database(str(DATABASE_URL))
    os.environ["DATABASE_URL"] = scratch_url

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", scratch_url)

    engine: AsyncEngine = create_async_engine(scratch_url, echo=False)
    try:
        # Step 1: Initial upgrade to head
        alembic.command.upgrade(config, "head")

        # Step 2: Ensure we can downgrade cleanly to base
        alembic.command.downgrade(config, "base")

        async def _check_downgraded() -> None:
            async with engine.connect() as conn:
                tables_after_downgrade = await conn.run_sync(
                    lambda sync_conn: set(inspect(sync_conn).get_table_names())
                )
                # All our tables should be gone (only alembic_version might remain)
                assert EXPECTED_TABLES.isdisjoint(tables_after_downgrade)

        run_in_new_loop(_check_downgraded)

        # Step 3: Upgrade back to head
        alembic.command.upgrade(config, "head")

        async def _check_upgraded() -> None:
            async with engine.connect() as conn:
                tables_after_upgrade = await conn.run_sync(
                    lambda sync_conn: set(inspect(sync_conn).get_table_names())
                )
                assert EXPECTED_TABLES.issubset(tables_after_upgrade)

                # Verify triggers exist (pg_trigger captures TRUNCATE statement triggers
                # which information_schema.triggers omits per SQL standard)
                triggers_res = await conn.execute(
                    text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal;")
                )
                found_triggers = {row[0] for row in triggers_res.fetchall()}
                assert EXPECTED_TRIGGERS.issubset(found_triggers)

                # Verify composite FK on source_items
                fks = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_foreign_keys("source_items")
                )
                composite_fk = next(
                    (
                        fk
                        for fk in fks
                        if fk.get("name")
                        in (
                            "fk_source_items_latest_snapshot_id_document_snapshots",
                            "fk_source_items_latest_snapshot_composite",
                        )
                    ),
                    None,
                )
                assert composite_fk is not None
                assert composite_fk["constrained_columns"] == ["latest_snapshot_id", "id"]
                assert composite_fk["referred_table"] == "document_snapshots"
                assert composite_fk["referred_columns"] == ["id", "source_item_id"]

        run_in_new_loop(_check_upgraded)
    finally:
        run_in_new_loop(engine.dispose)
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url
        else:
            os.environ.pop("DATABASE_URL", None)
        _teardown_scratch_database(admin_url_sync, scratch_db_name)
