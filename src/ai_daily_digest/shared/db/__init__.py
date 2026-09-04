"""Shared database kernel — ADR 0002 §12.3."""

from ai_daily_digest.shared.db.engine import create_engine, create_session_factory
from ai_daily_digest.shared.db.metadata import NAMING_CONVENTION, Base, metadata

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "create_engine",
    "create_session_factory",
    "metadata",
]
