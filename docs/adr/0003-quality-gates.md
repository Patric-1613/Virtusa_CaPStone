# 0003 — Use fast local checks and parallel CI quality gates

Status: Proposed  
Date: 2026-08-24

## Context

The team needs fast feedback without relying on every developer or coding agent to remember all standards. Full security and integration checks are valuable but too slow to run after every edit.

## Decision

Use Ruff formatting/lint, mypy, and focused pytest tests in a target local loop of under one minute. Use Pylint, the full test suite, Bandit, pip-audit, CodeQL/Sonar when available, builds, and peer review in the pull-request loop. Run independent CI jobs in parallel. Do not broadly skip Bandit rules; a justified line-level suppression is preferable when a real finding is proven safe.

## Consequences

- Common mistakes are caught while the change is still small.
- CI remains the authoritative clean-environment verification.
- Some checks intentionally overlap to prevent environment drift.
- Quality-gate exceptions require an issue and written rationale rather than silent suppression.
