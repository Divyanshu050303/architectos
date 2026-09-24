import uuid
from http.cookies import SimpleCookie

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import SessionRecord

pytestmark = pytest.mark.integration

LOGIN, REFRESH, LOGOUT = "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout"
ME, SESSIONS = "/api/v1/me", "/api/v1/me/sessions"
COOKIE = "__Secure-architectos_refresh"
PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


@pytest.fixture(autouse=True)
async def accounts(client: AsyncClient) -> None:
    for email in ("ada@example.com", "grace@example.com"):
        response = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email[:4]}
        )
        assert response.status_code == 202


class Device:
    """One signed-in browser: its access token and refresh cookie."""

    def __init__(self, response: Response) -> None:
        assert response.status_code == 200, response.text
        self.access = response.json()["accessToken"]
        cookie = SimpleCookie()
        cookie.load(response.headers["set-cookie"])
        self.refresh = cookie[COOKIE].value

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


async def sign_in(
    client: AsyncClient, email: str = "ada@example.com", user_agent: str = "Test/1.0"
) -> Device:
    response = await client.post(
        LOGIN, json={"email": email, "password": PASSWORD}, headers=WEB | {"User-Agent": user_agent}
    )
    return Device(response)


async def post_with_cookie(client: AsyncClient, url: str, token: str | None) -> Response:
    client.cookies.clear()
    if token is not None:
        # http.cookiejar stores cookies for dotless hosts under "<host>.local".
        client.cookies.set(COOKIE, token, domain="testserver.local", path="/api/v1/auth")
    return await client.post(url, headers=WEB)


# --- logout -------------------------------------------------------------------------------------


async def test_logout_ends_the_session_everywhere(client: AsyncClient) -> None:
    device = await sign_in(client)

    response = await post_with_cookie(client, LOGOUT, device.refresh)

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (await post_with_cookie(client, REFRESH, device.refresh)).status_code == 401
    me = await client.get(ME, headers=device.auth)
    assert (me.status_code, me.json()["error"]["code"]) == (401, "session_revoked")


async def test_logout_leaves_other_devices_signed_in(client: AsyncClient) -> None:
    laptop, phone = await sign_in(client), await sign_in(client)
    await post_with_cookie(client, LOGOUT, laptop.refresh)
    assert (await client.get(ME, headers=phone.auth)).status_code == 200


@pytest.mark.parametrize("token", [None, "garbage"], ids=["no-cookie", "garbage-cookie"])
async def test_logout_without_a_valid_cookie_still_succeeds(client: AsyncClient, token: str | None) -> None:
    response = await post_with_cookie(client, LOGOUT, token)
    assert response.status_code == 204


async def test_logging_out_twice_succeeds(client: AsyncClient) -> None:
    device = await sign_in(client)
    assert (await post_with_cookie(client, LOGOUT, device.refresh)).status_code == 204
    assert (await post_with_cookie(client, LOGOUT, device.refresh)).status_code == 204


async def test_logout_requires_the_same_origin_guard(client: AsyncClient) -> None:
    device = await sign_in(client)
    client.cookies.set(COOKIE, device.refresh, domain="testserver.local", path="/api/v1/auth")

    response = await client.post(LOGOUT, headers={"Origin": "https://evil.example"})

    assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_rejected")
    assert (await client.get(ME, headers=device.auth)).status_code == 200  # nothing was revoked


# --- listing ------------------------------------------------------------------------------------


async def test_list_my_sessions(client: AsyncClient) -> None:
    await sign_in(client, user_agent="Laptop/1.0")
    phone = await sign_in(client, user_agent="Phone/2.0")
    await sign_in(client, "grace@example.com")

    response = await client.get(SESSIONS, headers=phone.auth)

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert [s["userAgent"] for s in sessions] == ["Phone/2.0", "Laptop/1.0"]
    assert [s["current"] for s in sessions] == [True, False]
    assert set(sessions[0]) == {
        "id",
        "createdAt",
        "lastUsedAt",
        "expiresAt",
        "userAgent",
        "ipAddress",
        "current",
    }
    assert "hash" not in response.text.lower()
    assert phone.refresh.partition(".")[2] not in response.text


async def test_revoked_sessions_are_not_listed(client: AsyncClient) -> None:
    old, current = await sign_in(client), await sign_in(client)
    await post_with_cookie(client, LOGOUT, old.refresh)

    sessions = (await client.get(SESSIONS, headers=current.auth)).json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["current"] is True


async def test_listing_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(SESSIONS)
    assert (response.status_code, response.json()["error"]["code"]) == (401, "unauthenticated")


# --- revoking one session -----------------------------------------------------------------------


async def test_revoke_another_of_my_sessions(client: AsyncClient) -> None:
    laptop, phone = await sign_in(client), await sign_in(client)
    laptop_id = next(
        s["id"]
        for s in (await client.get(SESSIONS, headers=phone.auth)).json()["sessions"]
        if not s["current"]
    )

    response = await client.delete(f"{SESSIONS}/{laptop_id}", headers=phone.auth)

    assert response.status_code == 204
    assert "set-cookie" not in response.headers  # not this browser's session
    assert (await client.get(ME, headers=laptop.auth)).json()["error"]["code"] == "session_revoked"
    assert (await post_with_cookie(client, REFRESH, laptop.refresh)).status_code == 401
    assert (await client.get(ME, headers=phone.auth)).status_code == 200


async def test_revoke_my_current_session_clears_the_cookie(client: AsyncClient) -> None:
    device = await sign_in(client)
    current_id = (await client.get(SESSIONS, headers=device.auth)).json()["sessions"][0]["id"]

    response = await client.delete(f"{SESSIONS}/{current_id}", headers=device.auth)

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "Path=/api/v1/auth" in response.headers["set-cookie"]
    assert (await client.get(ME, headers=device.auth)).status_code == 401


async def test_cannot_revoke_another_users_session(client: AsyncClient, db: AsyncSession) -> None:
    ada, grace = await sign_in(client), await sign_in(client, "grace@example.com")
    graces_id = (await client.get(SESSIONS, headers=grace.auth)).json()["sessions"][0]["id"]

    response = await client.delete(f"{SESSIONS}/{graces_id}", headers=ada.auth)

    # Indistinguishable from an id that does not exist.
    assert (response.status_code, response.json()["error"]["code"]) == (404, "session_not_found")
    record = await db.get(SessionRecord, uuid.UUID(graces_id))
    assert record is not None
    await db.refresh(record)
    assert record.revoked_at is None
    assert (await client.get(ME, headers=grace.auth)).status_code == 200


async def test_revoking_unknown_or_already_revoked_sessions(client: AsyncClient) -> None:
    device, other = await sign_in(client), await sign_in(client)
    other_id = next(
        s["id"]
        for s in (await client.get(SESSIONS, headers=device.auth)).json()["sessions"]
        if not s["current"]
    )
    assert (await client.delete(f"{SESSIONS}/{other_id}", headers=device.auth)).status_code == 204

    again = await client.delete(f"{SESSIONS}/{other_id}", headers=device.auth)
    unknown = await client.delete(f"{SESSIONS}/{uuid.uuid4()}", headers=device.auth)
    malformed = await client.delete(f"{SESSIONS}/not-a-uuid", headers=device.auth)

    assert (again.status_code, unknown.status_code) == (404, 404)
    assert (malformed.status_code, malformed.json()["error"]["code"]) == (422, "validation_error")
    assert other.refresh  # the revoked device's cookie is dead
    assert (await post_with_cookie(client, REFRESH, other.refresh)).status_code == 401


async def test_revoking_requires_authentication(client: AsyncClient, db: AsyncSession) -> None:
    await sign_in(client)
    session = await db.scalar(select(SessionRecord))
    assert session is not None
    response = await client.delete(f"{SESSIONS}/{session.id}")
    assert response.status_code == 401
