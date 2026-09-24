"""The security suite runs against the real application and database, reusing the integration
fixtures (scratch database migrated with Alembic, rolled-back transaction per test)."""

import pytest

from tests.integration.api.conftest import app, client, clock, outbox, settings  # noqa: F401
from tests.integration.conftest import connection, db, engine, migrated_database_url  # noqa: F401


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)
