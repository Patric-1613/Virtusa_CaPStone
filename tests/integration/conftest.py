"""Integration-test lifecycle: a real, temporary PostgreSQL database per
test session (docs/adr/0002-postgres-pgvector.md section 15). Arrays
(`text[]`), the native `uuid` type, `timestamptz` microsecond precision,
`ON CONFLICT`, row-level triggers, and FK `RESTRICT` are not faithfully
reproduced by SQLite -- there is no SQLite fallback path anywhere in
this file.

**When `DATABASE_URL` is unset**, every test under this package skips
with a clear reason (`pytest.skip(...)`), UNLESS `-m integration` was
explicitly selected, in which case collection fails loudly instead
(section 15: "a collection-time guard fails (not skips) if -m
integration was explicitly selected with no DATABASE_URL"). No test in
this package ever silently passes without touching the database.

**Isolation strategy** (section 15): the run creates one temporary
database, runs `alembic upgrade head` against it once, then:

- ordinary, non-committing tests use the `database_session` fixture --
  one per-test transaction, rolled back at teardown;
- concurrency tests (duplicate ingestion, latest-pointer-and-metadata
  ordering) use `open_database_session` to open their **own** connections
  and commit for real -- a single outer rollback cannot unwind another
  connection's committed work, so those tests never rely on one, and
  every such test uses freshly generated UUID v7 records and asserts
  only on the rows it created (section 15: "no test assumes the tables
  are globally empty").

Teardown disposes all connections, runs `alembic downgrade base` where
practical to exercise the down-path, then drops the temporary database.
CI fails the job if the database, the upgrade, or the teardown/drop
cannot complete -- a half-provisioned database is never reported as
"tests skipped".
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from ai_daily_digest.shared.config import DatabaseConfig
from ai_daily_digest.shared.db import build_engine, build_session_factory
from alembic import command

LOGGER = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def _base_database_url() -> str | None:
    raw = os.environ.get("DATABASE_URL", "").strip()
    return raw or None


_INTEGRATION_TOKEN = re.compile(r"(?<![\w-])integration(?![\w-])")


def _markexpr_selects_integration(markexpr: str) -> bool:
    """Whether the caller *explicitly named* `integration` in `-m
    <markexpr>`, in a way that actually selects it -- section 15's
    trigger for failing loudly instead of skipping.

    Two conditions, both required:

    1. **`integration` appears as a literal term in the expression at
       all.** `make ci`'s own `-m "not live"` selects integration tests
       as a side effect of not excluding them (ADR 0002 section 15 says
       so explicitly: "`-m integration`, currently already inside `-m
       "not live"`") -- that is ordinary, expected behaviour, not the
       caller "explicitly selecting" integration tests, and a `-m "not
       live"` run with no database configured should skip each
       integration test individually, not abort the whole run. A plain
       substring check on `markexpr` is not enough either: `make
       check`'s own `-m "not integration and not e2e and not live"`
       contains the substring `"integration"` while deliberately
       *excluding* every integration test -- `_INTEGRATION_TOKEN`'s word
       -boundary match still finds the term there (correctly), which is
       why condition 2 below is also required.
    2. **The expression, evaluated with only `integration` true, is
       true.** Distinguishes "integration" / "integration or e2e"
       (selects) from "not integration" (excludes) once the term's
       presence is already confirmed by condition 1.
    """
    if not markexpr or not _INTEGRATION_TOKEN.search(markexpr):
        return False
    try:
        # pytest's own mark-expression evaluator -- not part of the
        # public API, but it is the one implementation of pytest's own
        # `-m` grammar (parentheses, and/or/not), and reimplementing
        # that grammar here would risk a second, subtly different
        # parser drifting from pytest's actual `-m` semantics.
        from _pytest.mark.expression import Expression

        expression = Expression.compile(markexpr)
    except Exception:  # pylint: disable=broad-exception-caught
        # An unparsable expression is pytest's own error to report
        # elsewhere; this guard only ever adds an extra failure on top
        # of a run that is otherwise fine, so it fails open here.
        return False
    return expression.evaluate(_only_the_integration_mark)


def _only_the_integration_mark(name: str, /, **_kwargs: str | int | bool | None) -> bool:
    """A hypothetical item's sole keyword is `"integration"` -- the
    exact `ExpressionMatcher` shape `Expression.evaluate` requires."""
    return name == "integration"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Fail loudly, not skip, when `-m integration` was explicitly
    selected but no database is configured (section 15).

    Raises `pytest.UsageError`, not `pytest.fail()` -- `pytest.fail()`
    is a *test*-outcome signal, meant to be raised from inside a test or
    fixture; raised from a collection hook instead, pytest has no test
    to attribute it to and reports it as an `INTERNALERROR` traceback
    dump. `UsageError` is the mechanism for "this invocation itself is
    invalid" (the same one a bad CLI flag produces) -- a short, clean
    one-line message and a non-zero exit, no traceback, exactly the
    "collection-time guard fails" behaviour this section calls for.
    """
    markexpr = config.option.markexpr or ""
    if not _markexpr_selects_integration(markexpr) or _base_database_url() is not None:
        return
    if any("integration" in item.keywords for item in items):
        raise pytest.UsageError(
            "-m integration was explicitly selected but DATABASE_URL is not set -- "
            "set DATABASE_URL to run PostgreSQL integration tests."
        )


def _with_database_name(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def run_alembic(*, database_url: str, target: str, downgrade: bool = False) -> None:
    """Run `alembic upgrade`/`downgrade` against `database_url`. Public
    (not `_`-prefixed) so `test_migrations.py` can drive its own
    dedicated upgrade-downgrade-upgrade cycle with the exact same
    mechanism the session fixture below uses, on its own database rather
    than the one every other integration test shares.

    `alembic/env.py` always reads `DATABASE_URL` from the real process
    environment through `DatabaseConfig.from_env()` (ADR 0002 section
    14: "Alembic reads the same variable" the application does) --
    pointing one specific invocation at a given temporary database
    therefore means setting that environment variable around the call
    and restoring whatever was there before, not only setting it on the
    `alembic.config.Config` object (which `env.py`'s own
    `DatabaseConfig.from_env()` read would otherwise silently override).
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        config = Config(str(_ALEMBIC_INI))
        if downgrade:
            command.downgrade(config, target)
        else:
            command.upgrade(config, target)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


async def run_alembic_async(*, database_url: str, target: str, downgrade: bool = False) -> None:
    """`await`-able wrapper around `run_alembic`, required whenever the
    caller is itself inside a running event loop (any async fixture or
    async test in this package).

    `alembic/env.py`'s online path ends in `asyncio.run(run_migrations_online())`
    (ADR 0002 section 4's async-Alembic bridge) -- `asyncio.run()` raises
    `RuntimeError: asyncio.run() cannot be called from a running event
    loop` if one is already running, which every `async def` fixture and
    test here has. Running the whole synchronous `run_alembic` call in a
    separate thread (`asyncio.to_thread`) gives Alembic's own
    `asyncio.run()` a thread with no event loop of its own, exactly as
    if it had been invoked from a plain synchronous script.
    """
    await asyncio.to_thread(
        run_alembic, database_url=database_url, target=target, downgrade=downgrade
    )


async def create_temporary_database(base_url: str) -> str:
    """Create a run-unique PostgreSQL database (unmigrated) and return
    its full URL. Public for the same reason as `run_alembic` above."""
    # uuid4().hex is our own generated value, not untrusted input -- safe
    # to interpolate as a SQL identifier, which PostgreSQL gives no
    # parameterized-bind-value syntax for (CREATE/DROP DATABASE take an
    # identifier, never a literal).
    database_name = f"ai_daily_digest_test_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        await admin_engine.dispose()
    return _with_database_name(base_url, database_name)


async def drop_temporary_database(base_url: str, database_url: str) -> None:
    """Drop the database named by `database_url`, connecting through
    `base_url`'s host/credentials. `WITH (FORCE)` (PostgreSQL 13+)
    disconnects any lingering session on it first, rather than requiring
    the caller to track and close every connection by hand."""
    database_name = urlsplit(database_url).path.lstrip("/")
    admin_engine = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
            )
    finally:
        await admin_engine.dispose()


@pytest_asyncio.fixture
async def own_temporary_database() -> AsyncIterator[str]:
    """Create a run-unique dedicated unmigrated PostgreSQL database for tests that drive their own migration lifecycle."""
    base_url = _base_database_url()
    if base_url is None:
        pytest.skip("set DATABASE_URL to run PostgreSQL integration tests")
    database_url = await create_temporary_database(base_url)
    try:
        yield database_url
    finally:
        await drop_temporary_database(base_url, database_url)


@pytest_asyncio.fixture(scope="session")
async def temporary_database_url() -> AsyncIterator[str]:
    """Create a run-unique PostgreSQL database, migrate it to `head`,
    yield its URL, then run `downgrade base` and drop it. Shared by
    every integration test in this session except `test_migrations.py`,
    which needs to mutate schema state itself and so drives its own,
    separate database through the same helpers."""
    base_url = _base_database_url()
    if base_url is None:
        pytest.skip("set DATABASE_URL to run PostgreSQL integration tests")

    temporary_url = await create_temporary_database(base_url)
    await run_alembic_async(database_url=temporary_url, target="head")

    try:
        yield temporary_url
    finally:
        # A downgrade failure must not prevent the drop -- the temporary
        # database is deleted either way; only the down-path coverage is
        # lost, and that failure is still logged, never swallowed
        # silently.
        try:
            await run_alembic_async(database_url=temporary_url, target="base", downgrade=True)
        # Teardown must not raise past the drop below -- any downgrade
        # failure is logged, never silently swallowed.
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("alembic downgrade base failed during integration teardown")

        await drop_temporary_database(base_url, temporary_url)


@pytest_asyncio.fixture(scope="session")
async def database_engine(temporary_database_url: str) -> AsyncIterator[AsyncEngine]:
    """The run's one engine against the temporary database -- mirrors
    the composition root building exactly one engine per process (ADR
    0002 section 12.6)."""
    engine = build_engine(DatabaseConfig(database_url=temporary_database_url))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def database_session(database_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One per-test transaction, rolled back at teardown -- for ordinary
    tests, including ones that call code under test which itself calls
    `session.commit()` (e.g. `ingestion/service.py::ingest_document`,
    which owns and commits its own transaction per ADR 0002 section 13).

    `join_transaction_mode="create_savepoint"` is the SQLAlchemy 2.0
    documented pattern for exactly this: the session joins the already-
    open outer `connection.begin()` transaction via a SAVEPOINT, so an
    application-level `session.commit()` only releases that SAVEPOINT
    (and SQLAlchemy immediately opens a fresh one) rather than committing
    the outer transaction for real. Nothing this fixture's teardown
    rolls back was ever actually durable -- exactly the isolation
    section 15 calls for ("ordinary, non-committing tests run inside a
    per-test transaction that is rolled back at teardown"), now safe
    even when the code under test commits internally. Concurrency tests
    still must not use this fixture -- see `open_database_session`."""
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
def open_database_session(
    temporary_database_url: str,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """A separate-connection helper for concurrency tests (section 15):
    the mandatory duplicate-ingestion and latest-pointer-and-metadata
    tests need genuinely concurrent transactions on separate
    connections that really commit -- the `database_session` fixture's
    single outer rollback cannot isolate them, and must not be used by
    those tests.

    Returns a zero-argument async context manager factory; a test opens
    as many independent sessions as it needs:

        async with open_database_session()() as session_a:
            ...
        async with open_database_session()() as session_b:
            ...
    """

    @asynccontextmanager
    async def _open() -> AsyncIterator[AsyncSession]:
        # A dedicated engine (and pool) per call, disposed when the
        # session closes -- each call is meant to model an independent
        # concurrent actor (a separate process or request), not a
        # second handle onto the run's shared pool.
        engine = create_async_engine(temporary_database_url)
        session_factory = build_session_factory(engine)
        try:
            async with session_factory() as session:
                yield session
        finally:
            await engine.dispose()

    return _open
