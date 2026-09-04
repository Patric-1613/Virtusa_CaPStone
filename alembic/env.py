"""Alembic async environment configuration — ADR 0002 §12.5."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import all module models to register them against shared metadata
import ai_daily_digest.ingestion.db.models  # pylint: disable=unused-import
import ai_daily_digest.intelligence.db.models  # noqa: F401  # pylint: disable=unused-import
from ai_daily_digest.shared.db.metadata import Base
from alembic import context

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: this file runs in-process whenever a caller invokes
    # alembic.command.upgrade()/downgrade() directly (e.g. tests/integration/intelligence/
    # test_alembic_lifecycle.py) rather than only via the standalone `alembic` CLI. fileConfig's
    # default (disable_existing_loggers=True) silently disables every already-configured logger
    # the moment this module is imported -- including pytest's own caplog handler -- which was
    # breaking caplog-based assertions in every test that ran after the lifecycle test in the
    # same session, in files with no apparent connection to Alembic at all.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_url() -> str:
    """Resolve database URL from environment or alembic.ini with psycopg driver."""
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url", "")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using an existing synchronous connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Online migration runner."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
