# 0002 — Use PostgreSQL as the system of record and pgvector as a derived index

Status: Proposed  
Date: 2026-08-24

## Context

The product needs durable article metadata, immutable versions, relationships, subscriber state, and semantic retrieval. A vector database alone is weak at audit history, constraints, and structured comparisons.

## Decision

Use PostgreSQL for normalized records and pgvector in the same database for embeddings. Keep raw source snapshots immutable. Every embedding references a document snapshot and embedding-model version and can be rebuilt.

## Consequences

- One database supports both structured queries and semantic retrieval during the MVP.
- Provenance and historical facts remain available even if the vector index is rebuilt.
- The team must manage schema migrations and backups.
- A separate vector service remains an option if measured scale or features require it.

## Deferred operational decisions

These do not block the first fixture-based vertical slice, but must be resolved before deployment:

- backup frequency, retention period, restore owner, and a tested restore procedure;
- retention/deletion rules for raw content and subscriber personal data;
- ordinary PostgreSQL indexes for source freshness, canonical deduplication, snapshot history,
  publication status, and email idempotency;
- the pgvector distance metric and index type, chosen from measured corpus size/query behavior;
- migration and rebuild procedures that prove the derived vector index can be deleted and restored
  without losing source history.
