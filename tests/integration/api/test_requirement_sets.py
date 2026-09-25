import hashlib
import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport

from .requirement_support import RPS, World, create, member, signed_in

pytestmark = pytest.mark.integration


def sets_url(world: World) -> str:
    return f"/api/v1/projects/{world.project_id}/requirement-sets"


async def create_set(client: AsyncClient, world: World, **body: Any) -> dict[str, Any]:
    response = await client.post(sets_url(world), json=body, headers=world.ada)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_create_read_and_planning_input(client: AsyncClient, world: World) -> None:
    throughput = await create(client, world)
    await create(client, world, title="Idea", status="draft")

    created = await create_set(client, world, name="Launch baseline")
    assert created == created | {
        "projectId": world.project_id,
        "number": 1,
        "label": "v1",
        "name": "Launch baseline",
        "schemaVersion": 1,
        "requirementCount": 1,
        "requirements": [{"requirementId": throughput["id"], "reference": "REQ-1", "version": 1}],
    }
    fetched = await client.get(f"{sets_url(world)}/{created['id']}", headers=world.ada)
    assert fetched.json() == created

    planning = (
        await client.get(f"{sets_url(world)}/{created['id']}/planning-input", headers=world.ada)
    ).json()
    assert (planning["requirementSetId"], planning["schemaVersion"], planning["contentHash"]) == (
        created["id"],
        1,
        created["contentHash"],
    )
    document = planning["planningInput"]
    assert document["project"] == {
        "id": world.project_id,
        "settings": {"cloud_provider": None, "currency": "USD"},
    }
    [pinned] = document["requirements"]
    assert (pinned["reference"], pinned["version"], pinned["constraint"]) == (
        "REQ-1",
        1,
        {"metric": "requests_per_second", "operator": ">=", "value": "2000", "unit": "requests/second"},
    )
    # Anyone can verify the hash from the document alone.
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(canonical).hexdigest() == created["contentHash"]


async def test_sets_are_reproducible_after_requirements_change(client: AsyncClient, world: World) -> None:
    requirement = await create(client, world)
    v1 = await create_set(client, world)
    before = (await client.get(f"{sets_url(world)}/{v1['id']}/planning-input", headers=world.ada)).json()

    await client.patch(
        f"{world.base}/{requirement['id']}",
        json={"expectedVersion": 1, "structuredData": RPS | {"value": 5000}, "changeReason": "Forecast grew"},
        headers=world.ada,
    )
    await client.delete(f"{world.base}/{requirement['id']}", headers=world.ada)

    after = (await client.get(f"{sets_url(world)}/{v1['id']}/planning-input", headers=world.ada)).json()
    assert after == before
    assert (await client.get(f"{sets_url(world)}/{v1['id']}", headers=world.ada)).json() == v1


async def test_listing(client: AsyncClient, world: World) -> None:
    await create(client, world)
    for index in range(3):
        await create_set(client, world, name=f"Set {index}")
    page = (await client.get(sets_url(world), params={"limit": 2}, headers=world.ada)).json()
    assert [s["label"] for s in page["requirementSets"]] == ["v3", "v2"]
    assert "requirements" not in page["requirementSets"][0]
    rest = (
        await client.get(sets_url(world), params={"cursor": page["nextCursor"]}, headers=world.ada)
    ).json()
    assert ([s["label"] for s in rest["requirementSets"]], rest["nextCursor"]) == (["v1"], None)
    bad = await client.get(sets_url(world), params={"cursor": "garbage"}, headers=world.ada)
    assert bad.json()["error"]["code"] == "invalid_cursor"


async def test_conflicts_are_refused(client: AsyncClient, world: World) -> None:
    await create(client, world, structuredData=RPS | {"value": 10_000})
    await create(client, world, structuredData=RPS | {"operator": "<=", "value": 5_000})
    response = await client.post(sets_url(world), json={}, headers=world.ada)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "requirement_set_conflicts"
    assert error["details"]["conflicts"][0]["requirements"] == ["REQ-1", "REQ-2"]


async def test_invalid_selections(client: AsyncClient, world: World) -> None:
    draft = await create(client, world, status="draft")
    cases = [
        ({}, {"field": "requirementIds", "reason": "nothing_in_force"}),
        ({"requirementIds": []}, {"field": "requirementIds", "reason": "empty"}),
        ({"requirementIds": [draft["id"]]}, {"requirementId": draft["id"], "reason": "not_in_force"}),
        ({"requirementIds": [str(uuid.uuid4())]}, None),
    ]
    for body, details in cases:
        response = await client.post(sets_url(world), json=body, headers=world.ada)
        assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_requirement_set"), (
            body
        )
        if details is not None:
            assert response.json()["error"]["details"] == details
    malformed = await client.post(sets_url(world), json={"requirementIds": ["REQ-1"]}, headers=world.ada)
    assert malformed.json()["error"]["code"] == "validation_error"
    extra = await client.post(sets_url(world), json={"contentHash": "0" * 64}, headers=world.ada)
    assert extra.json()["error"]["code"] == "validation_error"


async def test_archived_projects_cannot_get_new_sets(client: AsyncClient, world: World) -> None:
    await create(client, world)
    existing = await create_set(client, world)
    await client.post(f"/api/v1/projects/{world.project_id}/archive", headers=world.ada)
    response = await client.post(sets_url(world), json={}, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "project_archived")
    assert (await client.get(f"{sets_url(world)}/{existing['id']}", headers=world.ada)).status_code == 200


async def test_authorization(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    await create(client, world)
    created = await create_set(client, world)
    viewer = await member(client, db, outbox, world, "viewer")
    stranger = await signed_in(client, outbox, "eve@example.com")
    url = f"{sets_url(world)}/{created['id']}"

    assert (await client.get(url, headers=viewer)).status_code == 200
    assert (await client.get(f"{url}/planning-input", headers=viewer)).status_code == 200
    assert (await client.post(sets_url(world), json={}, headers=viewer)).status_code == 403
    for response in (
        await client.get(url, headers=stranger),
        await client.get(f"{url}/planning-input", headers=stranger),
        await client.get(sets_url(world), headers=stranger),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (404, "project_not_found")
    wrong_project = f"/api/v1/projects/{world.other_id}/requirement-sets/{created['id']}"
    assert (await client.get(wrong_project, headers=world.ada)).json()["error"][
        "code"
    ] == "requirement_set_not_found"


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_sets_cannot_be_modified(client: AsyncClient, world: World, method: str) -> None:
    await create(client, world)
    created = await create_set(client, world)
    for url in (f"{sets_url(world)}/{created['id']}", f"{sets_url(world)}/{created['id']}/planning-input"):
        response = await client.request(method, url, json={"name": "Rewritten"}, headers=world.ada)
        assert response.status_code == 405
