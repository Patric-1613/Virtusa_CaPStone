.PHONY: bootstrap format lint typecheck unit check pylint test security build ci hooks eval

# Non-editable installs avoid platform-specific .pth handling while still rebuilding when the
# project changes. This also tests the same wheel layout that production receives.
UV_RUN := uv run --no-editable

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
	$(UV_RUN) pytest -m "not live"

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
