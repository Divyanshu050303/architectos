"""Invitations over HTTP: Ada owns Acme and invites people; emails are captured in memory."""

import logging
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import InvitationRecord, OrganizationMemberRecord, UserRecord
from tests.unit.identity.fakes import FakeClock

from .conftest import email_html, email_text, token_from

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


async def signed_in(
    client: AsyncClient, outbox: InMemoryTransport, email: str, *, verify: bool = True
) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email[:4]})
    if verify:
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


def url(org_id: str, invitation_id: str | None = None) -> str:
    base = f"/api/v1/organizations/{org_id}/invitations"
    return f"{base}/{invitation_id}" if invitation_id else base


def accept_url(token: str) -> str:
    return f"/api/v1/invitations/{token}/accept"


async def invite(
    client: AsyncClient, headers: dict[str, str], org_id: str, email: str, role: str = "member"
) -> str:
    response = await client.post(url(org_id), json={"email": email, "role": role}, headers=headers)
    assert response.status_code == 201, response.text
    invitation_id: str = response.json()["id"]
    return invitation_id


def invitation_token(outbox: InMemoryTransport, email: str) -> str:
    message = next(m for m in reversed(outbox.outbox) if m["To"] == email and "Join" in str(m["Subject"]))
    return token_from(message)


# --- invite -------------------------------------------------------------------------------------


async def test_invite_emails_a_link_and_never_returns_the_token(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    response = await client.post(url(acme), json={"email": " Bob@Example.com ", "role": "admin"}, headers=ada)

    assert response.status_code == 201
    body = response.json()
    assert (body["email"], body["role"]) == ("bob@example.com", "admin")
    assert "token" not in str(body).lower()
    message = outbox.outbox[-1]
    assert message["Subject"] == "Join Acme on ArchitectOS"
    assert "http://localhost:3000/accept-invitation?token=" in email_text(message)
    assert token_from(message) not in response.text


async def test_organization_names_are_escaped_in_the_email(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str]
) -> None:
    org = (await client.post("/api/v1/organizations", json={"name": "<b>Evil</b> Co"}, headers=ada)).json()[
        "id"
    ]
    await invite(client, ada, org, "bob@example.com")
    assert "<b>Evil</b>" not in email_html(outbox.outbox[-1])


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"email": "bob@example.com", "role": "owner"}, 422, "owner_invitation_not_allowed"),
        ({"email": "ada@example.com", "role": "member"}, 409, "already_member"),
        ({"email": "not-an-email", "role": "member"}, 422, "invalid_email"),
        ({"email": "bob@example.com", "role": "god"}, 422, "validation_error"),
        ({"email": "bob@example.com"}, 422, "validation_error"),
    ],
    ids=["owner-role", "already-member", "bad-email", "bad-role", "missing-role"],
)
async def test_invalid_invitations(
    client: AsyncClient, ada: dict[str, str], acme: str, body: dict[str, object], status: int, code: str
) -> None:
    response = await client.post(url(acme), json=body, headers=ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


@pytest.mark.parametrize(
    ("role", "invitee_role", "outcome"),
    [
        ("admin", "member", 201),
        ("admin", "admin", 403),
        ("member", "viewer", 403),
        ("viewer", "viewer", 403),
    ],
)
async def test_who_may_invite(
    client: AsyncClient,
    db: AsyncSession,
    outbox: InMemoryTransport,
    acme: str,
    role: str,
    invitee_role: str,
    outcome: int,
) -> None:
    headers = await signed_in(client, outbox, "someone@example.com")
    user = await db.scalar(select(UserRecord).where(UserRecord.email == "someone@example.com"))
    assert user is not None
    db.add(OrganizationMemberRecord(organization_id=uuid.UUID(acme), user_id=user.id, role=role))
    await db.flush()

    response = await client.post(
        url(acme), json={"email": "new@example.com", "role": invitee_role}, headers=headers
    )
    assert response.status_code == outcome


async def test_reinviting_replaces_the_pending_invitation(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    await invite(client, ada, acme, "bob@example.com", "viewer")
    old_token = invitation_token(outbox, "bob@example.com")
    await invite(client, ada, acme, "bob@example.com", "member")

    pending = (await client.get(url(acme), headers=ada)).json()["invitations"]
    assert [(i["email"], i["role"]) for i in pending] == [("bob@example.com", "member")]

    bob = await signed_in(client, outbox, "bob@example.com")
    stale = await client.post(accept_url(old_token), headers=bob)
    assert (stale.status_code, stale.json()["error"]["code"]) == (404, "invalid_invitation")


# --- accept -------------------------------------------------------------------------------------


async def test_accepting_joins_with_the_invited_role(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    await invite(client, ada, acme, "bob@example.com", "viewer")
    token = invitation_token(outbox, "bob@example.com")
    bob = await signed_in(client, outbox, "bob@example.com")

    response = await client.post(accept_url(token), headers=bob)

    assert response.status_code == 200
    assert (response.json()["id"], response.json()["role"]) == (acme, "viewer")
    assert (await client.get(f"/api/v1/organizations/{acme}", headers=bob)).status_code == 200
    assert (await client.get(url(acme), headers=ada)).json()["invitations"] == []
    again = await client.post(accept_url(token), headers=bob)
    assert (again.status_code, again.json()["error"]["code"]) == (404, "invalid_invitation")


async def test_only_the_invited_address_can_accept(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    await invite(client, ada, acme, "bob@example.com")
    token = invitation_token(outbox, "bob@example.com")
    mallory = await signed_in(client, outbox, "mallory@example.com")

    response = await client.post(accept_url(token), headers=mallory)

    assert (response.status_code, response.json()["error"]["code"]) == (403, "invitation_email_mismatch")
    assert (await client.get(f"/api/v1/organizations/{acme}", headers=mallory)).status_code == 404
    invitation = await db.scalar(select(InvitationRecord))
    assert invitation is not None
    assert invitation.accepted_at is None  # still usable by Bob


async def test_unverified_invitee_must_verify_first(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    await invite(client, ada, acme, "bob@example.com")
    token = invitation_token(outbox, "bob@example.com")
    bob = await signed_in(client, outbox, "bob@example.com", verify=False)

    response = await client.post(accept_url(token), headers=bob)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "email_not_verified")


async def test_expired_invitation(
    client: AsyncClient, outbox: InMemoryTransport, clock: FakeClock, ada: dict[str, str], acme: str
) -> None:
    await invite(client, ada, acme, "bob@example.com")
    token = invitation_token(outbox, "bob@example.com")
    clock.advance(timedelta(days=7, seconds=1))
    bob = await signed_in(client, outbox, "bob@example.com")  # signs in after the jump, so his token is fresh

    response = await client.post(accept_url(token), headers=bob)
    assert (response.status_code, response.json()["error"]["code"]) == (410, "invitation_expired")


async def test_revoked_invitation(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    invitation_id = await invite(client, ada, acme, "bob@example.com")
    token = invitation_token(outbox, "bob@example.com")
    assert (await client.delete(url(acme, invitation_id), headers=ada)).status_code == 204
    bob = await signed_in(client, outbox, "bob@example.com")

    response = await client.post(accept_url(token), headers=bob)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "invalid_invitation")
    again = await client.delete(url(acme, invitation_id), headers=ada)
    assert (again.status_code, again.json()["error"]["code"]) == (404, "invitation_not_found")


async def test_invitation_to_a_deleted_organization(
    client: AsyncClient, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    await invite(client, ada, acme, "bob@example.com")
    token = invitation_token(outbox, "bob@example.com")
    await client.delete(f"/api/v1/organizations/{acme}", headers=ada)
    bob = await signed_in(client, outbox, "bob@example.com")

    response = await client.post(accept_url(token), headers=bob)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "invalid_invitation")


async def test_accepting_requires_sign_in_and_a_real_token(client: AsyncClient, ada: dict[str, str]) -> None:
    anonymous = await client.post(accept_url("A" * 43))
    unknown = await client.post(accept_url("A" * 43), headers=ada)
    oversized = await client.post(accept_url("A" * 300), headers=ada)
    assert (anonymous.status_code, anonymous.json()["error"]["code"]) == (401, "unauthenticated")
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "invalid_invitation")
    assert oversized.status_code == 422


# --- tenant isolation and secrecy ---------------------------------------------------------------


async def test_other_tenants_cannot_see_or_revoke_invitations(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, ada: dict[str, str], acme: str
) -> None:
    invitation_id = await invite(client, ada, acme, "bob@example.com")
    grace = await signed_in(client, outbox, "grace@example.com")
    globex = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)).json()["id"]

    listing = await client.get(url(acme), headers=grace)
    via_acme = await client.delete(url(acme, invitation_id), headers=grace)
    via_globex = await client.delete(url(globex, invitation_id), headers=grace)

    assert listing.status_code == 404
    assert via_acme.status_code == 404
    assert (via_globex.status_code, via_globex.json()["error"]["code"]) == (404, "invitation_not_found")
    invitation = await db.get(InvitationRecord, uuid.UUID(invitation_id))
    assert invitation is not None
    assert invitation.revoked_at is None


async def test_invitation_tokens_stay_out_of_the_applications_logs(
    client: AsyncClient,
    outbox: InMemoryTransport,
    ada: dict[str, str],
    acme: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    await invite(client, ada, acme, "bob@example.com")
    token = invitation_token(outbox, "bob@example.com")
    bob = await signed_in(client, outbox, "bob@example.com")
    await client.post(accept_url(token), headers=bob)

    app_records = [r for r in caplog.records if r.name.startswith("architectos")]
    assert all(token not in r.getMessage() for r in app_records)
    # The test HTTP client itself logs the full URL, token included: exactly why a token in a URL
    # path is risky with any client, proxy or access log that records URLs. See the docs.
    assert any(token in r.getMessage() for r in caplog.records if r.name == "httpx")
