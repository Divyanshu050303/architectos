import logging
from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from httpx import AsyncClient, Response

from apps.api.email.transport import InMemoryTransport
from apps.api.routes.auth import RESET_COMPLETE, RESET_REQUESTED
from tests.unit.identity.fakes import FakeClock

from .conftest import email_text, token_from

pytestmark = pytest.mark.integration

FORGOT, RESET = "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password"
LOGIN, REFRESH, ME, CHANGE = "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/me", "/api/v1/me/password"
COOKIE = "__Secure-architectos_refresh"
OLD, NEW = "correct horse battery staple", "tangerine submarine orchestra"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


@pytest.fixture(autouse=True)
async def ada(client: AsyncClient, outbox: InMemoryTransport) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "ada@example.com", "password": OLD, "name": "Ada"}
    )
    outbox.outbox.clear()


class Device:
    def __init__(self, response: Response) -> None:
        assert response.status_code == 200, response.text
        self.access = response.json()["accessToken"]
        cookie = SimpleCookie()
        cookie.load(response.headers["set-cookie"])
        self.refresh = cookie[COOKIE].value
        self.auth = {"Authorization": f"Bearer {self.access}"}


async def login(client: AsyncClient, password: str = OLD) -> Response:
    return await client.post(LOGIN, json={"email": "ada@example.com", "password": password}, headers=WEB)


async def refresh(client: AsyncClient, device: Device) -> Response:
    client.cookies.clear()
    client.cookies.set(COOKIE, device.refresh, domain="testserver.local", path="/api/v1/auth")
    return await client.post(REFRESH, headers=WEB)


async def request_link(client: AsyncClient, outbox: InMemoryTransport) -> str:
    response = await client.post(FORGOT, json={"email": "ada@example.com"})
    assert response.status_code == 202
    return token_from(outbox.outbox[-1])


# --- forgot password ----------------------------------------------------------------------------


async def test_forgot_password_answers_identically_whether_or_not_the_account_exists(
    client: AsyncClient, outbox: InMemoryTransport
) -> None:
    responses = [
        await client.post(FORGOT, json={"email": email})
        for email in ("ada@example.com", "nobody@example.com", "not an email")
    ]
    assert {(r.status_code, r.json()["message"]) for r in responses} == {(202, RESET_REQUESTED)}
    assert [m["To"] for m in outbox.outbox] == ["ada@example.com"]


async def test_reset_email_links_to_the_web_app(client: AsyncClient, outbox: InMemoryTransport) -> None:
    await client.post(FORGOT, json={"email": "ada@example.com"})
    [message] = outbox.outbox
    assert message["Subject"] == "Reset your password"
    assert "http://localhost:3000/reset-password?token=" in email_text(message)
    assert "30 minutes" in email_text(message)


# --- reset --------------------------------------------------------------------------------------


async def test_reset_flow_end_to_end(client: AsyncClient, outbox: InMemoryTransport) -> None:
    laptop, phone = Device(await login(client)), Device(await login(client))
    token = await request_link(client, outbox)

    response = await client.post(RESET, json={"token": token, "password": NEW})

    assert (response.status_code, response.json()) == (200, {"message": RESET_COMPLETE})
    # Every existing session is signed out: access and refresh tokens alike.
    for device in (laptop, phone):
        assert (await client.get(ME, headers=device.auth)).json()["error"]["code"] == "session_revoked"
        assert (await refresh(client, device)).status_code == 401
    assert (await login(client, OLD)).json()["error"]["code"] == "invalid_credentials"
    me = await client.get(ME, headers=Device(await login(client, NEW)).auth)
    assert me.json()["emailVerified"] is True
    assert outbox.outbox[-1]["Subject"] == "Your password was changed"


async def test_reset_token_cannot_be_reused(client: AsyncClient, outbox: InMemoryTransport) -> None:
    token = await request_link(client, outbox)
    assert (await client.post(RESET, json={"token": token, "password": NEW})).status_code == 200

    again = await client.post(RESET, json={"token": token, "password": "another fine password"})
    assert (again.status_code, again.json()["error"]["code"]) == (400, "invalid_token")
    assert (await login(client, NEW)).status_code == 200


async def test_expired_reset_token(client: AsyncClient, outbox: InMemoryTransport, clock: FakeClock) -> None:
    token = await request_link(client, outbox)
    clock.advance(timedelta(minutes=31))

    response = await client.post(RESET, json={"token": token, "password": NEW})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "token_expired")
    assert (await login(client, OLD)).status_code == 200


async def test_weak_password_keeps_the_link_usable(client: AsyncClient, outbox: InMemoryTransport) -> None:
    token = await request_link(client, outbox)

    weak = await client.post(RESET, json={"token": token, "password": "password1234"})
    assert (weak.status_code, weak.json()["error"]["code"]) == (422, "weak_password")
    assert (await client.post(RESET, json={"token": token, "password": NEW})).status_code == 200


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"token": "A" * 43, "password": NEW}, 400, "invalid_token"),
        ({"token": "", "password": NEW}, 422, "validation_error"),
        ({"password": NEW}, 422, "validation_error"),
        ({"token": "x", "password": NEW, "email": "ada@example.com"}, 422, "validation_error"),
    ],
    ids=["unknown-token", "empty-token", "missing-token", "unknown-field"],
)
async def test_bad_reset_requests(
    client: AsyncClient, body: dict[str, object], status: int, code: str
) -> None:
    response = await client.post(RESET, json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


async def test_reset_secrets_stay_out_of_logs(
    client: AsyncClient, outbox: InMemoryTransport, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token = await request_link(client, outbox)
    await client.post(RESET, json={"token": token, "password": NEW})
    assert token not in caplog.text
    assert NEW not in caplog.text


# --- change -------------------------------------------------------------------------------------


async def test_change_password_keeps_this_device_and_signs_out_others(
    client: AsyncClient, outbox: InMemoryTransport
) -> None:
    here, elsewhere = Device(await login(client)), Device(await login(client))

    response = await client.patch(
        CHANGE, json={"currentPassword": OLD, "newPassword": NEW}, headers=here.auth
    )

    assert response.status_code == 204
    assert (await client.get(ME, headers=here.auth)).status_code == 200
    assert (await refresh(client, here)).status_code == 200
    assert (await client.get(ME, headers=elsewhere.auth)).json()["error"]["code"] == "session_revoked"
    assert (await refresh(client, elsewhere)).status_code == 401
    assert (await login(client, OLD)).status_code == 401
    assert (await login(client, NEW)).status_code == 200
    assert outbox.outbox[-1]["Subject"] == "Your password was changed"


async def test_change_password_with_the_wrong_current_password(client: AsyncClient) -> None:
    device = Device(await login(client))
    response = await client.patch(
        CHANGE, json={"currentPassword": "not my password", "newPassword": NEW}, headers=device.auth
    )
    assert (response.status_code, response.json()["error"]["code"]) == (400, "incorrect_password")
    assert (await login(client, OLD)).status_code == 200


async def test_change_password_enforces_the_policy(client: AsyncClient) -> None:
    device = Device(await login(client))
    response = await client.patch(
        CHANGE, json={"currentPassword": OLD, "newPassword": "short"}, headers=device.auth
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "weak_password")


async def test_change_password_requires_authentication(client: AsyncClient) -> None:
    response = await client.patch(CHANGE, json={"currentPassword": OLD, "newPassword": NEW})
    assert (response.status_code, response.json()["error"]["code"]) == (401, "unauthenticated")


async def test_change_password_errors_never_echo_passwords(client: AsyncClient) -> None:
    device = Device(await login(client))
    response = await client.patch(
        CHANGE, json={"currentPassword": OLD, "newPassword": "x" * 2000}, headers=device.auth
    )
    assert response.status_code == 422
    assert OLD not in response.text
