# Engineering Standards and Feedback Loops

## Why these standards exist

The purpose is not to create paperwork. The purpose is to make mistakes cheap: detect them while the author still remembers the code, before another teammate builds on top of it.

## Plain-English glossary

### Formatter

A formatter rewrites code into one agreed visual style. Nobody debates spaces, line wrapping, or quote style because the tool decides. This project uses `ruff format`.

### Linter

A linter reads code without running it and flags suspicious patterns: unused imports, undefined names, bad naming, unreachable code, overly complex functions, and similar problems.

- Ruff is the very fast first linter and import sorter.
- Pylint is the slower second opinion for design and code smells.
- Ruff rule `F401` catches unused imports.

### Type checker

Python can accept the wrong kind of value until the code reaches that line at runtime. Type hints describe expected values; mypy checks those expectations before execution. It can catch, for example, passing an article URL where a `SourceItem` object is required.

### Static security analysis

Bandit scans Python syntax for common unsafe patterns. `pip-audit` checks installed dependencies against known vulnerability reports. They solve different problems and neither replaces code review.

### Sonar loop

This usually means SonarQube or SonarQube Cloud analysis. It combines maintainability, reliability, security, duplication, and coverage findings into a quality gate. It belongs in pull-request CI after the basic local tools work. It should focus on new code so the team does not inherit an impossible backlog.

## Inner loop: seconds, ideally under one minute

The inner loop is the cycle repeated while actively coding:

```text
make a small edit -> run a narrow check -> read failure -> correct it
```

Run `make check`, which contains:

1. Format validation.
2. Ruff lint/import validation.
3. Mypy type checking.
4. Fast unit tests, excluding integration and end-to-end tests.

While developing one collector, run its individual test file first. Run the entire inner loop before handing work to an agent, reviewer, or teammate.

Why the sub-minute target matters: if feedback takes ten minutes, developers postpone it and work on something else. The eventual failure is harder to connect to the responsible edit.

## Outer loop: pull request and CI

The outer loop checks the integrated repository in a clean environment:

1. Repeat formatter, Ruff, and mypy checks.
2. Run Pylint.
3. Run all unit, contract, and integration tests with coverage.
4. Run Bandit and dependency vulnerability scanning.
5. Build deployable artifacts.
6. Run SonarQube Cloud and CodeQL when those repository services are enabled.
7. Require human peer review.

`make ci` includes the Python package build. Container/frontend builds will join the same outer
loop when those artifacts exist.

The target is under ten minutes for ordinary pull requests. Jobs should run in parallel. Slow end-to-end or live-source tests can run nightly instead of blocking every PR.

Reducing outer-loop latency does not mean removing checks. It means caching dependencies, splitting independent jobs, using saved fixtures instead of live sites, and running the cheapest failure-prone checks first.

## Recommended tools

| Need | Tool | Where |
|---|---|---|
| Reproducible Python environment and lockfile | uv | Local and CI |
| Formatting | Ruff formatter | Save/commit/CI |
| Fast lint, imports, naming | Ruff | Save/commit/CI |
| Deeper Python code smells | Pylint | Pre-PR/CI |
| Static types | mypy | Inner loop/CI |
| Tests and coverage | pytest + pytest-cov | Inner/outer loops |
| Local Git hooks | pre-commit | Commit/pre-push |
| Code security patterns | Bandit | Outer loop |
| Dependency vulnerabilities | pip-audit + Dependabot | Outer loop/GitHub |
| Cross-language quality gate | SonarQube Cloud | PR/main, after connection |
| Data validation | Pydantic | Runtime boundaries |
| API schema | FastAPI-generated OpenAPI | Contract tests |

Ruff and Pylint overlap. Ruff stays in the inner loop because it is fast. Pylint stays in the outer loop and should be tuned to avoid duplicating formatter complaints.

The Makefile runs the project as a non-editable wheel install. This verifies the packaged `src`
layout and avoids relying on platform-specific editable-install `.pth` behavior. `uv` rebuilds the
local wheel when project files change.

## Naming conventions

- Python files, functions, parameters and variables: `snake_case`.
- Classes, exceptions and Pydantic models: `PascalCase`.
- Constants: `UPPER_SNAKE_CASE`.
- Private implementation details: leading underscore, such as `_parse_entry`.
- API paths: lowercase plural nouns, such as `/v1/updates`.
- Database tables: lowercase plural nouns, such as `document_snapshots`.
- Tests: `test_<behavior>_<condition>`, such as `test_collect_returns_empty_result_when_feed_is_unchanged`.
- Branches: `feat/<issue>-<description>`, `fix/...`, `docs/...`, `chore/...`.

Avoid files named `utils.py`, `helpers.py`, or classes named `Manager` until the narrower domain name is genuinely impossible.

## Test pyramid for this project

- Many unit tests: URL normalization, feed parsing, hashing, duplicate matching, fact comparison, prompt-output validation.
- Some integration tests: PostgreSQL/pgvector, migrations, email adapter, LLM adapter with recorded responses.
- Few end-to-end tests: ingest fixture, generate digest, expose API result, and stage an email.
- Nightly live-source smoke tests: verify official feeds still parse without importing their content into normal test results.

Never make normal unit tests depend on live OpenAI, Anthropic, Google, xAI, or email services.

## Peer review responsibilities

The author proves the change works. The reviewer checks assumptions the author may have missed.

Review in this order:

1. Does the behavior meet the issue?
2. Can incorrect output harm provenance, subscribers, or historical data?
3. Are module boundaries and public contracts preserved?
4. Are failure and retry behavior correct?
5. Are tests meaningful?
6. Is the code understandable?

Do not spend peer-review time on formatting that automation can decide.

## Rules versus enforcement

`AGENTS.md` and `CLAUDE.md` guide coding agents. They are context, not a security boundary. CI, branch protection, tests, hooks, and permissions provide enforcement.

Repository rules must be specific and concise. When an agent repeats the same mistake, improve a rule or automated check. Do not add a long paragraph of vague advice.

## GitHub settings to enable manually

After the repository is pushed to GitHub:

1. Protect `main`.
2. Require a pull request and at least one approval.
3. Dismiss stale approvals when new code is pushed.
4. Require conversation resolution.
5. Require the `quality`, `tests`, and `security` status checks.
6. Block force pushes and branch deletion.
7. Enable the dependency graph, Dependabot alerts, and secret scanning/push protection if available.
8. Enable CodeQL default setup if available for the repository plan.
9. Connect SonarQube Cloud only after its organization/project identifiers and quality gate are agreed.

## Suggested Sonar quality gate for new code

- No new blocker or critical reliability/security issues.
- Security hotspots reviewed.
- At least 80% coverage on new code.
- Less than 3% duplicated lines on new code.
- Maintainability rating A on new code.

Treat these as a starting policy. Record any permanent change as an ADR rather than quietly weakening the gate.
