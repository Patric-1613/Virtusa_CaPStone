"""Pluggable data source for everything downstream in intelligence/.

FixtureLoader is wired up now. StoreLoader is a stub until ingestion's
database exists — swapping between them at that point should be the
one-line change in get_loader() below, nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from ai_daily_digest.shared.schemas import (
    ChangeSet,
    Digest,
    DocumentSnapshot,
    ExtractedFact,
    SourceItem,
)

# repo_root/tests/fixtures/contracts — four levels up from this file
# (src/ai_daily_digest/intelligence/loaders.py -> repo root)
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "contracts"


class Loader(Protocol):
    """The interface everything downstream depends on — FixtureLoader and
    StoreLoader both satisfy it, so swapping one for the other (get_loader
    below) never requires touching a caller. Method names match the
    contract type they return; none of them filter or transform, they
    just load."""

    def load_items(self) -> list[SourceItem]: ...
    def load_snapshots(self) -> list[DocumentSnapshot]: ...
    def load_facts(self) -> list[ExtractedFact]: ...
    def load_change_sets(self) -> list[ChangeSet]: ...
    def load_digests(self) -> list[Digest]: ...


class FixtureLoader:
    """Reads tests/fixtures/contracts/*.json. Raises on any record that
    doesn't validate against shared/schemas.py — a broken fixture should
    fail loudly, not silently drop a row."""

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self.fixtures_dir = fixtures_dir

    def _read(self, name: str) -> list[dict[str, Any]]:
        path = self.fixtures_dir / name
        with path.open("r", encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)
        return data

    def load_items(self) -> list[SourceItem]:
        return [SourceItem.model_validate(row) for row in self._read("source_items.json")]

    def load_snapshots(self) -> list[DocumentSnapshot]:
        return [DocumentSnapshot.model_validate(row) for row in self._read("snapshots.json")]

    def load_facts(self) -> list[ExtractedFact]:
        return [ExtractedFact.model_validate(row) for row in self._read("extracted_facts.json")]

    def load_change_sets(self) -> list[ChangeSet]:
        return [ChangeSet.model_validate(row) for row in self._read("change_sets.json")]

    def load_digests(self) -> list[Digest]:
        return [Digest.model_validate(row) for row in self._read("digests.json")]

    def snapshot_text(self, snapshot_id: str) -> str:
        """Convenience: SourceItem carries no body (see shared/schemas.py)
        — callers needing text for a given item look it up by its
        snapshot id."""
        for snapshot in self.load_snapshots():
            if snapshot.id == snapshot_id:
                return snapshot.content_text or ""
        return ""


class StoreLoader:
    """Reads from the real database once ingestion's store exists (see
    docs/adr/0002-postgres-pgvector.md). Not implemented yet — do not wire
    this up until Gate 1."""

    def load_items(self) -> list[SourceItem]:
        raise NotImplementedError("StoreLoader is a stub until ingestion's store exists.")

    def load_snapshots(self) -> list[DocumentSnapshot]:
        raise NotImplementedError("StoreLoader is a stub until ingestion's store exists.")

    def load_facts(self) -> list[ExtractedFact]:
        raise NotImplementedError("StoreLoader is a stub until ingestion's store exists.")

    def load_change_sets(self) -> list[ChangeSet]:
        raise NotImplementedError("StoreLoader is a stub until ingestion's store exists.")

    def load_digests(self) -> list[Digest]:
        raise NotImplementedError("StoreLoader is a stub until ingestion's store exists.")


def get_loader() -> Loader:
    """The one place that decides fixtures vs. real store. Flip this to
    StoreLoader() at Gate 1 — nothing else in intelligence/ should need to
    change."""
    return FixtureLoader()
