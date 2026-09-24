import logging
from datetime import timedelta
from email.message import EmailMessage

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from apps.api.routes.auth import EMAIL_VERIFIED, VERIFICATION_SENT
from persistence.models import UserRecord
from tests.unit.identity.fakes import FakeClock

from .conftest import email_html, email_text, token_from

pytestmark = pytest.mark.integration

REGISTER, VERIFY, RESEND = (
    "/api/v1/auth/register",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/resend-verification",
)
PASSWORD = "correct horse battery staple"


async def register(client: AsyncClient, email: str = "ada@example.com", name: str = "Ada Lovelace") -> None:
    response = await client.post(REGISTER, json={"email": email, "password": PASSWORD, "name": name})
    assert response.status_code == 202


async def user(db: AsyncSession, email: str = "ada@example.com") -> UserRecord:
    record = await db.scalar(select(UserRecord).where(UserRecord.email == email))
    assert record is not None
    await db.refresh(record)
    return record


def last(outbox: InMemoryTransport) -> EmailMessage:
    assert outbox.outbox, "no email was sent"
    return outbox.outbox[-1]


# --- the verification email ---------------------------------------------------------------------


async def test_registration_emails_a_verification_link(
    client: AsyncClient, outbox: InMemoryTransport
) -> None:
    await register(client)

    [message] = outbox.outbox
    assert message["To"] == "ada@example.com"
    assert message["Subject"] == "Verify your email address"
    assert message["Auto-Submitted"] == "auto-generated"
    assert "http://localhost:3000/verify-email?token=" in email_text(message)
    assert token_from(message) in email_html(message)


async def test_names_are_escaped_in_html_email(client: AsyncClient, outbox: InMemoryTransport) -> None:
    await register(client, name='<img src=x onerror="alert(1)">')
    html = email_html(last(outbox))
    assert "<img" not in html
    assert "&lt;img" in html


async def test_failed_registration_sends_no_email(client: AsyncClient, outbox: InMemoryTransport) -> None:
    await client.post(REGISTER, json={"email": "ada@example.com", "password": "short", "name": "Ada"})
    assert outbox.outbox == []


async def test_duplicate_registration_of_a_verified_account_warns_the_owner(
    client: AsyncClient, outbox: InMemoryTransport
) -> None:
    await register(client)
    await client.post(VERIFY, json={"token": token_from(last(outbox))})

    await register(client, email="ADA@example.com", name="Mallory")

    message = last(outbox)
    assert message["Subject"] == "You already have an ArchitectOS account"
    assert message["To"] == "ada@example.com"
    assert "Mallory" not in email_text(message)


# --- verify -------------------------------------------------------------------------------------


async def test_valid_token_verifies_once(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport
) -> None:
    await register(client)
    token = token_from(last(outbox))

    first = await client.post(VERIFY, json={"token": token})
    assert (first.status_code, first.json()) == (200, {"message": EMAIL_VERIFIED})
    assert (await user(db)).email_verified_at is not None

    again = await client.post(VERIFY, json={"token": token})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_token"


async def test_expired_token(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, clock: FakeClock
) -> None:
    await register(client)
    clock.advance(timedelta(hours=24, seconds=1))

    response = await client.post(VERIFY, json={"token": token_from(last(outbox))})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "token_expired"
    assert (await user(db)).email_verified_at is None


async def test_unknown_token(client: AsyncClient) -> None:
    response = await client.post(VERIFY, json={"token": "A" * 43})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "invalid_token")


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": "x" * 257}, {"token": 42}])
async def test_malformed_verify_requests(client: AsyncClient, body: dict[str, object]) -> None:
    response = await client.post(VERIFY, json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")


# --- resend -------------------------------------------------------------------------------------


async def test_resend_answers_identically_for_every_kind_of_email(
    client: AsyncClient, outbox: InMemoryTransport, clock: FakeClock
) -> None:
    await register(client, "unverified@example.com")
    await register(client, "verified@example.com")
    await client.post(VERIFY, json={"token": token_from(last(outbox))})
    clock.advance(timedelta(minutes=5))
    sent_before = len(outbox.outbox)

    responses = [
        await client.post(RESEND, json={"email": email})
        for email in ("unverified@example.com", "verified@example.com", "nobody@example.com", "not an email")
    ]

    assert {(r.status_code, r.json()["message"]) for r in responses} == {(202, VERIFICATION_SENT)}
    assert [m["To"] for m in outbox.outbox[sent_before:]] == ["unverified@example.com"]


async def test_resend_is_throttled_and_supersedes_the_old_link(
    client: AsyncClient, outbox: InMemoryTransport, clock: FakeClock
) -> None:
    await register(client)
    old = token_from(last(outbox))

    for _ in range(3):
        await client.post(RESEND, json={"email": "ada@example.com"})
    assert len(outbox.outbox) == 1  # still inside the cooldown

    clock.advance(timedelta(seconds=61))
    await client.post(RESEND, json={"email": "ada@example.com"})
    assert len(outbox.outbox) == 2
    new = token_from(last(outbox))

    stale = await client.post(VERIFY, json={"token": old})
    assert stale.json()["error"]["code"] == "invalid_token"
    assert (await client.post(VERIFY, json={"token": new})).status_code == 200


# --- delivery and secrecy -----------------------------------------------------------------------


async def test_tokens_never_reach_the_logs(
    client: AsyncClient, outbox: InMemoryTransport, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await register(client)
    token = token_from(last(outbox))
    await client.post(VERIFY, json={"token": token})
    await client.post(VERIFY, json={"token": token})

    assert token not in caplog.text
    assert PASSWORD not in caplog.text


async def test_mail_server_failure_does_not_fail_the_request(
    app: FastAPI, client: AsyncClient, db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenTransport:
        async def send(self, message: EmailMessage) -> None:
            raise ConnectionRefusedError("smtp down")

    app.state.email_transport = BrokenTransport()
    caplog.set_level(logging.INFO, logger="architectos.email")

    await register(client)

    assert (await user(db)).email == "ada@example.com"  # the account exists; the user can resend later
    [record] = [r for r in caplog.records if r.message == "email delivery failed"]
    assert record.email_kind == "email_verification"  # type: ignore[attr-defined]
    assert "ada@example.com" not in caplog.text
