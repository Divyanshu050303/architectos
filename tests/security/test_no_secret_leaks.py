"""A complete user journey, with every response scanned for secrets and security internals."""

import re
from http.cookies import SimpleCookie

from httpx import AsyncClient, Response

from apps.api.email.transport import InMemoryTransport
from tests.integration.api.conftest import token_from

from .support import PASSWORD, WEB

NEW_PASSWORD = "tangerine submarine orchestra"
FORBIDDEN_FIELDS = re.compile(
    r"password_?hash|token_?hash|refresh_?token|\$argon2|previous_refresh", re.IGNORECASE
)


async def test_full_journey_leaks_nothing(client: AsyncClient, outbox: InMemoryTransport) -> None:
    responses: list[Response] = []

    async def call(method: str, url: str, **kwargs: object) -> Response:
        response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
        responses.append(response)
        return response

    await call(
        "POST",
        "/api/v1/auth/register",
        json={"email": "ada@example.com", "password": PASSWORD, "name": "Ada"},
    )
    verify_token = token_from(outbox.outbox[-1])
    await call("POST", "/api/v1/auth/verify-email", json={"token": verify_token})
    login = await call(
        "POST", "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}, headers=WEB
    )
    auth = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    cookie = SimpleCookie()
    cookie.load(login.headers["set-cookie"])
    refresh_secret = cookie["__Secure-architectos_refresh"].value.partition(".")[2]

    await call("POST", "/api/v1/auth/refresh", headers=WEB)
    await call("GET", "/api/v1/me", headers=auth)
    await call("GET", "/api/v1/me/sessions", headers=auth)
    await call("PATCH", "/api/v1/me", json={"name": "Ada L."}, headers=auth)
    org = (await call("POST", "/api/v1/organizations", json={"name": "Acme"}, headers=auth)).json()["id"]
    await call(
        "POST",
        f"/api/v1/organizations/{org}/invitations",
        json={"email": "bob@example.com", "role": "member"},
        headers=auth,
    )
    invite_token = token_from(outbox.outbox[-1])
    await call("GET", f"/api/v1/organizations/{org}/invitations", headers=auth)
    await call("GET", f"/api/v1/organizations/{org}/members", headers=auth)
    await call("GET", f"/api/v1/organizations/{org}/audit-log", headers=auth)
    await call(
        "PATCH",
        "/api/v1/me/password",
        json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD},
        headers=auth,
    )
    await call("POST", "/api/v1/auth/forgot-password", json={"email": "ada@example.com"})
    reset_token = token_from(outbox.outbox[-1])
    await call("POST", "/api/v1/auth/reset-password", json={"token": reset_token, "password": PASSWORD})
    await call(
        "POST",
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "wrong password!!"},
        headers=WEB,
    )
    await call("POST", "/api/v1/auth/logout", headers=WEB)

    secrets = [
        PASSWORD,
        NEW_PASSWORD,
        "wrong password!!",
        verify_token,
        invite_token,
        reset_token,
        refresh_secret,
    ]
    for response in responses:
        headers = {k: v for k, v in response.headers.items() if k.lower() != "set-cookie"}
        visible = response.text + " " + " ".join(f"{k}: {v}" for k, v in headers.items())
        leaked = [s for s in secrets if s in visible]
        assert not leaked, (response.request.url, leaked)
        assert not FORBIDDEN_FIELDS.search(visible), (response.request.url, visible[:300])
    assert len(responses) == 17
