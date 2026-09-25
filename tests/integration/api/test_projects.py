"""Projects over HTTP. Ada owns Acme; Grace owns Globex."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
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
async def acme(client: AsyncClient, ada: dict[str, str]) -> str:
    org_id: str = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()[
        "id"
    ]
    return org_id


def projects_url(org_id: str) -> str:
    return f"/api/v1/organizations/{org_id}/projects"


async def create(
    client: AsyncClient, headers: dict[str, str], org_id: str, **body: object
) -> dict[str, object]:
    response = await client.post(projects_url(org_id), json={"name": "Food Delivery"} | body, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, object] = response.json()
    return created


async def add_member(db: AsyncSession, org_id: str, email: str, role: str) -> None:
    user = await db.scalar(select(UserRecord).where(UserRecord.email == email))
    assert user is not None
    db.add(OrganizationMemberRecord(organization_id=uuid.UUID(org_id), user_id=user.id, role=role))
    await db.flush()


# --- create -------------------------------------------------------------------------------------


async def test_create_returns_the_project_with_derived_slug_and_role(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], acme: str
) -> None:
    body = await create(
        client,
        ada,
        acme,
        name="  Food   Delivery Platform ",
        description="Orders and couriers",
        settings={"cloudProvider": "aws", "currency": "eur"},
    )

    assert (body["name"], body["slug"], body["status"], body["role"]) == (
        "Food Delivery Platform",
        "food-delivery-platform",
        "active",
        "owner",
    )
    assert body["organizationId"] == acme
    assert body["settings"] == {"cloudProvider": "aws", "currency": "EUR"}
    entry = await db.scalar(select(AuditLogRecord).where(AuditLogRecord.action == "project.created"))
    assert entry is not None
    assert entry.resource_id == body["id"]


async def test_explicit_slug_and_duplicates(client: AsyncClient, ada: dict[str, str], acme: str) -> None:
    await create(client, ada, acme, slug="orders")
    duplicate = await client.post(projects_url(acme), json={"name": "Other", "slug": "Orders"}, headers=ada)
    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "project_slug_taken")


async def test_the_same_slug_in_two_organizations_is_fine(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    grace = await signed_in(client, outbox, "grace@example.com")
    globex = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)).json()["id"]
    first = await create(client, ada, acme)
    second = await create(client, grace, globex)
    assert first["slug"] == second["slug"] == "food-delivery"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"name": "  "}, "invalid_project_name"),
        ({"name": "x" * 101}, "invalid_project_name"),
        ({"name": "日本語"}, "invalid_project_slug"),
        ({"name": "Ok", "slug": "Not Valid"}, "invalid_project_slug"),
        ({"name": "Ok", "settings": {"cloudProvider": "oracle"}}, "validation_error"),
        ({"name": "Ok", "settings": {"region": "us-east-1"}}, "validation_error"),
        ({"name": "Ok", "organizationId": str(uuid.uuid4())}, "validation_error"),
        ({"name": "Ok", "status": "archived"}, "validation_error"),
        ({}, "validation_error"),
    ],
)
async def test_invalid_creation(
    client: AsyncClient, ada: dict[str, str], acme: str, body: dict[str, object], code: str
) -> None:
    response = await client.post(projects_url(acme), json=body, headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (422, code)


# --- list ---------------------------------------------------------------------------------------


async def test_listing_paginates_sorts_and_filters(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], acme: str
) -> None:
    for name in ["Charlie", "alpha", "Bravo", "Delta", "Echo"]:
        await create(client, ada, acme, name=name)
    await db.execute(
        update(ProjectRecord)
        .where(ProjectRecord.slug == "echo")
        .values(status="archived", archived_at=ProjectRecord.created_at)
    )

    names: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"sort": "name", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(projects_url(acme), params=params, headers=ada)).json()
        names += [p["name"] for p in page["projects"]]
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert names == ["alpha", "Bravo", "Charlie", "Delta", "Echo"]

    newest_first = (await client.get(projects_url(acme), headers=ada)).json()["projects"]
    assert [p["name"] for p in newest_first] == ["Echo", "Delta", "Bravo", "alpha", "Charlie"]
    archived = (await client.get(projects_url(acme), params={"status": "archived"}, headers=ada)).json()
    assert [p["name"] for p in archived["projects"]] == ["Echo"]


async def test_search_is_literal_and_case_insensitive(
    client: AsyncClient, ada: dict[str, str], acme: str
) -> None:
    await create(client, ada, acme, name="Payments Ledger")
    await create(client, ada, acme, name="Food Delivery")
    await create(client, ada, acme, name="100% Uptime", slug="uptime")

    def names(response: object) -> list[str]:
        return [p["name"] for p in response.json()["projects"]]  # type: ignore[attr-defined]

    assert names(await client.get(projects_url(acme), params={"search": "LEDGER"}, headers=ada)) == [
        "Payments Ledger"
    ]
    assert names(await client.get(projects_url(acme), params={"search": "food-del"}, headers=ada)) == [
        "Food Delivery"
    ]
    # A lone wildcard must not match everything.
    assert names(await client.get(projects_url(acme), params={"search": "%"}, headers=ada)) == ["100% Uptime"]
    assert names(await client.get(projects_url(acme), params={"search": "_"}, headers=ada)) == []


@pytest.mark.parametrize(
    "params",
    [{"sort": "organization_id"}, {"status": "deleted"}, {"limit": 0}, {"limit": 101}, {"cursor": "garbage"}],
)
async def test_invalid_listing_parameters(
    client: AsyncClient, ada: dict[str, str], acme: str, params: dict[str, str | int]
) -> None:
    response = await client.get(projects_url(acme), params=params, headers=ada)
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"validation_error", "invalid_cursor"}


async def test_a_cursor_cannot_be_replayed_with_another_sort(
    client: AsyncClient, ada: dict[str, str], acme: str
) -> None:
    for i in range(3):
        await create(client, ada, acme, name=f"P{i}")
    cursor = (await client.get(projects_url(acme), params={"limit": 1}, headers=ada)).json()["nextCursor"]
    response = await client.get(projects_url(acme), params={"sort": "name", "cursor": cursor}, headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_cursor")


# --- get and update -----------------------------------------------------------------------------


async def test_get_and_update(client: AsyncClient, ada: dict[str, str], acme: str) -> None:
    project = await create(client, ada, acme)
    url = f"/api/v1/projects/{project['id']}"

    assert (await client.get(url, headers=ada)).json()["name"] == "Food Delivery"
    updated = await client.patch(
        url, json={"name": "Orders", "description": "New", "settings": {"cloudProvider": "gcp"}}, headers=ada
    )

    assert updated.status_code == 200
    body = updated.json()
    assert (body["name"], body["description"], body["slug"]) == ("Orders", "New", "food-delivery")
    assert body["settings"] == {"cloudProvider": "gcp", "currency": "USD"}


@pytest.mark.parametrize(
    "body",
    [
        {"organizationId": str(uuid.uuid4())},
        {"slug": "moved"},
        {"createdByUserId": str(uuid.uuid4())},
        {"status": "archived"},
    ],
)
async def test_ownership_slug_and_lifecycle_cannot_be_patched(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], acme: str, body: dict[str, object]
) -> None:
    project = await create(client, ada, acme)
    response = await client.patch(f"/api/v1/projects/{project['id']}", json=body, headers=ada)

    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")
    record = await db.get(ProjectRecord, uuid.UUID(str(project["id"])), populate_existing=True)
    assert record is not None
    assert (str(record.organization_id), record.slug, record.status) == (acme, "food-delivery", "active")


async def test_empty_update(client: AsyncClient, ada: dict[str, str], acme: str) -> None:
    project = await create(client, ada, acme)
    response = await client.patch(f"/api/v1/projects/{project['id']}", json={}, headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "nothing_to_update")


async def test_archived_projects_are_read_only(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], acme: str
) -> None:
    project = await create(client, ada, acme)
    await db.execute(
        update(ProjectRecord)
        .where(ProjectRecord.id == uuid.UUID(str(project["id"])))
        .values(status="archived", archived_at=ProjectRecord.created_at)
    )
    url = f"/api/v1/projects/{project['id']}"

    assert (await client.get(url, headers=ada)).json()["status"] == "archived"
    response = await client.patch(url, json={"name": "Orders"}, headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "project_archived")


# --- roles and tenants --------------------------------------------------------------------------


@pytest.mark.parametrize(("role", "can_write"), [("admin", True), ("member", True), ("viewer", False)])
async def test_role_permissions(
    client: AsyncClient,
    db: AsyncSession,
    outbox: InMemoryTransport,
    ada: dict[str, str],
    acme: str,
    role: str,
    can_write: bool,
) -> None:
    project = await create(client, ada, acme)
    headers = await signed_in(client, outbox, "someone@example.com")
    await add_member(db, acme, "someone@example.com", role)

    read = await client.get(f"/api/v1/projects/{project['id']}", headers=headers)
    listed = await client.get(projects_url(acme), headers=headers)
    created = await client.post(projects_url(acme), json={"name": "Mine"}, headers=headers)
    patched = await client.patch(
        f"/api/v1/projects/{project['id']}", json={"name": "Changed"}, headers=headers
    )

    assert (read.status_code, read.json()["role"], listed.status_code) == (200, role, 200)
    expected = (201, 200) if can_write else (403, 403)
    assert (created.status_code, patched.status_code) == expected


async def test_other_tenants_get_404_for_everything(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    project = await create(client, ada, acme)
    grace = await signed_in(client, outbox, "grace@example.com")
    url = f"/api/v1/projects/{project['id']}"

    get = await client.get(url, headers=grace)
    patch = await client.patch(url, json={"name": "Hijacked"}, headers=grace)
    listing = await client.get(projects_url(acme), headers=grace)
    creating = await client.post(projects_url(acme), json={"name": "Planted"}, headers=grace)

    assert (get.status_code, get.json()["error"]["code"]) == (404, "project_not_found")
    assert (patch.status_code, patch.json()["error"]["code"]) == (404, "project_not_found")
    assert (listing.status_code, creating.status_code) == (404, 404)
    assert (await client.get(url, headers=ada)).json()["name"] == "Food Delivery"


async def test_unknown_and_malformed_project_ids(client: AsyncClient, ada: dict[str, str]) -> None:
    unknown = await client.get(f"/api/v1/projects/{uuid.uuid4()}", headers=ada)
    malformed = await client.get("/api/v1/projects/not-a-uuid", headers=ada)
    assert (unknown.status_code, malformed.status_code) == (404, 422)
