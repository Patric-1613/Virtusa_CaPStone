# AI Daily Digest

An evidence-backed web application that tracks developments from major AI companies, remembers historical versions, explains what changed, publishes a digest, and emails subscribers.

The repository is currently in its planning and engineering-foundation stage. Application features should not be added until the team approves the initial architecture decisions in `docs/adr/`.

## Start here

1. Read [Architecture](docs/ARCHITECTURE.md).
2. Read [Engineering standards](docs/ENGINEERING_STANDARDS.md).
3. Read [API and data contract](docs/API_CONTRACT.md).
4. Review the decisions in [docs/adr](docs/adr).
5. Install the development environment with `make bootstrap`.
6. Run the fast local checks with `make check`.

## Team modules

| Area | Responsibility | Primary owner |
|---|---|---|
| `ingestion` | Sources, collection, normalization, snapshots, provenance, duplicate detection | Person A |
| `intelligence` | Facts, historical comparison, grounded writing, evaluation | Person B |
| `delivery` | API, web integration, subscriptions, email, chat, deployment adapters | Person C |
| `shared` | Cross-module contracts and configuration | Shared; peer review required |

Folder ownership reduces collisions but does not remove collective responsibility. Every change is reviewed by another person.

## Existing research

- [Part A source research](PART_A_SOURCE_RESEARCH.md)
- [Source registry](sources.yaml)
