"""Ingestion persistence package — ADR 0002."""

from ai_daily_digest.ingestion.db.models import DocumentSnapshotRow, SourceItemRow

__all__ = ["DocumentSnapshotRow", "SourceItemRow"]
