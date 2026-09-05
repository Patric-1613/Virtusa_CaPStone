"""Ingestion-owned database code (docs/adr/0002-postgres-pgvector.md
section 12.4): the `source_items`/`document_snapshots` ORM models and
their PostgreSQL repository implementation. Private to `ingestion/` --
`delivery/` must not import this package directly; it depends only on
the `shared/repositories.py` read Protocol."""
