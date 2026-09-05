"""Construction of the process's one async SQLAlchemy engine, connection
pool, and session factory (docs/adr/0002-postgres-pgvector.md section
12.3, 12.6). Called exactly once, by the process composition root -- the
API's ASGI entrypoint or the worker's `async def main()` -- never
per-request or per-module. Importing this module performs no I/O and
opens no connection; only calling the two builders below does."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_daily_digest.shared.config import DatabaseConfig


def build_engine(config: DatabaseConfig) -> AsyncEngine:
    """Create the process's one `AsyncEngine` from a validated
    `DatabaseConfig`. Pool sizing and timeouts always come from the
    config's Phase-1 bounded defaults or its operator overrides (ADR
    0002 section 14) -- never hardcoded here and never re-read from
    `os.environ` directly, so every value this engine uses passed
    through `DatabaseConfig.from_env()`'s validation once.

    `statement_timeout` is set via the `options` libpq connection
    parameter (`-c statement_timeout=<ms>`) -- the standard way to set a
    server-side per-statement bound at connection time with psycopg,
    independent of any one query.
    """
    return create_async_engine(
        config.database_url,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_pre_ping=config.pool_pre_ping,
        pool_recycle=config.pool_recycle_seconds,
        connect_args={
            "connect_timeout": config.connect_timeout_seconds,
            "options": f"-c statement_timeout={config.statement_timeout_ms}",
        },
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the process's one session factory, bound to `engine`.

    `expire_on_commit=False`: the ingestion service (ADR 0002 section 13)
    reads attributes off the found-or-created `SourceItem` row after its
    single commit, inside the same unit of work -- the SQLAlchemy default
    (`expire_on_commit=True`) would force an implicit re-`SELECT` (or
    raise, once the session is closed) for that read instead of returning
    the value already in memory.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
