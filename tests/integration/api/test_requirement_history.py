import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport

from .requirement_support import RPS, WEB, World, create, member, signed_in

pytestmark = pytest.mark.integration


async def revise(
    client: AsyncClient, world: World, requirement_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.patch(f"{world.base}/{requirement_id}", json=body, headers=world.ada)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def history(
    client: AsyncClient, world: World, requirement_id: str, **params: str | int
) -> dict[str, Any]:
    response = await client.get(f"{world.base}/{requirement_id}/versions", params=params, headers=world.ada)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_every_change_is_kept_in_order_with_its_reason(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    await revise(
        client,
        world,
        created["id"],
        {
            "expectedVersion": 1,
            "structuredData": RPS | {"value": 5000},
            "statement": "Support 5,000 requests per second.",
            "changeReason": "Traffic forecast increased from 2K to 5K RPS",
        },
    )
    await revise(
        client,
        world,
        created["id"],
        {"expectedVersion": 2, "status": "satisfied", "changeReason": "Load test passed"},
    )

    versions = (await history(client, world, created["id"]))["versions"]
    assert [
        (v["version"], v["status"], v["structuredData"]["value"], v["changeReason"]) for v in versions
    ] == [
        (1, "active", "2000", None),
        (2, "active", "5000", "Traffic forecast increased from 2K to 5K RPS"),
        (3, "satisfied", "5000", "Load test passed"),
    ]
    assert versions[0]["statement"] == "The API must support 2,000 requests per second."
    assert versions[1]["normalizedData"] == {
        "metric": "requests_per_second",
        "operator": ">=",
        "value": "5000",
        "unit": "requests/second",
    }
    assert {v["requirementId"] for v in versions} == {created["id"]}
    assert all(v["createdByUserId"] == created["createdByUserId"] for v in versions)


async def test_a_version_can_be_fetched_and_never_changes(client: AsyncClient, world: World) -> None:
    created = await create(client, world, status="draft")
    first = (await client.get(f"{world.base}/{created['id']}/versions/1", headers=world.ada)).json()
    await revise(client, world, created["id"], {"expectedVersion": 1, "title": "Renamed"})

    again = await client.get(f"{world.base}/{created['id']}/versions/1", headers=world.ada)
    assert again.json() == first
    assert first["title"] == "API throughput"
    latest = (await client.get(f"{world.base}/{created['id']}/versions/2", headers=world.ada)).json()
    assert latest["title"] == "Renamed"


async def test_history_paginates(client: AsyncClient, world: World) -> None:
    created = await create(client, world, status="draft")
    for version in range(1, 6):
        await revise(client, world, created["id"], {"expectedVersion": version, "title": f"Title {version}"})

    seen: list[int] = []
    params: dict[str, str | int] = {"limit": 2}
    while True:
        page = await history(client, world, created["id"], **params)
        seen += [v["version"] for v in page["versions"]]
        if page["nextCursor"] is None:
            break
        params = {"limit": 2, "cursor": page["nextCursor"]}
    assert seen == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("cursor", ["garbage", "WyJjcmVhdGVkX2F0IiwiMSJd"])
async def test_bad_cursors(client: AsyncClient, world: World, cursor: str) -> None:
    created = await create(client, world)
    response = await client.get(
        f"{world.base}/{created['id']}/versions", params={"cursor": cursor}, headers=world.ada
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_cursor")


async def test_missing_versions(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    missing = await client.get(f"{world.base}/{created['id']}/versions/2", headers=world.ada)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "requirement_version_not_found")
    for bad in ("0", "-1", "one", "99999999999"):
        assert (
            await client.get(f"{world.base}/{created['id']}/versions/{bad}", headers=world.ada)
        ).status_code == 422
    unknown = await client.get(f"{world.base}/{uuid.uuid4()}/versions", headers=world.ada)
    assert unknown.json()["error"]["code"] == "requirement_not_found"
    unknown_version = await client.get(f"{world.base}/{uuid.uuid4()}/versions/1", headers=world.ada)
    assert unknown_version.json()["error"]["code"] == "requirement_not_found"


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "POST"])
async def test_history_cannot_be_modified_through_the_api(
    client: AsyncClient, world: World, method: str
) -> None:
    created = await create(client, world)
    before = await history(client, world, created["id"])
    for url in (f"{world.base}/{created['id']}/versions/1", f"{world.base}/{created['id']}/versions"):
        response = await client.request(method, url, json={"title": "Rewritten"}, headers=world.ada | WEB)
        assert (response.status_code, response.json()["error"]["code"]) == (405, "method_not_allowed")
    assert await history(client, world, created["id"]) == before


async def test_deleted_requirements_hide_their_history(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    await client.delete(f"{world.base}/{created['id']}", headers=world.ada)
    for url in (f"{world.base}/{created['id']}/versions", f"{world.base}/{created['id']}/versions/1"):
        assert (await client.get(url, headers=world.ada)).json()["error"]["code"] == "requirement_not_found"


async def test_history_is_tenant_and_project_scoped(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    created = await create(client, world)
    stranger = await signed_in(client, outbox, "eve@example.com")
    viewer = await member(client, db, outbox, world, "viewer")
    path = f"{created['id']}/versions"

    assert (await client.get(f"{world.base}/{path}", headers=viewer)).status_code == 200
    assert (await client.get(f"{world.base}/{path}/1", headers=viewer)).status_code == 200
    for url in (f"{world.base}/{path}", f"{world.base}/{path}/1"):
        response = await client.get(url, headers=stranger)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "project_not_found")
    other = f"/api/v1/projects/{world.other_id}/requirements/{path}"
    for url in (other, f"{other}/1"):
        assert (await client.get(url, headers=world.ada)).json()["error"]["code"] == "requirement_not_found"


async def test_archived_projects_keep_their_history_readable(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    await client.post(f"/api/v1/projects/{world.project_id}/archive", headers=world.ada)
    assert len((await history(client, world, created["id"]))["versions"]) == 1
