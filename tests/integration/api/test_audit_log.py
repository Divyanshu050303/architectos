import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord, OrganizationMemberRecord, UserRecord

from .conftest import token_from

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


async def signed_in(client: AsyncClient, outbox: InMemoryTransport, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email[:4]})
    verification = next(m for m in reversed(outbox.outbox) if m["To"] == email)
    await client.post("/api/v1/auth/verify-email", json={"token": token_from(verification)})
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers=WEB | {"User-Agent": "AuditTest/1.0"},
    )
    return {"Authorization": f"Bearer {response.json()['accessToken']}", "User-Agent": "AuditTest/1.0"}


@pytest.fixture
async def ada(client: AsyncClient, outbox: InMemoryTransport) -> dict[str, str]:
    return await signed_in(client, outbox, "ada@example.com")


@pytest.fixture
async def acme(client: AsyncClient, ada: dict[str, str]) -> str:
    org_id: str = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()[
        "id"
    ]
    await client.patch(f"/api/v1/organizations/{org_id}", json={"name": "Acme Labs"}, headers=ada)
    for i in range(3):
        await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            json={"email": f"guest{i}@example.com", "role": "viewer"},
            headers=ada,
        )
    return org_id


def log_url(org_id: str, **params: object) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"/api/v1/organizations/{org_id}/audit-log" + (f"?{query}" if query else "")


async def test_owner_reads_the_trail_newest_first(
    client: AsyncClient, ada: dict[str, str], acme: str
) -> None:
    response = await client.get(log_url(acme), headers=ada)

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [e["action"] for e in entries] == [
        "member.invited",
        "member.invited",
        "member.invited",
        "organization.updated",
        "organization.created",
    ]
    created = entries[-1]
    assert created["actorUserId"] is not None
    assert created["resourceId"] == acme
    assert created["userAgent"] == "AuditTest/1.0"
    assert created["ipAddress"]
    assert entries[0]["metadata"]["role"] == "viewer"


async def test_pagination(client: AsyncClient, ada: dict[str, str], acme: str) -> None:
    first = (await client.get(log_url(acme, limit=2), headers=ada)).json()
    second = (await client.get(log_url(acme, limit=2, cursor=first["nextCursor"]), headers=ada)).json()
    third = (await client.get(log_url(acme, limit=2, cursor=second["nextCursor"]), headers=ada)).json()

    ids = [e["id"] for page in (first, second, third) for e in page["entries"]]
    assert len(ids) == len(set(ids)) == 5
    assert third["nextCursor"] is None


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"cursor": "garbage"}, "invalid_cursor"),
        ({"limit": 0}, "validation_error"),
        ({"limit": 101}, "validation_error"),
    ],
)
async def test_bad_paging_parameters(
    client: AsyncClient, ada: dict[str, str], acme: str, params: dict[str, object], code: str
) -> None:
    response = await client.get(log_url(acme, **params), headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (422, code)


@pytest.mark.parametrize(("role", "allowed"), [("admin", True), ("member", False), ("viewer", False)])
async def test_who_can_read_the_trail(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, acme: str, role: str, allowed: bool
) -> None:
    headers = await signed_in(client, outbox, "someone@example.com")
    user = await db.scalar(select(UserRecord).where(UserRecord.email == "someone@example.com"))
    assert user is not None
    db.add(OrganizationMemberRecord(organization_id=uuid.UUID(acme), user_id=user.id, role=role))
    await db.flush()

    response = await client.get(log_url(acme), headers=headers)
    assert response.status_code == (200 if allowed else 403)


async def test_tenants_only_see_their_own_trail(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    grace = await signed_in(client, outbox, "grace@example.com")
    globex = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)).json()["id"]

    assert (await client.get(log_url(acme), headers=grace)).status_code == 404
    globex_log = (await client.get(log_url(globex), headers=grace)).json()["entries"]
    assert [e["action"] for e in globex_log] == ["organization.created"]
    assert all(e["resourceId"] != acme for e in globex_log)


async def test_the_trail_never_contains_secrets(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    invite_tokens = [token_from(m) for m in outbox.outbox if "Join" in str(m["Subject"])]
    await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong password!!"}, headers=WEB
    )

    rows = (await db.scalars(select(AuditLogRecord))).all()
    dump = " ".join(f"{r.action} {r.resource_id} {r.event_metadata} {r.user_agent}" for r in rows)
    assert PASSWORD not in dump
    assert "wrong password" not in dump
    assert all(token not in dump for token in invite_tokens)
    assert "user.login_failed" in dump  # recorded, though not in any organization's trail
