"""Members over HTTP. Acme: Ada (owner), Adam (admin), Mel (member), Vic (viewer). Globex: Grace."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import OrganizationMemberRecord, OrganizationRecord, UserRecord

from .conftest import token_from

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}
PEOPLE = {"ada": "owner", "adam": "admin", "mel": "member", "vic": "viewer"}


class Org:
    def __init__(self, org_id: str, headers: dict[str, dict[str, str]], members: dict[str, str]) -> None:
        self.id, self.headers, self.members = org_id, headers, members

    def url(self, name: str | None = None) -> str:
        base = f"/api/v1/organizations/{self.id}/members"
        return f"{base}/{self.members[name]}" if name else base


async def signed_in(client: AsyncClient, outbox: InMemoryTransport, name: str) -> dict[str, str]:
    email = f"{name}@example.com"
    await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": name.title()}
    )
    await client.post("/api/v1/auth/verify-email", json={"token": token_from(outbox.outbox[-1])})
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=WEB
    )
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


@pytest.fixture
async def acme(client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport) -> Org:
    headers = {name: await signed_in(client, outbox, name) for name in PEOPLE}
    org_id = (
        await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=headers["ada"])
    ).json()["id"]
    for name, role in PEOPLE.items():
        if role != "owner":
            user = await db.scalar(select(UserRecord).where(UserRecord.email == f"{name}@example.com"))
            assert user is not None
            db.add(OrganizationMemberRecord(organization_id=uuid.UUID(org_id), user_id=user.id, role=role))
    await db.flush()
    listing = (await client.get(f"/api/v1/organizations/{org_id}/members", headers=headers["ada"])).json()[
        "members"
    ]
    members = {m["email"].split("@")[0]: m["id"] for m in listing}
    return Org(org_id, headers, members)


async def role_of(db: AsyncSession, member_id: str) -> str | None:
    record = await db.get(OrganizationMemberRecord, uuid.UUID(member_id), populate_existing=True)
    return record.role if record else None


async def change(client: AsyncClient, org: Org, actor: str, target: str, role: str) -> tuple[int, str | None]:
    response = await client.patch(org.url(target), json={"role": role}, headers=org.headers[actor])
    return response.status_code, response.json().get("error", {}).get(
        "code"
    ) if response.status_code >= 400 else None


async def remove(client: AsyncClient, org: Org, actor: str, target: str) -> tuple[int, str | None]:
    response = await client.delete(org.url(target), headers=org.headers[actor])
    return response.status_code, response.json()["error"]["code"] if response.status_code >= 400 else None


# --- listing ------------------------------------------------------------------------------------


@pytest.mark.parametrize("viewer", list(PEOPLE))
async def test_every_member_can_list_members(client: AsyncClient, acme: Org, viewer: str) -> None:
    response = await client.get(acme.url(), headers=acme.headers[viewer])
    members = response.json()["members"]
    assert [m["role"] for m in members] == ["owner", "admin", "member", "viewer"]
    assert set(members[0]) == {"id", "userId", "name", "email", "avatarUrl", "role", "joinedAt"}


# --- role changes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "target", "role", "outcome"),
    [
        ("ada", "mel", "admin", (200, None)),
        ("ada", "adam", "owner", (200, None)),
        ("adam", "mel", "viewer", (200, None)),
        ("adam", "vic", "member", (200, None)),
        ("adam", "mel", "admin", (403, "role_not_manageable")),
        ("adam", "vic", "owner", (403, "role_not_manageable")),
        ("adam", "ada", "viewer", (403, "role_not_manageable")),
        ("mel", "vic", "member", (403, "permission_denied")),
        ("vic", "mel", "viewer", (403, "permission_denied")),
        ("ada", "ada", "admin", (403, "cannot_change_own_role")),
        ("adam", "adam", "owner", (403, "cannot_change_own_role")),
    ],
)
async def test_role_change_rules(
    client: AsyncClient,
    db: AsyncSession,
    acme: Org,
    actor: str,
    target: str,
    role: str,
    outcome: tuple[int, str | None],
) -> None:
    before = await role_of(db, acme.members[target])

    assert await change(client, acme, actor, target, role) == outcome

    assert await role_of(db, acme.members[target]) == (role if outcome[0] == 200 else before)


async def test_owner_is_never_left_without_an_owner(client: AsyncClient, db: AsyncSession, acme: Org) -> None:
    # Ada makes Adam owner, then Adam demotes Ada: fine, Adam remains. Ada can no longer act.
    assert await change(client, acme, "ada", "adam", "owner") == (200, None)
    assert await change(client, acme, "adam", "ada", "admin") == (200, None)
    assert await change(client, acme, "ada", "adam", "admin") == (403, "role_not_manageable")
    assert await role_of(db, acme.members["adam"]) == "owner"


@pytest.mark.parametrize("body", [{"role": "superuser"}, {}, {"role": "admin", "userId": str(uuid.uuid4())}])
async def test_invalid_role_change_requests(client: AsyncClient, acme: Org, body: dict[str, object]) -> None:
    response = await client.patch(acme.url("mel"), json=body, headers=acme.headers["ada"])
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")


# --- removal and leaving ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "target", "outcome"),
    [
        ("ada", "adam", (204, None)),
        ("adam", "mel", (204, None)),
        ("adam", "vic", (204, None)),
        ("adam", "ada", (403, "role_not_manageable")),
        ("mel", "vic", (403, "permission_denied")),
        ("mel", "adam", (403, "permission_denied")),
        ("vic", "mel", (403, "permission_denied")),
    ],
)
async def test_removal_rules(
    client: AsyncClient, db: AsyncSession, acme: Org, actor: str, target: str, outcome: tuple[int, str | None]
) -> None:
    assert await remove(client, acme, actor, target) == outcome
    assert (await role_of(db, acme.members[target]) is None) is (outcome[0] == 204)


@pytest.mark.parametrize("leaver", ["adam", "mel", "vic"])
async def test_anyone_but_the_last_owner_can_leave(client: AsyncClient, acme: Org, leaver: str) -> None:
    assert await remove(client, acme, leaver, leaver) == (204, None)
    after = await client.get(f"/api/v1/organizations/{acme.id}", headers=acme.headers[leaver])
    assert after.status_code == 404


async def test_the_last_owner_cannot_leave(client: AsyncClient, acme: Org) -> None:
    assert await remove(client, acme, "ada", "ada") == (409, "last_owner")


# --- tenant isolation ---------------------------------------------------------------------------


async def test_members_of_another_organization_are_out_of_reach(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, acme: Org
) -> None:
    grace = await signed_in(client, outbox, "grace")
    globex = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)).json()["id"]
    grace_member = (await client.get(f"/api/v1/organizations/{globex}/members", headers=grace)).json()[
        "members"
    ][0]["id"]

    # Ada (owner of Acme) with Grace's membership id, through Acme's URL and through Globex's.
    via_acme = await client.delete(
        f"/api/v1/organizations/{acme.id}/members/{grace_member}", headers=acme.headers["ada"]
    )
    via_globex = await client.patch(
        f"/api/v1/organizations/{globex}/members/{grace_member}",
        json={"role": "viewer"},
        headers=acme.headers["ada"],
    )
    listing = await client.get(f"/api/v1/organizations/{globex}/members", headers=acme.headers["ada"])

    assert (via_acme.status_code, via_acme.json()["error"]["code"]) == (404, "member_not_found")
    assert (via_globex.status_code, via_globex.json()["error"]["code"]) == (404, "organization_not_found")
    assert listing.status_code == 404
    assert await role_of(db, grace_member) == "owner"


async def test_unknown_member(client: AsyncClient, acme: Org) -> None:
    response = await client.delete(f"{acme.url()}/{uuid.uuid4()}", headers=acme.headers["ada"])
    assert (response.status_code, response.json()["error"]["code"]) == (404, "member_not_found")


# --- account deletion ---------------------------------------------------------------------------


async def test_sole_owner_must_transfer_before_deleting_their_account(
    client: AsyncClient, db: AsyncSession, acme: Org
) -> None:
    blocked = await client.request(
        "DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=acme.headers["ada"]
    )
    assert (blocked.status_code, blocked.json()["error"]["code"]) == (409, "sole_owner_of_organization")
    assert blocked.json()["error"]["details"] == {"organizationIds": [acme.id]}

    assert await change(client, acme, "ada", "adam", "owner") == (200, None)
    deleted = await client.request(
        "DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=acme.headers["ada"]
    )
    assert deleted.status_code == 204
    names = [
        m["email"] for m in (await client.get(acme.url(), headers=acme.headers["adam"])).json()["members"]
    ]
    assert "ada@example.com" not in names


async def test_deleting_an_account_removes_organizations_it_was_alone_in(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport
) -> None:
    solo = await signed_in(client, outbox, "solo")
    org_id = (await client.post("/api/v1/organizations", json={"name": "Solo"}, headers=solo)).json()["id"]

    assert (
        await client.request("DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=solo)
    ).status_code == 204

    organization = await db.get(OrganizationRecord, uuid.UUID(org_id), populate_existing=True)
    assert organization is not None
    assert organization.deleted_at is not None
