"""API test client: the real application, wired to the test's rolled-back transaction."""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.message import EmailMessage

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.config import Settings
from apps.api.dependencies.database import get_db
from apps.api.dependencies.services import get_clock
from apps.api.email.transport import InMemoryTransport
from apps.api.main import create_app
from core.domain.clock import Clock
from tests.integration.conftest import joined_session
from tests.unit.identity.fakes import FakeClock

TEST_SECRET = "test-access-token-secret-" + "x" * 32
TOKEN_IN_LINK = re.compile(r"[?&]token=([A-Za-z0-9_-]+)")


def email_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain",))
    assert part is not None
    return str(part.get_content())


def email_html(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("html",))
    assert part is not None
    return str(part.get_content())


def token_from(message: EmailMessage) -> str:
    match = TOKEN_IN_LINK.search(email_text(message))
    assert match, "no token link in the email"
    return match.group(1)


@pytest.fixture
def settings(migrated_database_url: str) -> Settings:
    # _env_file=None: a developer's local .env must not change what the tests exercise.
    return Settings(
        _env_file=None,
        environment="test",
        database_url=migrated_database_url,
        cors_allowed_origins=["http://localhost:3000"],
        access_token_secret=TEST_SECRET,
    )


@pytest.fixture
def outbox() -> InMemoryTransport:
    return InMemoryTransport()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime.now(UTC))


@pytest.fixture
def app(
    settings: Settings, connection: AsyncConnection, outbox: InMemoryTransport, clock: FakeClock
) -> FastAPI:
    app = create_app(settings)
    app.state.email_transport = outbox

    def test_clock() -> Clock:
        return clock

    app.dependency_overrides[get_clock] = test_clock

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
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        yield client
