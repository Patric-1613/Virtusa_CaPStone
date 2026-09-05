"""Migration lifecycle: upgrade -> downgrade -> upgrade
(docs/adr/0002-postgres-pgvector.md section 15, section 16). After
`downgrade`, the two tables, their constraints, the row-level and
statement-level triggers, the trigger functions, and the keyset index
are gone; after the second `upgrade` they are back identically, and
Alembic keeps managing its own `alembic_version` table throughout
(section 7: "the revision never drops `alembic_version`").

Runs on its own dedicated temporary database, not the session-shared
`temporary_database_url` fixture -- this test deliberately tears the
schema down and rebuilds it, and every other integration test in this
package needs the schema to stay present for the whole session.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import run_alembic_async

pytestmark = pytest.mark.integration

_APPLICATION_TABLES = ("source_items", "document_snapshots")
_APPLICATION_TRIGGERS = (
    "source_items_protect_identity_trigger",
    "document_snapshots_block_update_trigger",
    "document_snapshots_block_delete_trigger",
    "document_snapshots_block_truncate_trigger",
)
_APPLICATION_FUNCTIONS = (
    "source_items_protect_identity",
    "document_snapshots_block_update",
    "document_snapshots_block_delete",
    "document_snapshots_block_truncate",
)
_KEYSET_INDEX = "ix_source_items_first_fetched_at_id"


async def _table_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                )
            )
            return {row[0] for row in result}
    finally:
        await engine.dispose()


async def _trigger_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
            return {row[0] for row in result}
    finally:
        await engine.dispose()


async def _function_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT proname FROM pg_proc "
                    "JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace "
                    "WHERE pg_namespace.nspname = 'public'"
                )
            )
            return {row[0] for row in result}
    finally:
        await engine.dispose()


async def _index_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
            return {row[0] for row in result}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_downgrade_upgrade_recreates_application_objects_identically(
    own_temporary_database: str,
) -> None:
    database_url = own_temporary_database

    await run_alembic_async(database_url=database_url, target="head")
    tables_after_first_upgrade = await _table_names(database_url)
    triggers_after_first_upgrade = await _trigger_names(database_url)
    functions_after_first_upgrade = await _function_names(database_url)
    indexes_after_first_upgrade = await _index_names(database_url)

    for table in _APPLICATION_TABLES:
        assert table in tables_after_first_upgrade
    for trigger in _APPLICATION_TRIGGERS:
        assert trigger in triggers_after_first_upgrade
    for function in _APPLICATION_FUNCTIONS:
        assert function in functions_after_first_upgrade
    assert _KEYSET_INDEX in indexes_after_first_upgrade
    # Alembic creates and manages alembic_version itself -- this
    # revision neither creates nor drops it (ADR 0002 section 7).
    assert "alembic_version" in tables_after_first_upgrade

    await run_alembic_async(database_url=database_url, target="base", downgrade=True)
    tables_after_downgrade = await _table_names(database_url)
    triggers_after_downgrade = await _trigger_names(database_url)
    functions_after_downgrade = await _function_names(database_url)

    for table in _APPLICATION_TABLES:
        assert table not in tables_after_downgrade
    assert not triggers_after_downgrade
    assert not functions_after_downgrade
    assert "alembic_version" in tables_after_downgrade

    await run_alembic_async(database_url=database_url, target="head")
    tables_after_second_upgrade = await _table_names(database_url)
    triggers_after_second_upgrade = await _trigger_names(database_url)
    functions_after_second_upgrade = await _function_names(database_url)
    indexes_after_second_upgrade = await _index_names(database_url)

    assert tables_after_second_upgrade == tables_after_first_upgrade
    assert triggers_after_second_upgrade == triggers_after_first_upgrade
    assert functions_after_second_upgrade == functions_after_first_upgrade
    assert indexes_after_second_upgrade == indexes_after_first_upgrade
