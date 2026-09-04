"""Public delivery summary schemas — ADR 0008 §12."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from ai_daily_digest.shared.ids import Uuid7Id

__all__ = ["UpdateSummary"]


class UpdateSummary(BaseModel):
    """Public projection of a source update — ADR 0008 §12.

    List endpoints return delivery-specific summary models rather than raw shared
    domain records, ensuring internal fields (such as dedupe_key, raw storage
    locations, and content_text) are never exposed publicly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Uuid7Id
    source_id: str
    publisher: str
    title: str
    canonical_url: HttpUrl | str
    published_at: datetime | None = None
    first_fetched_at: datetime
    event_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    language: str | None = None
    latest_snapshot_id: Uuid7Id | None = None
