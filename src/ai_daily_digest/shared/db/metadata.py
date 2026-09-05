"""The single SQLAlchemy `MetaData` every module-owned ORM model
registers against (docs/adr/0002-postgres-pgvector.md section 12.3, 12.4).
Placing it in `ingestion/db/` would force a later intelligence-persistence
module to either import `ingestion.db` internals (a module-boundary
violation, forbidden by AGENTS.md) or stand up a second, competing
`MetaData` against the same database -- both rejected by the ADR. This
module performs no I/O on import; it only builds Python objects."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# A deterministic constraint/index naming convention. Without one,
# PostgreSQL auto-generates unpredictable constraint names, which makes
# Alembic autogenerate diffs noisy and makes a constraint-violation error
# message (e.g. from a duplicate `dedupe_key`) unreadable in application
# logs. This is the naming convention SQLAlchemy's own documentation and
# Alembic's cookbook recommend.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """The one `DeclarativeBase` every module's ORM model subclasses.

    Every module-owned ORM model -- `ingestion/db/models.py` today, a
    future `intelligence/db/models.py` under its own ADR -- registers
    against this exact `metadata` object, never a module-local one (ADR
    0002 section 12.4: "Every module's ORM models register against
    `shared.db.metadata` -- never a module-local `MetaData`").
    """

    metadata = metadata
