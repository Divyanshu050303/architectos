from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import SessionRecord, UserRecord
from tests.unit.identity.fakes import FakeClock

from .conftest import token_from

pytestmark = pytest.mark.integration

LOGIN, REFRESH, ME = "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/me"
COOKIE = "__Secure-architectos_refresh"
PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


@pytest.fixture(autouse=True)
async def ada(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "ada@example.com", "password": PASSWORD, "name": "Ada Lovelace"},
    )
    assert response.status_code == 202


async def login(client: AsyncClient, email: str = "ada@example.com", password: str = PASSWORD) -> Response:
    return await client.post(LOGIN, json={"email": email, "password": password}, headers=WEB)


def refresh_cookie(response: Response) -> str:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie[COOKIE].value


async def refresh_with(client: AsyncClient, token: str) -> Response:
    """Refresh presenting exactly ``token``, as a browser holding that cookie would."""
    client.cookies.clear()
    # http.cookiejar stores cookies for dotless hosts under "<host>.local".
    client.cookies.set(COOKIE, token, domain="testserver.local", path="/api/v1/auth")
    return await client.post(REFRESH, headers=WEB)


def bearer(response: Response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


# --- login --------------------------------------------------------------------------------------


async def test_login_returns_an_access_token_and_a_hardened_cookie(client: AsyncClient) -> None:
    response = await login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["tokenType"] == "Bearer"
    assert body["expiresIn"] == 900
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["emailVerified"] is False
    assert response.headers["cache-control"] == "no-store"

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/api/v1/auth", "Max-Age=2592000"):
        assert attribute in set_cookie
    # The refresh token lives only in the cookie.
    assert refresh_cookie(response) not in response.text


async def test_login_body_never_exposes_security_fields(client: AsyncClient) -> None:
    body = (await login(client)).json()
    assert set(body["user"]) == {"id", "email", "name", "avatarUrl", "emailVerified", "createdAt"}
    for secret_field in ("passwordHash", "password_hash", "refreshToken", "status"):
        assert secret_field not in str(body)


async def test_wrong_password_and_unknown_email_are_indistinguishable(client: AsyncClient) -> None:
    wrong = await login(client, password="wrong password!!")
    unknown = await login(client, email="nobody@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert "set-cookie" not in wrong.headers


async def test_disabled_account(client: AsyncClient, db: AsyncSession) -> None:
    user = await db.scalar(select(UserRecord).where(UserRecord.email == "ada@example.com"))
    assert user is not None
    user.status = "disabled"
    await db.flush()

    response = await login(client)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "account_disabled")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://localhost:3000"},
        {"X-Requested-With": "XMLHttpRequest"},
        {"X-Requested-With": "architectos", "Origin": "https://evil.example"},
    ],
    ids=["no-headers", "no-csrf-header", "wrong-csrf-value", "foreign-origin"],
)
async def test_login_and_refresh_reject_cross_site_requests(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    for url, json in ((LOGIN, {"email": "ada@example.com", "password": PASSWORD}), (REFRESH, None)):
        response = await client.post(url, json=json, headers=headers)
        assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_rejected"), url


async def test_requests_without_origin_are_allowed_with_the_header(client: AsyncClient) -> None:
    # Non-browser clients send no Origin; the custom header is still required.
    response = await client.post(
        LOGIN,
        json={"email": "ada@example.com", "password": PASSWORD},
        headers={"X-Requested-With": "architectos"},
    )
    assert response.status_code == 200


async def test_session_records_client_metadata(client: AsyncClient, db: AsyncSession) -> None:
    await client.post(
        LOGIN,
        json={"email": "ada@example.com", "password": PASSWORD},
        headers=WEB | {"User-Agent": "ArchitectOS-Test/1.0"},
    )
    session = await db.scalar(select(SessionRecord))
    assert session is not None
    assert session.user_agent == "ArchitectOS-Test/1.0"
    assert session.ip_address is not None


# --- /me and access tokens ----------------------------------------------------------------------


async def test_me_with_a_valid_access_token(client: AsyncClient) -> None:
    response = await client.get(ME, headers=bearer(await login(client)))
    assert response.status_code == 200
    assert response.json()["name"] == "Ada Lovelace"
    assert "passwordHash" not in response.text


async def test_me_without_a_token(client: AsyncClient) -> None:
    response = await client.get(ME)
    assert (response.status_code, response.json()["error"]["code"]) == (401, "unauthenticated")
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize("value", ["Bearer not-a-jwt", "Bearer ", "Basic YWRhOnB3"])
async def test_me_with_a_malformed_token(client: AsyncClient, value: str) -> None:
    response = await client.get(ME, headers={"Authorization": value})
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"invalid_access_token", "unauthenticated"}


async def test_expired_access_token_asks_for_a_refresh(client: AsyncClient, clock: FakeClock) -> None:
    headers = bearer(await login(client))
    clock.advance(timedelta(minutes=15))

    response = await client.get(ME, headers=headers)
    assert (response.status_code, response.json()["error"]["code"]) == (401, "access_token_expired")
    assert 'error_description="expired"' in response.headers["www-authenticate"]


async def test_access_token_stops_working_when_its_session_is_revoked(
    client: AsyncClient, db: AsyncSession, clock: FakeClock
) -> None:
    signed_in = await login(client)
    session = await db.scalar(select(SessionRecord))
    assert session is not None
    session.revoked_at, session.revoked_reason = clock.now, "user_revoked"
    await db.flush()

    response = await client.get(ME, headers=bearer(signed_in))
    assert (response.status_code, response.json()["error"]["code"]) == (401, "session_revoked")


async def test_unverified_user_can_use_the_api_and_sees_the_flag(
    client: AsyncClient, outbox: InMemoryTransport
) -> None:
    me = await client.get(ME, headers=bearer(await login(client)))
    assert me.json()["emailVerified"] is False

    await client.post("/api/v1/auth/verify-email", json={"token": token_from(outbox.outbox[-1])})
    me = await client.get(ME, headers=bearer(await login(client)))
    assert me.json()["emailVerified"] is True


# --- refresh ------------------------------------------------------------------------------------


async def test_refresh_rotates_the_cookie_and_issues_a_new_access_token(
    client: AsyncClient, clock: FakeClock
) -> None:
    signed_in = await login(client)
    clock.advance(timedelta(minutes=16))

    refreshed = await refresh_with(client, refresh_cookie(signed_in))

    assert refreshed.status_code == 200
    assert refresh_cookie(refreshed) != refresh_cookie(signed_in)
    assert refreshed.json()["user"]["email"] == "ada@example.com"
    assert (await client.get(ME, headers=bearer(refreshed))).status_code == 200


async def test_refresh_without_a_cookie(client: AsyncClient) -> None:
    client.cookies.clear()
    response = await client.post(REFRESH, headers=WEB)
    assert (response.status_code, response.json()["error"]["code"]) == (401, "invalid_refresh_token")


async def test_concurrent_refresh_within_grace_is_a_conflict(client: AsyncClient, clock: FakeClock) -> None:
    signed_in = await login(client)
    old = refresh_cookie(signed_in)
    current = refresh_cookie(await refresh_with(client, old))
    clock.advance(timedelta(seconds=3))

    conflict = await refresh_with(client, old)
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "refresh_conflict")
    assert (await refresh_with(client, current)).status_code == 200


async def test_reused_refresh_token_revokes_the_session_everywhere(
    client: AsyncClient, db: AsyncSession, clock: FakeClock
) -> None:
    signed_in = await login(client)
    stolen = refresh_cookie(signed_in)
    legitimate = await refresh_with(client, stolen)
    clock.advance(timedelta(minutes=1))

    replay = await refresh_with(client, stolen)
    assert (replay.status_code, replay.json()["error"]["code"]) == (401, "invalid_refresh_token")
    assert f'{COOKIE}=""' in replay.headers["set-cookie"]  # told to drop the cookie

    session = await db.scalar(select(SessionRecord))
    assert session is not None
    await db.refresh(session)
    assert session.revoked_reason == "token_reuse"

    # Both the attacker's and the legitimate holder's credentials are dead now.
    assert (await refresh_with(client, refresh_cookie(legitimate))).status_code == 401
    assert (await client.get(ME, headers=bearer(legitimate))).json()["error"]["code"] == "session_revoked"


async def test_expired_session_clears_the_cookie(client: AsyncClient, clock: FakeClock) -> None:
    signed_in = await login(client)
    clock.advance(timedelta(days=30, seconds=1))

    response = await refresh_with(client, refresh_cookie(signed_in))
    assert (response.status_code, response.json()["error"]["code"]) == (401, "session_expired")
    assert "Max-Age=0" in response.headers["set-cookie"]


async def test_refresh_tokens_are_stored_hashed(client: AsyncClient, db: AsyncSession) -> None:
    token = refresh_cookie(await login(client))
    session = await db.scalar(select(SessionRecord))
    assert session is not None
    secret = token.partition(".")[2]
    assert secret.encode() not in session.refresh_token_hash
    assert len(session.refresh_token_hash) == 32


async def test_the_browser_cookie_jar_round_trip(client: AsyncClient) -> None:
    """No manual cookie handling: the client stores the Secure cookie from login and sends it back."""
    await login(client)
    first = await client.post(REFRESH, headers=WEB)
    second = await client.post(REFRESH, headers=WEB)
    assert (first.status_code, second.status_code) == (200, 200)
