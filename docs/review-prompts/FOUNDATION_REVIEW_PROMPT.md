# Prompt for Claude Code — Review Our Engineering Foundation

You are acting as a critical senior software architect, Python quality engineer, DevSecOps reviewer, and agentic-application reviewer.

## Review mode and restrictions

- Start by reading the repository rather than relying only on this prompt.
- Read `AGENTS.md` and `CLAUDE.md` first, then inspect every file listed below.
- This is an **opinion and architecture review only**.
- Do not edit, create, delete, format, stage, commit, push, install, publish, or deploy anything.
- Do not change the proposed ADRs from `Proposed` to `Accepted`.
- You may run read-only inspection commands such as `git status`, `git diff`, `rg`, and file reads.
- Do not run commands that change Git state or external services.
- If you believe a change is needed, describe the exact proposed change and rationale without applying it.
- Be willing to disagree with the current design. Do not approve it merely because it is detailed.

## Project context

Three interns are building an AI Daily Digest web application. It will:

1. Collect official AI-company announcements, API changes, release notes, articles, and selected secondary coverage.
2. Normalize and deduplicate collected material while preserving provenance.
3. Store immutable historical snapshots.
4. Retrieve older related facts when new information arrives.
5. Explain what changed between the old and new information.
6. Generate a grounded digest whose factual claims cite stored evidence.
7. Publish the digest through a web application.
8. Send scheduled emails to subscribers with links back to the website.
9. Provide a grounded chat experience over stored updates.

The product is expected to use agentic workflows such as LangGraph and selected LangChain interfaces. Deep Agents should be used only if a concrete long-horizon task justifies it.

The team wants production-oriented, maintainable work without:

- coding before agreeing on architecture;
- three interns overwriting the same files;
- different data shapes in different modules;
- coding agents drifting away from project decisions;
- trusting an LLM's statement that code is correct without automated evidence;
- storing only embeddings and losing historical traceability;
- publishing unsupported claims;
- creating fragile collectors where one failed source stops the entire run;
- adding frameworks merely to make the technology list look impressive.

## Existing working style

The current developer uses Claude Code from the VS Code terminal for implementation and uses Codex as a separate reviewer and architecture discussion partner.

Before the repository foundation was created, the developer normally generated Markdown documents for proposals, implementation plans, testing, changes, Git progress, and publishing. Architecture was kept in private notes or a to-do list and reopened whenever the AI appeared to drift.

We want to preserve the useful parts of that workflow—planning, explicit review, and stopping when an issue is found—while moving stable knowledge into shared repository files and moving mechanical enforcement into executable tools and CI.

The intended human/AI workflow is:

```text
issue with acceptance criteria
    -> Claude Code inspects and proposes a file-level plan
    -> developer approves the plan
    -> Claude implements a small change
    -> narrow relevant test
    -> make check
    -> developer reviews the actual diff
    -> Codex reviews correctness, architecture, security, provenance, and tests
    -> findings are corrected
    -> make ci
    -> human teammate peer review
    -> merge only after required checks pass
```

Claude Code and Codex are reviewers/implementers, but neither replaces human approval or automated checks.

## Proposed architecture

The current proposal is a modular monolith with one repository and firm internal boundaries:

```text
Official feeds/APIs/pages
        -> ingestion module
        -> immutable raw snapshots + PostgreSQL system of record
        -> intelligence/change-detection workflow
        -> delivery API, web, email, and grounded chat
```

Proposed ownership:

- Person A: `ingestion` — collection, normalization, provenance, snapshots, deduplication, retries, and run reporting.
- Person B: `intelligence` — historical retrieval, typed fact extraction, change detection, grounded digest generation, and evaluation.
- Person C: `delivery` — API, subscriptions, email, chat integration, frontend-facing services, and deployment adapters.
- Shared: `shared` — only the contracts, protocols, configuration, and errors genuinely used across modules. Changes require peer review.

Proposed technology baseline:

- Python 3.12.
- `uv` with committed `uv.lock`.
- FastAPI and Pydantic v2 for the HTTP API and schemas.
- PostgreSQL as the system of record.
- pgvector in PostgreSQL as a derived semantic-search index.
- SQLAlchemy 2 and Alembic for persistence and migrations.
- httpx and feedparser for collection.
- LangGraph for explicit stateful agent workflows.
- LangChain interfaces only where useful.
- React, TypeScript, and Vite for the frontend.
- A separately invoked worker command plus a hosting-platform scheduler instead of an in-process scheduler or immediate Celery/Redis deployment.

Important proposed invariants:

- Raw source snapshots are immutable.
- PostgreSQL, not the vector index, is the source of truth.
- Embeddings reference a snapshot and embedding-model version and can be rebuilt.
- Collected content is untrusted data and may contain prompt injection.
- LLM output is not evidence.
- Every published factual claim must cite one or more stored source snapshots.
- Deterministic logic handles URLs, hashes, dates, numeric comparisons, citation validation, and idempotency wherever possible.
- One source failure must not abort other collectors.
- Scheduled jobs and email delivery must be idempotent.

## Repository files and their intended roles

### Agent instructions

- `AGENTS.md`
  - Canonical shared repository instructions.
  - Contains project goal, architecture boundaries, local commands, Python conventions, test rules, security rules, and code-review rules.
  - Intended for Codex and as the shared source imported by Claude Code.

- `CLAUDE.md`
  - Imports `AGENTS.md` using `@AGENTS.md`.
  - Contains only Claude Code-specific workflow notes.
  - Intended to prevent separate Claude and Codex rulebooks from drifting.

### Human documentation

- `README.md`
  - Project entry point and reading order.

- `CONTRIBUTING.md`
  - Issue, branch, pull-request, review, merge, and definition-of-done rules.

- `docs/ARCHITECTURE.md`
  - Proposed system architecture, module boundaries, data entities, workflows, storage rules, reliability, security, deployment shape, and milestones.

- `docs/API_CONTRACT.md`
  - Draft public endpoint conventions and example contracts for source items, snapshots, changes, digest claims, and chat responses.

- `docs/ENGINEERING_STANDARDS.md`
  - Plain-English explanation of formatting, linting, type checking, inner/outer loops, Sonar, naming, tests, peer review, and suggested GitHub protection settings.

- `docs/TEAM_WORKFLOW.md`
  - Three-person ownership model, conflict-avoidance rules, daily rhythm, issue format, and Claude/Codex workflow.

- `docs/adr/README.md`
  - ADR purpose and template.

- `docs/adr/0001-modular-monolith.md`
  - Proposed decision to use a modular monolith.

- `docs/adr/0002-postgres-pgvector.md`
  - Proposed decision to use PostgreSQL as the system of record and pgvector as a derived index.

- `docs/adr/0003-quality-gates.md`
  - Proposed decision for fast local checks and a broader pull-request loop.

- `PART_A_SOURCE_RESEARCH.md`
  - Source research, ingestion strategy, data shape, deduplication, reliability, and acceptance recommendations for Person A.

### Executable standards and automation

- `pyproject.toml`
  - Python project metadata and configuration for Ruff, mypy, pytest, coverage, Pylint, and Bandit.

- `uv.lock`
  - Reproducible resolved dependency versions.

- `.python-version`
  - Python 3.12 selection.

- `Makefile`
  - Standard commands:
    - `make bootstrap`: synchronize the development environment.
    - `make format`: apply Ruff formatting and safe lint fixes.
    - `make check`: intended inner loop; formatting check, Ruff, mypy, and fast unit tests.
    - `make ci`: intended pre-PR loop; inner checks plus Pylint, full non-live tests, Bandit, and dependency audit.
    - `make hooks`: install pre-commit and pre-push hooks.

- `.pre-commit-config.yaml`
  - Ruff on commit; mypy and fast tests on push.

- `.github/workflows/ci.yml`
  - Parallel `quality`, `tests`, and `security` jobs for pull requests and `main`.

- `.github/dependabot.yml`
  - Weekly uv and GitHub Actions dependency updates.

- `.github/pull_request_template.md`
  - Requires outcome, module/contract impact, verification, risk, rollback, and reviewer focus.

- `.env.example`
  - Empty environment-variable names only; no secrets.

- `.gitignore`
  - Excludes environments, caches, coverage files, secrets, personal Claude instructions, raw runtime data, logs, and frontend build data.

- `sources.yaml`
  - Starter registry of official feeds, changelogs, APIs, and permitted page sources.

### Initial package layout

```text
src/ai_daily_digest/
├── ingestion/
├── intelligence/
├── delivery/
└── shared/
```

The package currently contains only the foundation and a smoke test. Runtime framework dependencies should be added only after the team reviews and accepts or changes the proposed ADRs.

The configured checks have already been executed successfully on the foundation:

- Ruff formatting and linting passed.
- mypy passed.
- pytest passed with the configured coverage threshold.
- Pylint passed.
- Bandit reported no issues.
- `pip-audit` reported no known vulnerable dependencies.

Do not treat these foundation results as evidence that the future application is correct; they only verify the current scaffold.

## Intended documentation policy

We do not want one permanent Markdown file for every mechanical action.

Use committed documentation for durable knowledge:

- architecture;
- contracts;
- coding/security standards;
- ADRs;
- contributor and operational instructions.

Use a GitHub issue or PR description for temporary information:

- one feature's plan;
- expected files;
- acceptance criteria;
- current progress;
- test evidence;
- review findings;
- rollout and rollback notes.

Use executable files for enforceable facts:

- tests in `tests/`;
- tooling configuration in `pyproject.toml`;
- dependency resolution in `uv.lock`;
- CI in `.github/workflows/`;
- source configuration in `sources.yaml`.

Create a new ADR only when a lasting architecture, storage, security, deployment, contract, or quality-gate decision needs a rationale. Do not create separate permanent documents only to record a Git push or a routine test run.

## Questions you must answer

Review the actual repository and answer all of the following:

1. Is the modular-monolith architecture appropriate for this product, team size, and likely project duration? If not, propose a simpler or stronger alternative.
2. Are the boundaries between `ingestion`, `intelligence`, `delivery`, and `shared` clear enough to prevent accidental coupling and concurrent-edit conflicts?
3. Is PostgreSQL plus pgvector a sensible first storage choice? Identify migration, backup, indexing, and data-retention concerns that are currently missing.
4. Is the separation between deterministic code and LLM/agent work correct? Point out any tasks currently assigned to an LLM that should be deterministic, or vice versa.
5. Is LangGraph justified? What minimum workflow should use it, and what should remain ordinary Python?
6. Does the data/API contract contain the minimum information necessary for historical change tracking, provenance, citations, subscriptions, and chat?
7. Are any important domain entities or invariants missing?
8. Are the inner and outer feedback loops practical? Identify duplicated, slow, weak, or incorrectly placed checks.
9. Is using both Ruff and Pylint worthwhile here, or is it unnecessary duplication?
10. Is the 80% coverage policy appropriate? Explain whether it should apply globally, to changed/new code, or in phases.
11. Are the security controls sufficient for web collection, prompt injection, subscriber data, email delivery, and scheduled jobs?
12. What additional agentic-system evaluations are essential before automatic publication?
13. Do `AGENTS.md` and `CLAUDE.md` contain the right information, or are they too long, too vague, too restrictive, duplicated, or missing critical instructions?
14. Does the repository contain unnecessary documentation that will become stale? Which files should be merged, shortened, moved, or removed?
15. Is the proposed team workflow realistic for three interns? Where can the process be simplified without losing safety?
16. What should be completed before the first application feature is implemented?
17. What should deliberately be postponed until after the first vertical slice?
18. Identify any contradiction between the documentation, configuration, CI workflow, package layout, and source registry.
19. Identify anything that could make the repository pass automated checks while still producing an inaccurate or unsafe digest.
20. Give a final decision: `ACCEPT`, `ACCEPT WITH CHANGES`, or `REJECT`, with a concise explanation.

## Required response format

Return the review using exactly these sections:

### 1. Executive verdict

- Decision: `ACCEPT`, `ACCEPT WITH CHANGES`, or `REJECT`.
- Five to ten sentences explaining the decision.

### 2. What is already strong

List strengths that should be preserved. Do not add praise unless you can point to a specific file or rule.

### 3. Must fix before feature coding

For each item include:

- severity;
- affected files;
- concrete problem;
- likely consequence;
- recommended change;
- how to verify the change.

### 4. Should fix during the first vertical slice

Use the same fields as above.

### 5. Safe to postpone

List things that are useful later but would be premature now.

### 6. Architecture and data-contract review

Discuss module boundaries, data ownership, persistence, vector indexing, version history, API compatibility, and idempotency.

### 7. Agentic-workflow and evaluation review

Discuss LangGraph scope, model boundaries, structured outputs, prompt injection, prompt/model versioning, fixtures, evaluations, citations, and publication gates.

### 8. Developer workflow and collaboration review

Discuss Claude implementation, Codex review, human review, branches, PR size, shared-file changes, and documentation policy.

### 9. Tooling and CI review

Discuss uv, Ruff, mypy, Pylint, pytest/coverage, pre-commit, Bandit, `pip-audit`, Dependabot, Sonar/CodeQL, and outer-loop latency.

### 10. File-by-file recommendations

Provide a compact table:

| File | Keep/change/remove | Recommendation |
|---|---|---|

Include every major file named in this prompt.

### 11. Recommended first vertical slice

Provide a small end-to-end slice that all three interns can integrate without waiting for the complete system. Include acceptance tests.

### 12. Prioritized action list

Give no more than ten ordered actions. Separate actions required before coding from actions appropriate after the first vertical slice.

### 13. Open questions for the team or mentor

Ask only questions whose answers would materially change the architecture, workflow, security, or delivery plan.

Be concise where the design is sound and detailed where you identify real risk. Avoid redesigning the project merely to demonstrate expertise.
