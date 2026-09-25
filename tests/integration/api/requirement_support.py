"""Shared setup for the requirement API tests: an owner with an organization and two projects."""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import OrganizationMemberRecord, UserRecord

from .conftest import token_from

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}
RPS: dict[str, Any] = {
    "metric": "requests_per_second",
    "operator": ">=",
    "value": 2000,
    "unit": "requests/second",
}
THROUGHPUT: dict[str, Any] = {
    "type": "capacity",
    "category": "throughput",
    "title": "API throughput",
    "statement": "The API must support 2,000 requests per second.",
    "priority": "critical",
    "status": "active",
    "structuredData": RPS,
}


async def signed_in(client: AsyncClient, outbox: InMemoryTransport, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email[:4]})
    verification = next(m for m in reversed(outbox.outbox) if m["To"] == email)
    await client.post("/api/v1/auth/verify-email", json={"token": token_from(verification)})
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=WEB
    )
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


class World:
    def __init__(self, ada: dict[str, str], org_id: str, project_id: str, other_id: str) -> None:
        self.ada, self.org_id, self.project_id, self.other_id = ada, org_id, project_id, other_id

    @property
    def base(self) -> str:
        return f"/api/v1/projects/{self.project_id}/requirements"


async def make_world(client: AsyncClient, outbox: InMemoryTransport) -> World:
    ada = await signed_in(client, outbox, "ada@example.com")
    org_id = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()["id"]
    projects = f"/api/v1/organizations/{org_id}/projects"
    project_id = (await client.post(projects, json={"name": "Food Delivery"}, headers=ada)).json()["id"]
    other_id = (await client.post(projects, json={"name": "Payments"}, headers=ada)).json()["id"]
    return World(ada, org_id, project_id, other_id)


async def member(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World, role: str
) -> dict[str, str]:
    email = f"{role}@example.com"
    headers = await signed_in(client, outbox, email)
    user = await db.scalar(select(UserRecord).where(UserRecord.email == email))
    assert user is not None
    db.add(OrganizationMemberRecord(organization_id=uuid.UUID(world.org_id), user_id=user.id, role=role))
    await db.flush()
    return headers


async def create(client: AsyncClient, world: World, **overrides: Any) -> dict[str, Any]:
    response = await client.post(world.base, json=THROUGHPUT | overrides, headers=world.ada)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body
