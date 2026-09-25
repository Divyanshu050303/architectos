import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.config import Settings
from apps.api.email.transport import InMemoryTransport
from apps.api.main import create_app

from .conftest import token_from

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


# --- rate limits over HTTP ----------------------------------------------------------------------


async def exhaust(client: AsyncClient, limit: int, send: Any) -> None:
    for attempt in range(limit):
        response = await send(attempt)
        assert response.status_code != 429, f"limited early, at attempt {attempt + 1}"
    refused = await send(limit)
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "rate_limited")
    assert int(refused.headers["retry-after"]) > 0
    assert refused.json()["error"]["details"]["retryAfter"] == int(refused.headers["retry-after"])


async def test_login_is_limited_per_email_even_with_the_right_password(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "ada@example.com", "password": PASSWORD, "name": "Ada"}
    )

    async def attempt(_: int) -> Any:
        return await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "wrong password!!"},
            headers=WEB,
        )

    await exhaust(client, 10, attempt)
    correct = await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}, headers=WEB
    )
    assert correct.status_code == 429  # the account is protected, not just the guess


async def test_login_is_limited_per_ip_across_emails(client: AsyncClient) -> None:
    async def attempt(i: int) -> Any:
        return await client.post(
            "/api/v1/auth/login",
            json={"email": f"user{i}@example.com", "password": "whatever pw"},
            headers=WEB,
        )

    await exhaust(client, 30, attempt)


@pytest.mark.parametrize(
    ("path", "body", "limit"),
    [
        (
            "/api/v1/auth/register",
            lambda i: {"email": f"r{i}@example.com", "password": PASSWORD, "name": "R"},
            10,
        ),
        ("/api/v1/auth/forgot-password", lambda i: {"email": "same@example.com"}, 5),
        ("/api/v1/auth/resend-verification", lambda i: {"email": "same@example.com"}, 5),
        ("/api/v1/auth/reset-password", lambda i: {"token": "A" * 43, "password": PASSWORD}, 30),
        ("/api/v1/auth/verify-email", lambda i: {"token": "A" * 43}, 30),
    ],
    ids=["register", "forgot-password", "resend-verification", "reset-password", "verify-email"],
)
async def test_unauthenticated_endpoints_are_limited(
    client: AsyncClient, path: str, body: Any, limit: int
) -> None:
    async def attempt(i: int) -> Any:
        return await client.post(path, json=body(i))

    await exhaust(client, limit, attempt)


async def test_refresh_is_limited(client: AsyncClient) -> None:
    async def attempt(_: int) -> Any:
        return await client.post("/api/v1/auth/refresh", headers=WEB)

    await exhaust(client, 120, attempt)


async def test_password_confirmations_are_limited_per_user(
    client: AsyncClient, outbox: InMemoryTransport
) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "ada@example.com", "password": PASSWORD, "name": "Ada"}
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}, headers=WEB
    )
    auth = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    async def attempt(i: int) -> Any:
        if i % 2:
            return await client.request(
                "DELETE", "/api/v1/me", json={"password": "not my password"}, headers=auth
            )
        return await client.patch(
            "/api/v1/me/password",
            json={"currentPassword": "not my password", "newPassword": "x" * 20},
            headers=auth,
        )

    await exhaust(client, 10, attempt)


# --- headers, docs, body size -------------------------------------------------------------------


async def test_security_headers_on_success_and_error(client: AsyncClient) -> None:
    for response in (await client.get("/api/v1/me"), await client.get("/api/v1/nope")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "strict-transport-security" not in response.headers  # not production


async def test_docs_have_no_restrictive_csp(client: AsyncClient) -> None:
    response = await client.get("/api/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


@pytest.fixture
async def production_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    production = settings.model_copy(
        update={"environment": "production", "api_docs_enabled": None, "cookie_secure": True}
    )
    transport = ASGITransport(app=create_app(production), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        yield client


async def test_production_hides_docs_and_sends_hsts(production_client: AsyncClient) -> None:
    assert (await production_client.get("/api/docs")).status_code == 404
    assert (await production_client.get("/api/openapi.json")).status_code == 404
    response = await production_client.get("/api/v1/nope")
    assert response.headers["strict-transport-security"].startswith("max-age=")


async def test_unexpected_errors_still_carry_security_headers(production_client: AsyncClient) -> None:
    # This app was never started, so it has no database: any database-backed route fails with a 500.
    response = await production_client.get("/api/v1/me", headers={"Authorization": "Bearer x"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["strict-transport-security"].startswith("max-age=")


async def test_oversized_bodies_are_refused_before_parsing(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        content=b'{"email": "a@example.com", "password": "' + b"x" * 70_000 + b'", "name": "A"}',
        headers={"Content-Type": "application/json"},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (413, "payload_too_large")
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_oversized_bodies_without_a_declared_length_are_cut_off(client: AsyncClient) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(100):
            yield b"x" * 1024

    response = await client.post(
        "/api/v1/auth/register", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code in {400, 413}  # never accepted, never a 500
    assert response.json()["error"]["code"] in {"malformed_request", "payload_too_large"}


# --- logs ---------------------------------------------------------------------------------------


async def test_request_log_uses_route_templates_not_raw_paths(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="architectos.request")
    token = "SuperSecretInvitationToken_" + "A" * 20

    await client.post(f"/api/v1/invitations/{token}/accept")

    [record] = [r for r in caplog.records if r.name == "architectos.request"]
    assert record.route == "/api/v1/invitations/{invitation_token}/accept"  # type: ignore[attr-defined]
    assert record.status == 401  # type: ignore[attr-defined]
    assert token not in str(record.__dict__)


async def test_refusals_are_logged_for_observability(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="architectos.security")
    await client.get("/api/v1/me", headers={"Authorization": "Bearer nonsense"})
    [record] = [r for r in caplog.records if r.message == "request refused"]
    assert (record.code, record.status, record.route) == ("invalid_access_token", 401, "/api/v1/me")  # type: ignore[attr-defined]


async def test_passwords_never_reach_any_log(
    client: AsyncClient, outbox: InMemoryTransport, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await client.post(
        "/api/v1/auth/register", json={"email": "ada@example.com", "password": PASSWORD, "name": "Ada"}
    )
    await client.post("/api/v1/auth/verify-email", json={"token": token_from(outbox.outbox[-1])})
    await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}, headers=WEB
    )
    await client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong password!!"}, headers=WEB
    )
    assert PASSWORD not in caplog.text
    assert "wrong password" not in caplog.text
