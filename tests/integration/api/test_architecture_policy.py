"""The project's architecture policy over HTTP and in the database (Milestone 6, phase 4)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord, ProjectRecord

from .test_project_lifecycle import signed_in
from .test_projects import add_member

pytestmark = pytest.mark.integration

STRICT = {
    "allowedTechnologies": ["PostgreSQL", "fastapi"],
    "prohibitedTechnologies": ["mongodb"],
    "allowedRegions": ["eu-west-1"],
    "requireTls": True,
    "maxComponents": 25,
}


@pytest.fixture
async def ada(client: AsyncClient, outbox: InMemoryTransport) -> dict[str, str]:
    return await signed_in(client, outbox, "ada@example.com")


@pytest.fixture
async def project(client: AsyncClient, ada: dict[str, str]) -> tuple[str, str]:
    org_id = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()["id"]
    created = await client.post(
        f"/api/v1/organizations/{org_id}/projects", json={"name": "Orders"}, headers=ada
    )
    return org_id, created.json()["id"]


async def test_a_new_project_has_the_empty_policy(
    client: AsyncClient, ada: dict[str, str], project: tuple[str, str]
) -> None:
    response = await client.get(f"/api/v1/projects/{project[1]}/architecture-policy", headers=ada)
    assert response.status_code == 200
    assert response.json()["policy"] == {
        "allowedTechnologies": [],
        "prohibitedTechnologies": [],
        "allowedRegions": [],
        "requireTls": False,
        "maxComponents": None,
    }


async def test_owners_replace_the_policy_normalized_stored_and_audited(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], project: tuple[str, str]
) -> None:
    url = f"/api/v1/projects/{project[1]}/architecture-policy"
    saved = await client.put(url, json=STRICT, headers=ada)
    assert saved.status_code == 200, saved.text
    assert saved.json()["policy"]["allowedTechnologies"] == ["fastapi", "postgresql"]
    assert (await client.get(url, headers=ada)).json() == saved.json()

    record = await db.scalar(select(ProjectRecord).where(ProjectRecord.id == project[1]))
    assert record is not None
    assert record.architecture_policy["prohibited_technologies"] == ["mongodb"]
    audit = await db.scalar(select(AuditLogRecord).where(AuditLogRecord.action == "project.policy_updated"))
    assert audit is not None
    assert audit.event_metadata == {
        "fields": [
            "allowed_regions",
            "allowed_technologies",
            "max_components",
            "prohibited_technologies",
            "require_tls",
        ]
    }

    cleared = await client.put(url, json={}, headers=ada)  # the whole policy is replaced
    assert cleared.json()["policy"]["requireTls"] is False


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (
            {"allowedTechnologies": ["redis"], "prohibitedTechnologies": ["redis"]},
            "invalid_architecture_policy",
        ),
        ({"allowedRegions": ["eu west"]}, "invalid_architecture_policy"),
        ({"maxComponents": 0}, "validation_error"),
        ({"allowedTechnologies": ["x"] * 101}, "validation_error"),
        ({"colour": "red"}, "validation_error"),
    ],
)
async def test_invalid_policies_are_refused(
    client: AsyncClient, ada: dict[str, str], project: tuple[str, str], body: dict[str, object], code: str
) -> None:
    response = await client.put(f"/api/v1/projects/{project[1]}/architecture-policy", json=body, headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (422, code)


async def test_members_read_but_cannot_change_it_and_archived_projects_are_frozen(
    client: AsyncClient,
    db: AsyncSession,
    outbox: InMemoryTransport,
    ada: dict[str, str],
    project: tuple[str, str],
) -> None:
    org_id, project_id = project
    url = f"/api/v1/projects/{project_id}/architecture-policy"
    mel = await signed_in(client, outbox, "mel@example.com")
    await add_member(db, org_id, "mel@example.com", "member")

    assert (await client.get(url, headers=mel)).status_code == 200
    denied = await client.put(url, json=STRICT, headers=mel)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")

    await client.post(f"/api/v1/projects/{project_id}/archive", headers=ada)
    frozen = await client.put(url, json=STRICT, headers=ada)
    assert (frozen.status_code, frozen.json()["error"]["code"]) == (409, "project_archived")
