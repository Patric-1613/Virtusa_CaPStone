# 0001 — Use a modular monolith

Status: Proposed  
Date: 2026-08-24

## Context

Three interns need to develop ingestion, intelligence, and delivery concurrently within a short project. They need clear boundaries and independent tests, but do not need the deployment and distributed-systems cost of microservices.

## Decision

Use one repository and codebase with `ingestion`, `intelligence`, `delivery`, and `shared` Python packages. Run the API and scheduled worker as separate process types from the same build. Enforce boundaries with imports, contracts, ownership, and review.

## Consequences

- Local setup, integration testing, and deployment remain simple.
- Cross-module calls can use typed Python interfaces and the shared database.
- Shared-contract changes require coordination.
- A module may be extracted into a service later only when measured scale or ownership needs justify it.

