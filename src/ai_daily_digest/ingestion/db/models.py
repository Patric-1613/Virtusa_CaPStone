"""SQLAlchemy models for ingestion persistence — ADR 0002 §9."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ai_daily_digest.shared.db.metadata import Base, UTCDateTime

__all__ = ["DocumentSnapshotRow", "SourceItemRow"]


class SourceItemRow(Base):
    """PostgreSQL storage model for source_items — ADR 0002 §9."""

    __tablename__ = "source_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    first_fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    authors: Mapped[list[str]] = mapped_column(
        ARRAY(Text).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latest_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["latest_snapshot_id", "id"],
            ["document_snapshots.id", "document_snapshots.source_item_id"],
            name="fk_source_items_latest_snapshot_composite",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("idx_source_items_pagination", "first_fetched_at", "id"),
        Index("idx_source_items_publisher", "publisher"),
        Index("idx_source_items_source_id", "source_id"),
    )


class DocumentSnapshotRow(Base):
    """PostgreSQL storage model for document_snapshots — ADR 0002 §9."""

    __tablename__ = "document_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "source_item_id", "content_hash", name="uq_document_snapshots_item_content"
        ),
        UniqueConstraint("id", "source_item_id", name="uq_document_snapshots_id_source_item"),
        Index("idx_document_snapshots_item_fetched", "source_item_id", "fetched_at"),
    )
