"""The application as deployed: real lifespan (engine, session factory, logging), real per-request
sessions and transactions, no dependency overrides. Rows are committed, so the one test user is
removed afterwards."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.config import Settings
from apps.api.email.transport import InMemoryTransport
from apps.api.main import create_app
from persistence.database import create_session_factory
from persistence.models import UserRecord
from tests.integration.api.conftest import token_from

from .support import PASSWORD, WEB


@pytest.fixture
async def live(
    settings: Settings, engine: AsyncEngine
) -> AsyncIterator[tuple[AsyncClient, InMemoryTransport]]:
    app = create_app(settings)
    outbox = InMemoryTransport()
    app.state.email_transport = outbox
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://testserver") as client:
            yield client, outbox
    root.handlers, root.level = saved
    logging.getLogger("uvicorn.access").disabled = False


async def test_register_verify_login_and_me_through_the_real_stack(
    live: tuple[AsyncClient, InMemoryTransport], engine: AsyncEngine
) -> None:
    client, outbox = live
    email = f"wiring-{uuid.uuid4().hex[:8]}@example.com"
    try:
        register = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": "Wire"}
        )
        assert register.status_code == 202
        verify = await client.post("/api/v1/auth/verify-email", json={"token": token_from(outbox.outbox[-1])})
        assert verify.status_code == 200
        login = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=WEB
        )
        assert login.status_code == 200
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {login.json()['accessToken']}"}
        )
        assert (me.status_code, me.json()["emailVerified"]) == (200, True)
        assert (await client.post("/api/v1/auth/refresh", headers=WEB)).status_code == 200
    finally:
        async with create_session_factory(engine)() as db, db.begin():
            await db.execute(delete(UserRecord).where(UserRecord.email == email))
