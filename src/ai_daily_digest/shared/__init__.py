"""Shared cross-module contract types. See shared/README.md for what
belongs here vs. an intelligence/ingestion/delivery-internal module.
"""

from ai_daily_digest.shared.snapshot_resolver import InMemorySnapshotResolver, SnapshotResolver

__all__ = ["InMemorySnapshotResolver", "SnapshotResolver"]
