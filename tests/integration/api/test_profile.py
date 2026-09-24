import uuid

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import SessionRecord, UserRecord

pytestmark = pytest.mark.integration

LOGIN, REFRESH, ME = "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/me"
COOKIE = "__Secure-architectos_refresh"
PASSWORD = "correct horse battery staple"
AVATAR = "https://images.example.com/ada.png"
WEB = {"X-Requested-With": "architectos", "Origin": "http://localhost:3000"}


async def register(client: AsyncClient, email: str = "ada@example.com", name: str = "Ada") -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": name}
    )
    assert response.status_code == 202


async def login(client: AsyncClient, email: str = "ada@example.com") -> Response:
    return await client.post(LOGIN, json={"email": email, "password": PASSWORD}, headers=WEB)


def auth(response: Response) -> dict[str, str]:
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


@pytest.fixture(autouse=True)
async def ada(client: AsyncClient) -> None:
    await register(client)


# --- PATCH /me ----------------------------------------------------------------------------------


async def test_update_name_and_avatar(client: AsyncClient) -> None:
    headers = auth(await login(client))

    response = await client.patch(ME, json={"name": " Ada  Lovelace ", "avatarUrl": AVATAR}, headers=headers)

    assert response.status_code == 200
    assert (response.json()["name"], response.json()["avatarUrl"]) == ("Ada Lovelace", AVATAR)
    assert (await client.get(ME, headers=headers)).json()["name"] == "Ada Lovelace"


async def test_partial_update_leaves_other_fields_alone(client: AsyncClient) -> None:
    headers = auth(await login(client))
    await client.patch(ME, json={"avatarUrl": AVATAR}, headers=headers)

    renamed = await client.patch(ME, json={"name": "Ada L."}, headers=headers)
    assert renamed.json()["avatarUrl"] == AVATAR

    removed = await client.patch(ME, json={"avatarUrl": None}, headers=headers)
    assert (removed.json()["name"], removed.json()["avatarUrl"]) == ("Ada L.", None)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "nothing_to_update"),
        ({"name": "   "}, "invalid_name"),
        ({"name": "x" * 81}, "invalid_name"),
        ({"avatarUrl": "javascript:alert(1)"}, "invalid_avatar_url"),
        ({"avatarUrl": "http://images.example.com/a.png"}, "invalid_avatar_url"),
        ({"email": "mallory@example.com"}, "validation_error"),
        ({"emailVerified": True}, "validation_error"),
        ({"status": "active"}, "validation_error"),
    ],
    ids=["empty", "blank-name", "long-name", "js-avatar", "http-avatar", "email", "verified-flag", "status"],
)
async def test_invalid_or_forbidden_updates(
    client: AsyncClient, db: AsyncSession, body: dict[str, object], code: str
) -> None:
    headers = auth(await login(client))

    response = await client.patch(ME, json=body, headers=headers)

    assert (response.status_code, response.json()["error"]["code"]) == (422, code)
    user = await db.scalar(select(UserRecord).where(UserRecord.email == "ada@example.com"))
    assert user is not None
    await db.refresh(user)
    assert (user.name, user.email, user.email_verified_at) == ("Ada", "ada@example.com", None)


async def test_update_requires_authentication(client: AsyncClient) -> None:
    response = await client.patch(ME, json={"name": "Mallory"})
    assert (response.status_code, response.json()["error"]["code"]) == (401, "unauthenticated")


async def test_a_user_only_ever_updates_themself(client: AsyncClient, db: AsyncSession) -> None:
    await register(client, "grace@example.com", "Grace")
    headers = auth(await login(client))

    await client.patch(ME, json={"name": "Changed"}, headers=headers)

    grace = await db.scalar(select(UserRecord).where(UserRecord.email == "grace@example.com"))
    assert grace is not None
    assert grace.name == "Grace"


# --- DELETE /me ---------------------------------------------------------------------------------


async def test_delete_account(client: AsyncClient, db: AsyncSession) -> None:
    here, elsewhere = await login(client), await login(client)
    user_id = here.json()["user"]["id"]

    response = await client.request("DELETE", ME, json={"password": PASSWORD}, headers=auth(here))

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    for device in (here, elsewhere):
        me = await client.get(ME, headers=auth(device))
        assert (me.status_code, me.json()["error"]["code"]) == (401, "session_revoked")
    assert (await login(client)).json()["error"]["code"] == "invalid_credentials"

    record = await db.get(UserRecord, uuid.UUID(user_id))
    assert record is not None
    await db.refresh(record)
    assert record.status == "deleted"
    assert record.deleted_at is not None
    assert record.email.endswith("@deleted.invalid")
    assert (record.name, record.avatar_url, record.email_verified_at) == ("Deleted user", None, None)
    assert not record.password_hash.startswith("$argon2")
    sessions = (await db.scalars(select(SessionRecord).where(SessionRecord.user_id == record.id))).all()
    assert {s.revoked_reason for s in sessions} == {"account_deleted"}


async def test_deleted_email_can_register_again_as_a_new_account(client: AsyncClient) -> None:
    old_id = (await login(client)).json()["user"]["id"]
    headers = auth(await login(client))
    await client.request("DELETE", ME, json={"password": PASSWORD}, headers=headers)

    await register(client, name="Ada Again")
    fresh = await login(client)

    assert fresh.status_code == 200
    assert fresh.json()["user"]["id"] != old_id
    assert fresh.json()["user"]["name"] == "Ada Again"


async def test_delete_requires_the_current_password(client: AsyncClient) -> None:
    headers = auth(await login(client))

    wrong = await client.request("DELETE", ME, json={"password": "not my password"}, headers=headers)
    missing = await client.request("DELETE", ME, json={}, headers=headers)

    assert (wrong.status_code, wrong.json()["error"]["code"]) == (400, "incorrect_password")
    assert (missing.status_code, missing.json()["error"]["code"]) == (422, "validation_error")
    assert (await client.get(ME, headers=headers)).status_code == 200


async def test_delete_requires_authentication(client: AsyncClient) -> None:
    response = await client.request("DELETE", ME, json={"password": PASSWORD})
    assert (response.status_code, response.json()["error"]["code"]) == (401, "unauthenticated")
