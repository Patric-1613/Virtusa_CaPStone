"""Concrete readiness probe for the configured PostgreSQL database
(docs/adr/0002-postgres-pgvector.md section 14). Implements the
`ReadinessProbe` Protocol from `dependencies.py`. `delivery/api/app.py`
wires an instance into readiness only when a database-backed feature is
actually configured -- a foundation-only app with no database keeps
`required_dependencies` empty, per ADR 0010's "no Postgres adapter
exists merely to raise `NotImplementedError`"."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

LOGGER = logging.getLogger(__name__)

_PROBE_STATEMENT = text("SELECT 1")


class DatabaseReadinessProbe:
    """Runs a bounded `SELECT 1` on a pooled connection with a short
    per-statement timeout, and reduces every outcome to a bool.

    Catches its own driver errors specifically (`SQLAlchemyError`, which
    wraps `psycopg.Error` as `DBAPIError`) and its own timeout -- this
    probe never lets a DSN, host, driver message, or connection string
    escape (ADR 0002 section 14). `ReadinessRegistry.evaluate()`
    (`dependencies.py`) is the backstop if an unexpected exception
    somehow still propagates, but this probe is the first line, and is
    expected to always return a plain bool rather than rely on that
    backstop.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        timeout_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds

    async def is_ready(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._session_factory() as session:
                    await session.execute(_PROBE_STATEMENT)
            return True
        except SQLAlchemyError:
            LOGGER.error("Database readiness probe failed: driver error")
            return False
        except TimeoutError:
            LOGGER.error(
                "Database readiness probe failed: timed out",
                extra={"timeout_seconds": self._timeout_seconds},
            )
            return False
