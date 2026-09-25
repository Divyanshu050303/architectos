"""Query budgets: how many SQL statements key requests cost, and that the count does not grow
with the amount of data (no N+1). Counted on the test connection all request sessions share."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord, OrganizationMemberRecord, ProjectRecord, UserRecord

from .conftest import token_from

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


@contextmanager
def counting(connection: AsyncConnection) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(*args: Any) -> None:
        statements.append(str(args[2]).split()[0])

    sync = connection.sync_connection
    assert sync is not None
    event.listen(sync, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(sync, "before_cursor_execute", record)


async def owner(client: AsyncClient, outbox: InMemoryTransport) -> tuple[dict[str, str], str]:
    await client.post(
        "/api/v1/auth/register", json={"email": "ada@example.com", "password": PASSWORD, "name": "Ada"}
    )
    await client.post("/api/v1/auth/verify-email", json={"token": token_from(outbox.outbox[-1])})
    login = await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}, headers=WEB
    )
    auth = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    org_id: str = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=auth)).json()[
        "id"
    ]
    return auth, org_id


async def grow(db: AsyncSession, org_id: str, size: int) -> None:
    for i in range(size):
        user = UserRecord(
            email=f"m{i}-{uuid.uuid4().hex[:6]}@example.com", name=f"M{i}", password_hash="$argon2id$x"
        )
        db.add(user)
        await db.flush()
        db.add(OrganizationMemberRecord(organization_id=uuid.UUID(org_id), user_id=user.id, role="member"))
        db.add(AuditLogRecord(action="member.invited", organization_id=uuid.UUID(org_id)))
        db.add(ProjectRecord(organization_id=uuid.UUID(org_id), name=f"Grown {i}", slug=f"grown-{i}"))
    await db.flush()


REQUIREMENT = {
    "type": "functional",
    "category": "order",
    "title": "Place an order",
    "statement": "A customer can place an order.",
    "priority": "high",
}


@pytest.mark.parametrize(
    ("path", "budget"),
    [
        ("/api/v1/me", 2),  # session + user
        ("/api/v1/organizations", 3),  # session + user + memberships join
        ("/api/v1/organizations/{org}", 3),  # session + user + membership join
        ("/api/v1/organizations/{org}/members", 4),  # + one joined member listing
        ("/api/v1/organizations/{org}/audit-log", 4),  # + one keyset page
        ("/api/v1/organizations/{org}/projects", 4),  # + one filtered, sorted page
        ("/api/v1/projects/{project}", 3),  # session + user + project/organization/membership join
        ("/api/v1/projects/{project}/requirements", 4),  # + one filtered page
        ("/api/v1/projects/{project}/requirements/{requirement}", 4),  # + the requirement
        ("/api/v1/projects/{project}/requirements/analysis", 4),  # + one query for all analyzed
        ("/api/v1/projects/{project}/requirements/{requirement}/versions", 4),  # + one page of history
    ],
)
async def test_reads_stay_within_budget_regardless_of_size(
    client: AsyncClient,
    outbox: InMemoryTransport,
    connection: AsyncConnection,
    db: AsyncSession,
    path: str,
    budget: int,
) -> None:
    auth, org_id = await owner(client, outbox)
    project = await client.post(
        f"/api/v1/organizations/{org_id}/projects", json={"name": "Budget"}, headers=auth
    )
    requirements = f"/api/v1/projects/{project.json()['id']}/requirements"
    requirement = await client.post(requirements, json=REQUIREMENT, headers=auth)
    url = (
        path.replace("{org}", org_id)
        .replace("{project}", project.json()["id"])
        .replace("{requirement}", requirement.json()["id"])
    )

    with counting(connection) as small:
        assert (await client.get(url, headers=auth)).status_code == 200
    await grow(db, org_id, 25)
    for i in range(25):
        await client.post(requirements, json=REQUIREMENT | {"title": f"Grown {i}"}, headers=auth)
    with counting(connection) as large:
        assert (await client.get(url, headers=auth)).status_code == 200

    selects = [s for s in large if s.upper() in {"SELECT", "WITH"}]
    assert len(selects) <= budget, large
    assert len(large) == len(small), (small, large)  # independent of data size: no N+1
