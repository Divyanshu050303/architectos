# Backend developer commands. The frontend has its own scripts in apps/web/package.json.
# Integration and migration targets need the local database: `make db-up`.

PY_SOURCES := apps/api core persistence tests
UV := uv run

API_PORT ?= 8000

.PHONY: install db-up db-down migrate migration run lint format typecheck test test-unit \
        test-integration test-api test-security test-eval eval schemas coverage migrate-check check

install:            ## Install Python dependencies (including dev tools) into .venv
	uv sync

db-up:              ## Start local Postgres, Redis and Mailpit and wait until they are ready
	docker compose up -d --wait postgres redis mailpit

db-down:            ## Stop local services (data volume is kept)
	docker compose down

migrate:            ## Apply all migrations to DATABASE_URL
	$(UV) alembic upgrade head

migration:          ## Create a migration from model changes: make migration m="describe change"
	$(UV) alembic revision --autogenerate -m "$(m)"

run:                ## Run the API with auto-reload on API_PORT (default 8000)
	$(UV) uvicorn --factory apps.api.main:create_app --reload --port $(API_PORT) --no-access-log

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

test-api:           ## HTTP-level tests of every endpoint (subset of test-integration)
	$(UV) pytest tests/integration/api

test-security:      ## Security suite: endpoint sweeps, tenant isolation, leaks, traceability
	$(UV) pytest tests/security

test-eval:          ## Requirements Engine regression: quality must not drop below the recorded thresholds
	$(UV) pytest tests/evaluation

eval:               ## Requirements Engine evaluation report (metrics and every difference from the labels)
	$(UV) python -m ai.evaluation.evaluator

schemas:            ## Regenerate the published JSON Schemas (core/schemas) from the code
	$(UV) python -m core.architecture_ir.schema > core/schemas/architecture.schema.json

coverage:           ## Full suite with line coverage of the API, domain and persistence code
	$(UV) pytest --cov=apps/api --cov=core --cov=persistence --cov-report=term-missing:skip-covered

migrate-check:      ## Migrations on clean databases: upgrade, downgrade, re-upgrade, no model drift
	$(UV) pytest tests/integration/database/test_migrations.py

test:               ## Every Python test
	$(UV) pytest

check: lint typecheck test   ## Everything CI runs
