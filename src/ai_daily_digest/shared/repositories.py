"""Shared repository protocols for cross-module queries — ADR 0002 §12.2."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from ai_daily_digest.shared.schemas import SourceItem

__all__ = ["SourceItemFeedRepository"]


class SourceItemFeedRepository(Protocol):
    """Asynchronous read protocol for cursor-paginated source item feeds.

    This protocol represents a cross-module seam: the delivery API depends on
    a query capability that ingestion's source persistence provides. The
    concrete PostgreSQL adapter is ingestion-owned, while the protocol lives in
    shared/ (ADR 0002 §12.2).
    """

    async def list_source_items(
        self,
        *,
        publisher: str | None = None,
        source_id: str | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 20,
    ) -> Sequence[SourceItem]:
        """Fetch up to limit + 1 items ordered by (first_fetched_at DESC, id DESC).

        Args:
            publisher: Canonical publisher filter (trimmed, NFC, case-sensitive), if any.
            source_id: Canonical source ID filter (trimmed, NFC, case-sensitive), if any.
            after: Keyset continuation tuple (first_fetched_at, id), if resuming.
            limit: Maximum items to return in the page (the repository returns up to limit + 1
                   to support forward cursor generation).

        Returns:
            A sequence of up to limit + 1 SourceItem instances matching the filters and
            keyset predicate.
        """
        ...
