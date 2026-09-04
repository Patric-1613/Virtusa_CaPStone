from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData, types
from sqlalchemy.orm import DeclarativeBase

__all__ = ["NAMING_CONVENTION", "Base", "UTCDateTime", "metadata"]

# Deterministic constraint and index naming convention per ADR 0002 §12.3:
# Every module-owned ORM model registers against this single MetaData instance.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(types.TypeDecorator[datetime]):  # pylint: disable=too-many-ancestors,abstract-method
    """SQLAlchemy TypeDecorator that guarantees timezone-aware UTC datetimes across all dialects."""

    impl = types.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is not None:
            if not isinstance(value, datetime):
                return value
            if value.tzinfo is None:
                raise ValueError(
                    "Encountered offset-naive datetime; "
                    "storage boundaries require timezone-aware UTC"
                )
            return value.astimezone(UTC)
        return None

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is not None:
            if not isinstance(value, datetime):
                return value
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return None


class Base(DeclarativeBase):
    """Common declarative base for all module-owned ORM models."""

    metadata = metadata
