"""Organizations over HTTP, with two tenants: Ada owns Acme, Grace owns Globex."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import OrganizationMemberRecord, OrganizationRecord, UserRecord

from .conftest import token_from

pytestmark = pytest.mark.integration

ORGS = "/api/v1/organizations"
PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


async def signed_in(
    client: AsyncClient, outbox: InMemoryTransport, email: str, *, verify: bool = True
) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email[:5]})
    if verify:
        await client.post("/api/v1/auth/verify-email", json={"token": token_from(outbox.outbox[-1])})
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=WEB
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


async def create_org(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    response = await client.post(ORGS, json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    org_id: str = response.json()["id"]
    return org_id


async def add_member(db: AsyncSession, org_id: str, email: str, role: str) -> None:
    user = await db.scalar(select(UserRecord).where(UserRecord.email == email))
    assert user is not None
    db.add(OrganizationMemberRecord(organization_id=uuid.UUID(org_id), user_id=user.id, role=role))
    await db.flush()


@pytest.fixture
async def ada(client: AsyncClient, outbox: InMemoryTransport) -> dict[str, str]:
    return await signed_in(client, outbox, "ada@example.com")


@pytest.fixture
async def grace(client: AsyncClient, outbox: InMemoryTransport) -> dict[str, str]:
    return await signed_in(client, outbox, "grace@example.com")


@pytest.fixture
async def acme(client: AsyncClient, ada: dict[str, str]) -> str:
    return await create_org(client, ada, "Acme")


@pytest.fixture
async def globex(client: AsyncClient, grace: dict[str, str]) -> str:
    return await create_org(client, grace, "Globex")


# --- create and list ----------------------------------------------------------------------------


async def test_create_makes_the_caller_owner(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str]
) -> None:
    response = await client.post(ORGS, json={"name": "  Acme   Labs "}, headers=ada)

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["role"]) == ("Acme Labs", "owner")
    member = await db.scalar(
        select(OrganizationMemberRecord).where(
            OrganizationMemberRecord.organization_id == uuid.UUID(body["id"])
        )
    )
    assert member is not None
    assert member.role == "owner"


async def test_unverified_users_cannot_create(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport
) -> None:
    headers = await signed_in(client, outbox, "new@example.com", verify=False)

    response = await client.post(ORGS, json={"name": "Acme"}, headers=headers)

    assert (response.status_code, response.json()["error"]["code"]) == (403, "email_not_verified")
    assert (await db.scalars(select(OrganizationRecord))).all() == []


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"name": "   "}, 422, "invalid_organization_name"),
        ({"name": "x" * 101}, 422, "invalid_organization_name"),
        ({}, 422, "validation_error"),
        ({"name": "Acme", "ownerId": str(uuid.uuid4())}, 422, "validation_error"),
    ],
    ids=["blank", "too-long", "missing", "unknown-field"],
)
async def test_invalid_creation(
    client: AsyncClient, ada: dict[str, str], body: dict[str, object], status: int, code: str
) -> None:
    response = await client.post(ORGS, json=body, headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


async def test_list_shows_only_my_organizations_with_my_role(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], acme: str, globex: str
) -> None:
    await add_member(db, globex, "ada@example.com", "viewer")
    zeta = await create_org(client, ada, "zeta")

    response = await client.get(ORGS, headers=ada)

    listed = [(o["id"], o["name"], o["role"]) for o in response.json()["organizations"]]
    assert listed == [(acme, "Acme", "owner"), (globex, "Globex", "viewer"), (zeta, "zeta", "owner")]


async def test_organizations_require_authentication(client: AsyncClient) -> None:
    for method, url in [("GET", ORGS), ("POST", ORGS), ("GET", f"{ORGS}/{uuid.uuid4()}")]:
        response = await client.request(method, url, json={"name": "x"} if method == "POST" else None)
        assert (response.status_code, response.json()["error"]["code"]) == (401, "unauthenticated"), url


# --- tenant isolation ---------------------------------------------------------------------------


async def test_a_non_member_cannot_see_modify_or_delete_another_tenant(
    client: AsyncClient, db: AsyncSession, ada: dict[str, str], globex: str
) -> None:
    for method, body in [("GET", None), ("PATCH", {"name": "Hijacked"}), ("DELETE", None)]:
        response = await client.request(method, f"{ORGS}/{globex}", json=body, headers=ada)
        # Same answer as a missing organization: membership cannot be probed.
        assert (response.status_code, response.json()["error"]["code"]) == (404, "organization_not_found"), (
            method
        )

    organization = await db.get(OrganizationRecord, uuid.UUID(globex))
    assert organization is not None
    await db.refresh(organization)
    assert (organization.name, organization.deleted_at) == ("Globex", None)
    assert all(o["id"] != globex for o in (await client.get(ORGS, headers=ada)).json()["organizations"])


async def test_unknown_and_malformed_ids(client: AsyncClient, ada: dict[str, str]) -> None:
    unknown = await client.get(f"{ORGS}/{uuid.uuid4()}", headers=ada)
    malformed = await client.get(f"{ORGS}/not-a-uuid", headers=ada)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "organization_not_found")
    assert (malformed.status_code, malformed.json()["error"]["code"]) == (422, "validation_error")


# --- roles --------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["owner", "admin", "member", "viewer"])
async def test_every_member_can_read(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, acme: str, role: str
) -> None:
    headers = await signed_in(client, outbox, "someone@example.com")
    await add_member(db, acme, "someone@example.com", role)

    response = await client.get(f"{ORGS}/{acme}", headers=headers)
    assert (response.status_code, response.json()["role"]) == (200, role)


@pytest.mark.parametrize(("role", "allowed"), [("admin", True), ("member", False), ("viewer", False)])
async def test_rename_permission(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, acme: str, role: str, allowed: bool
) -> None:
    headers = await signed_in(client, outbox, "someone@example.com")
    await add_member(db, acme, "someone@example.com", role)

    response = await client.patch(f"{ORGS}/{acme}", json={"name": "Renamed"}, headers=headers)

    if allowed:
        assert (response.status_code, response.json()["name"], response.json()["role"]) == (
            200,
            "Renamed",
            role,
        )
    else:
        assert (response.status_code, response.json()["error"]["code"]) == (403, "permission_denied")
        organization = await db.get(OrganizationRecord, uuid.UUID(acme))
        assert organization is not None
        await db.refresh(organization)
        assert organization.name == "Acme"


async def test_owner_renames(client: AsyncClient, ada: dict[str, str], acme: str) -> None:
    response = await client.patch(f"{ORGS}/{acme}", json={"name": " Acme  Labs "}, headers=ada)
    assert (response.status_code, response.json()["name"]) == (200, "Acme Labs")


@pytest.mark.parametrize("role", ["admin", "member", "viewer"])
async def test_only_the_owner_can_delete(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, acme: str, role: str
) -> None:
    headers = await signed_in(client, outbox, "someone@example.com")
    await add_member(db, acme, "someone@example.com", role)

    response = await client.delete(f"{ORGS}/{acme}", headers=headers)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "permission_denied")


async def test_deleted_organization_disappears_for_every_member(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    member = await signed_in(client, outbox, "someone@example.com")
    await add_member(db, acme, "someone@example.com", "member")

    assert (await client.delete(f"{ORGS}/{acme}", headers=ada)).status_code == 204

    for headers in (ada, member):
        assert (await client.get(f"{ORGS}/{acme}", headers=headers)).status_code == 404
        assert (await client.get(ORGS, headers=headers)).json()["organizations"] == []
    again = await client.delete(f"{ORGS}/{acme}", headers=ada)
    assert again.status_code == 404
    record = await db.get(OrganizationRecord, uuid.UUID(acme))
    assert record is not None
    await db.refresh(record)
    assert record.deleted_at is not None  # retained, not erased
