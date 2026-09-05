.PHONY: bootstrap format lint typecheck unit check pylint test security build ci hooks eval \
	db-up db-down db-migrate db-revision test-integration

# Non-editable installs avoid platform-specific .pth handling while still rebuilding when the
# project changes. This also tests the same wheel layout that production receives.
UV_RUN := uv run --no-editable

# The local compose.yaml's own credentials (docs/adr/0002-postgres-pgvector.md
# section 15/16) -- never a production secret. Applied as a DEFAULT only to
# the database-specific targets below (db-up, db-migrate, db-revision,
# test-integration), via target-specific `?=` -- deliberately NOT exported
# globally here. `check`/`ci`/`test`/`unit` must always see exactly the
# caller's own environment: if DATABASE_URL is genuinely unset there,
# integration tests skip individually with a clear reason (the designed
# behaviour, tests/integration/conftest.py) instead of this Makefile
# silently filling in a DSN that points at nothing and turns a clean skip
# into a confusing connection failure.
DEFAULT_DATABASE_URL := postgresql+psycopg://ai_daily_digest:ai_daily_digest@localhost:5432/ai_daily_digest

bootstrap:
	uv sync --all-groups --no-editable

format:
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

lint:
	$(UV_RUN) ruff format --check .
	$(UV_RUN) ruff check .

typecheck:
	$(UV_RUN) mypy

unit:
	$(UV_RUN) pytest -m "not integration and not e2e and not live"

check: lint typecheck unit

pylint:
	$(UV_RUN) pylint src/ai_daily_digest

test:
	$(UV_RUN) pytest -m "not live" --cov-fail-under=80

security:
	$(UV_RUN) bandit -c pyproject.toml -r src
	$(UV_RUN) pip-audit .

build:
	uv build

ci: check pylint test security build

hooks:
	$(UV_RUN) pre-commit install --hook-type pre-commit --hook-type pre-push

# Currently a self-check against the draft fixture pack, not a real
# evaluation -- see intelligence/evaluate.py's run_eval() docstring.
eval:
	$(UV_RUN) python -m ai_daily_digest.intelligence.evaluate

# -- Local PostgreSQL (docs/adr/0002-postgres-pgvector.md section 15/16) --
# See docs/LOCAL_DATABASE.md for the full walkthrough.

db-up: export DATABASE_URL ?= $(DEFAULT_DATABASE_URL)
db-up:
	docker compose up -d
	@echo "Waiting for postgres to become healthy..."
	@until [ "$$(docker compose ps postgres --format '{{.Health}}')" = "healthy" ]; do sleep 1; done
	@echo "postgres is healthy at $(DATABASE_URL)"

db-down:
	docker compose down

db-migrate: export DATABASE_URL ?= $(DEFAULT_DATABASE_URL)
db-migrate:
	$(UV_RUN) alembic upgrade head

# Usage: make db-revision message="add whatever table"
db-revision: export DATABASE_URL ?= $(DEFAULT_DATABASE_URL)
db-revision:
	@test -n "$(message)" || (echo 'usage: make db-revision message="..."' && exit 1)
	$(UV_RUN) alembic revision -m "$(message)"

# Runs only the PostgreSQL integration suite (-m integration) -- the
# inner `make check` loop stays database-free (Makefile's `unit` target
# already excludes it). Requires `db-up` (or any reachable PostgreSQL at
# DATABASE_URL) first; fails loudly, per tests/integration/conftest.py,
# rather than silently skipping, since -m integration is explicit here.
test-integration: export DATABASE_URL ?= $(DEFAULT_DATABASE_URL)
test-integration:
	$(UV_RUN) pytest -m integration
