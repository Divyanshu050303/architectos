"""API test client: the real application, wired to the test's rolled-back transaction."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.config import Settings
from apps.api.dependencies.database import get_db
from apps.api.main import create_app
from tests.integration.conftest import joined_session


@pytest.fixture
def settings(migrated_database_url: str) -> Settings:
    return Settings(database_url=migrated_database_url, cors_allowed_origins=["http://localhost:3000"])


@pytest.fixture
def app(settings: Settings, connection: AsyncConnection) -> FastAPI:
    app = create_app(settings)

    async def test_db() -> AsyncIterator[AsyncSession]:
        # A fresh session per request, like production, on the test's connection.
        session = joined_session(connection)
        try:
            yield session
        finally:
            await session.close()

    app.dependency_overrides[get_db] = test_db
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
