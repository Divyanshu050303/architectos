import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.auth import REGISTRATION_ACCEPTED
from persistence.models import UserRecord

pytestmark = pytest.mark.integration

URL = "/api/v1/auth/register"
PASSWORD = "correct horse battery staple"


def payload(**overrides: object) -> dict[str, object]:
    return {"email": "ada@example.com", "password": PASSWORD, "name": "Ada Lovelace"} | overrides


async def users_with_email(db: AsyncSession, email: str) -> list[UserRecord]:
    return list((await db.scalars(select(UserRecord).where(func.lower(UserRecord.email) == email))).all())


async def test_register_creates_an_unverified_user(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.post(URL, json=payload(email="  Ada@Example.com "))

    assert response.status_code == 202
    assert response.json() == {"message": REGISTRATION_ACCEPTED}
    [user] = await users_with_email(db, "ada@example.com")
    assert user.email == "ada@example.com"
    assert user.name == "Ada Lovelace"
    assert user.email_verified_at is None
    assert user.password_hash.startswith("$argon2id$")


async def test_response_never_contains_the_password_or_its_hash(
    client: AsyncClient, db: AsyncSession
) -> None:
    response = await client.post(URL, json=payload())
    [user] = await users_with_email(db, "ada@example.com")

    assert PASSWORD not in response.text
    assert user.password_hash not in response.text
    assert "passwordHash" not in response.text


async def test_duplicate_registration_is_indistinguishable(client: AsyncClient, db: AsyncSession) -> None:
    first = await client.post(URL, json=payload())
    second = await client.post(URL, json=payload(email="ADA@example.com", name="Someone Else"))

    assert (first.status_code, first.json()) == (second.status_code, second.json())
    [user] = await users_with_email(db, "ada@example.com")
    assert user.name == "Ada Lovelace"  # the existing account is untouched


async def test_weak_password_lists_every_reason(client: AsyncClient) -> None:
    response = await client.post(URL, json=payload(password="   "))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "weak_password"
    assert error["details"] == {"reasons": ["too_short", "blank"]}


async def test_common_password_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.post(URL, json=payload(password="password1234"))

    assert response.json()["error"]["details"] == {"reasons": ["common"]}
    assert await users_with_email(db, "ada@example.com") == []


@pytest.mark.parametrize(
    ("overrides", "code"),
    [({"email": "not-an-email"}, "invalid_email"), ({"name": "   "}, "invalid_name")],
)
async def test_invalid_identity_values(client: AsyncClient, overrides: dict[str, object], code: str) -> None:
    response = await client.post(URL, json=payload(**overrides))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "body",
    [
        {"email": "ada@example.com", "name": "Ada"},  # missing password
        payload(password=12345678901234),  # wrong type
        payload(password="x" * 1025),  # over the input cap
        payload(role="owner"),  # unknown field
    ],
    ids=["missing-field", "wrong-type", "oversized", "unknown-field"],
)
async def test_malformed_bodies_are_validation_errors(client: AsyncClient, body: dict[str, object]) -> None:
    response = await client.post(URL, json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["fields"]


async def test_validation_errors_never_echo_the_submitted_password(client: AsyncClient) -> None:
    secret = "hunter2-" + "x" * 1020
    response = await client.post(URL, json=payload(password=secret, unexpected=True))
    assert "hunter2" not in response.text


async def test_invalid_json_is_a_validation_error(client: AsyncClient) -> None:
    response = await client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_errors_carry_the_request_id(client: AsyncClient) -> None:
    response = await client.post(URL, json=payload(email="nope"), headers={"X-Request-ID": "req_test_12345"})

    assert response.headers["X-Request-ID"] == "req_test_12345"
    assert response.json()["error"]["request_id"] == "req_test_12345"


async def test_malformed_request_ids_are_replaced(client: AsyncClient) -> None:
    response = await client.post(
        URL, json=payload(email="nope"), headers={"X-Request-ID": "bad id\nInjected: 1"}
    )
    request_id = response.headers["X-Request-ID"]
    assert request_id.startswith("req_")
    assert response.json()["error"]["request_id"] == request_id


async def test_unknown_routes_and_methods_use_the_error_contract(client: AsyncClient) -> None:
    missing = await client.get("/api/v1/nope")
    wrong_method = await client.get(URL)

    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")
    assert (wrong_method.status_code, wrong_method.json()["error"]["code"]) == (405, "method_not_allowed")


async def test_unexpected_errors_hide_internals(app: FastAPI, client: AsyncClient) -> None:
    from apps.api.dependencies.services import get_auth_service  # noqa: PLC0415

    def broken() -> None:
        raise RuntimeError("password=hunter2 connection to 10.0.0.5 failed")

    app.dependency_overrides[get_auth_service] = broken
    response = await client.post(URL, json=payload())

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert "10.0.0.5" not in response.text
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_cors_allows_the_web_app_origin_only(client: AsyncClient) -> None:
    def preflight(origin: str) -> dict[str, str]:
        return {"Origin": origin, "Access-Control-Request-Method": "POST"}

    allowed = await client.options(URL, headers=preflight("http://localhost:3000"))
    denied = await client.options(URL, headers=preflight("https://evil.example"))

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers
