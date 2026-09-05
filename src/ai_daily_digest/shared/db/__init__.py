"""The shared database kernel (docs/adr/0002-postgres-pgvector.md section
12.3): the one SQLAlchemy `MetaData`/`DeclarativeBase` every module's ORM
models register against, and the one engine/session-factory construction
every process composition root calls. Importing this package -- or
anything in it -- performs no I/O and opens no connection; only calling
`build_engine()`/`build_session_factory()` does (section 12.3: "no
auto-connect on import")."""

from ai_daily_digest.shared.db.engine import build_engine, build_session_factory
from ai_daily_digest.shared.db.metadata import Base, metadata

__all__ = ["Base", "build_engine", "build_session_factory", "metadata"]
