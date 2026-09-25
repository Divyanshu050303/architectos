import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord, OrganizationMemberRecord, ProjectRecord, UserRecord

from .conftest import token_from

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


async def signed_in(client: AsyncClient, outbox: InMemoryTransport, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email[:4]})
    verification = next(m for m in reversed(outbox.outbox) if m["To"] == email)
    await client.post("/api/v1/auth/verify-email", json={"token": token_from(verification)})
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=WEB
    )
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


@pytest.fixture
async def ada(client: AsyncClient, outbox: InMemoryTransport) -> dict[str, str]:
    return await signed_in(client, outbox, "ada@example.com")


@pytest.fixture
async def org_and_project(client: AsyncClient, ada: dict[str, str]) -> tuple[str, str]:
    org_id = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()["id"]
    project = (
        await client.post(
            f"/api/v1/organizations/{org_id}/projects", json={"name": "Food Delivery"}, headers=ada
        )
    ).json()
    return org_id, project["id"]


async def audit_actions(db: AsyncSession, project_id: str) -> list[str]:
    rows = await db.scalars(
        select(AuditLogRecord.action)
        .where(AuditLogRecord.resource_id == project_id)
        .order_by(AuditLogRecord.created_at, AuditLogRecord.id)
    )
    return list(rows)


async def test_archive_restore_delete_journey(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], org_and_project: tuple[str, str]
) -> None:
    org_id, project_id = org_and_project
    base = f"/api/v1/projects/{project_id}"

    archived = await client.post(f"{base}/archive", headers=ada)
    again = await client.post(f"{base}/archive", headers=ada)
    assert (archived.status_code, archived.json()["status"]) == (200, "archived")
    assert again.json()["archivedAt"] == archived.json()["archivedAt"]
    assert (await client.patch(base, json={"name": "x"}, headers=ada)).json()["error"][
        "code"
    ] == "project_archived"

    restored = await client.post(f"{base}/restore", headers=ada)
    assert (restored.json()["status"], restored.json()["archivedAt"]) == ("active", None)
    assert (await client.patch(base, json={"name": "Orders"}, headers=ada)).status_code == 200

    not_archived = await client.delete(base, headers=ada)
    assert (not_archived.status_code, not_archived.json()["error"]["code"]) == (409, "project_not_archived")
    await client.post(f"{base}/archive", headers=ada)
    assert (await client.delete(base, headers=ada)).status_code == 204

    assert (await client.get(base, headers=ada)).json()["error"]["code"] == "project_not_found"
    assert (await client.delete(base, headers=ada)).status_code == 404
    listing = (await client.get(f"/api/v1/organizations/{org_id}/projects", headers=ada)).json()["projects"]
    assert listing == []
    assert await audit_actions(db, project_id) == [
        "project.created",
        "project.archived",
        "project.restored",
        "project.updated",
        "project.archived",
        "project.deleted",
    ]
    record = await db.get(ProjectRecord, uuid.UUID(project_id), populate_existing=True)
    assert record is not None
    assert record.deleted_at is not None  # retained, not purged


async def test_a_deleted_projects_slug_can_be_used_again(
    client: AsyncClient, ada: dict[str, str], org_and_project: tuple[str, str]
) -> None:
    org_id, project_id = org_and_project
    await client.post(f"/api/v1/projects/{project_id}/archive", headers=ada)
    await client.delete(f"/api/v1/projects/{project_id}", headers=ada)

    again = await client.post(
        f"/api/v1/organizations/{org_id}/projects", json={"name": "Food Delivery"}, headers=ada
    )
    assert (again.status_code, again.json()["slug"]) == (201, "food-delivery")
    assert again.json()["id"] != project_id


async def test_an_archived_project_still_blocks_its_slug(
    client: AsyncClient, ada: dict[str, str], org_and_project: tuple[str, str]
) -> None:
    org_id, project_id = org_and_project
    await client.post(f"/api/v1/projects/{project_id}/archive", headers=ada)
    duplicate = await client.post(
        f"/api/v1/organizations/{org_id}/projects", json={"name": "Food Delivery"}, headers=ada
    )
    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "project_slug_taken")


@pytest.mark.parametrize(("role", "allowed"), [("admin", True), ("member", False), ("viewer", False)])
async def test_who_may_archive_restore_and_delete(
    client: AsyncClient,
    db: AsyncSession,
    outbox: InMemoryTransport,
    ada: dict[str, str],
    org_and_project: tuple[str, str],
    role: str,
    allowed: bool,
) -> None:
    org_id, project_id = org_and_project
    headers = await signed_in(client, outbox, "someone@example.com")
    user = await db.scalar(select(UserRecord).where(UserRecord.email == "someone@example.com"))
    assert user is not None
    db.add(OrganizationMemberRecord(organization_id=uuid.UUID(org_id), user_id=user.id, role=role))
    await db.flush()
    base = f"/api/v1/projects/{project_id}"

    statuses = [
        (await client.post(f"{base}/archive", headers=headers)).status_code,
        (await client.post(f"{base}/restore", headers=headers)).status_code,
    ]
    await client.post(f"{base}/archive", headers=ada)
    statuses.append((await client.delete(base, headers=headers)).status_code)

    assert statuses == ([200, 200, 204] if allowed else [403, 403, 403])
