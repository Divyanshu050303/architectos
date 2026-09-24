# Backend developer commands. The frontend has its own scripts in apps/web/package.json.
# Integration and migration targets need the local database: `make db-up`.

PY_SOURCES := apps/api core persistence tests
UV := uv run

.PHONY: install db-up db-down migrate migration lint format typecheck test test-unit \
        test-integration migrate-check check

install:            ## Install Python dependencies (including dev tools) into .venv
	uv sync

db-up:              ## Start local Postgres and wait until it accepts connections
	docker compose up -d --wait postgres

db-down:            ## Stop local services (data volume is kept)
	docker compose down

migrate:            ## Apply all migrations to DATABASE_URL
	$(UV) alembic upgrade head

migration:          ## Create a migration from model changes: make migration m="describe change"
	$(UV) alembic revision --autogenerate -m "$(m)"

lint:               ## Lint and check formatting
	$(UV) ruff check $(PY_SOURCES)
	$(UV) ruff format --check $(PY_SOURCES)

format:             ## Apply formatting and safe lint fixes
	$(UV) ruff format $(PY_SOURCES)
	$(UV) ruff check --fix $(PY_SOURCES)

typecheck:          ## Strict static type checking
	$(UV) mypy

test-unit:          ## Pure logic tests; no database needed
	$(UV) pytest tests/unit

test-integration:   ## Tests against a real, migrated Postgres database
	$(UV) pytest tests/integration -m integration

migrate-check:      ## Migrations on clean databases: upgrade, downgrade, re-upgrade, no model drift
	$(UV) pytest tests/integration/database/test_migrations.py

test:               ## Every Python test
	$(UV) pytest

check: lint typecheck test   ## Everything CI runs
