"""Shared database engine and session factory construction — ADR 0002 §12.3."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

__all__ = ["create_engine", "create_session_factory"]


def create_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_recycle: int = 1800,
    pool_pre_ping: bool = True,
    **kwargs: Any,
) -> AsyncEngine:
    """Create a configured async database engine.

    Per ADR 0002 §12.3, a process has one configured engine, one connection pool,
    and one session factory boundary. Importing or constructing does not perform
    I/O until connections are checked out.
    """
    engine_kwargs: dict[str, Any] = {
        "pool_pre_ping": pool_pre_ping,
        "pool_recycle": pool_recycle,
        **kwargs,
    }
    # SQLite in-memory or file testing engines do not accept pool_size/max_overflow with StaticPool
    if not database_url.startswith("sqlite"):
        engine_kwargs["pool_size"] = pool_size
        engine_kwargs["max_overflow"] = max_overflow

    return create_async_engine(database_url, **engine_kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async sessionmaker bound to the shared engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
