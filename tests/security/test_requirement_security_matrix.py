"""The requirement items of the security test matrix (spec section 50) not covered by the sweeps:
malicious JSON, pagination and search that try to cross boundaries, and moving a requirement."""

import pytest
from httpx import AsyncClient

from apps.api.email.transport import InMemoryTransport
from tests.integration.api.requirement_support import THROUGHPUT, World, create, make_world, signed_in

from .support import assert_error_envelope


@pytest.fixture
async def world(client: AsyncClient, outbox: InMemoryTransport) -> World:
    return await make_world(client, outbox)


@pytest.mark.parametrize(
    ("depth", "status", "code"),
    [(100, 422, "invalid_requirement"), (30_000, 400, "malformed_request")],  # 30k: past the parser's limit
)
async def test_deeply_nested_json_is_refused_cleanly(
    client: AsyncClient, world: World, depth: int, status: int, code: str
) -> None:
    nested = "[" * depth + "]" * depth
    body = '{"type":"capacity","category":"throughput","title":"t","statement":"s","priority":"low",'
    body += f'"structuredData":{{"metric":{nested}}}}}'
    response = await client.post(
        world.base, content=body, headers=world.ada | {"content-type": "application/json"}
    )
    assert_error_envelope(response, status, code)


@pytest.mark.parametrize(
    "structured_data",
    [
        {"metric": "__import__('os').system('id')", "operator": ">=", "value": 1, "unit": "ms"},
        {"metric": "latency", "operator": "<=", "value": "__import__('os')", "unit": "ms"},
        {"metric": "latency", "operator": "<=", "value": 1, "unit": "ms", "$where": "sleep(1000)"},
        {"metric": "latency", "operator": "<=", "value": {"$gt": 0}, "unit": "ms"},
        {"metric": "latency", "operator": "<=", "value": "1e999999", "unit": "ms"},
        {"metric": "latency", "operator": "<=", "value": "NaN", "unit": "ms"},
    ],
    ids=["code-as-metric", "code-as-value", "operator-injection", "object-as-value", "huge-exponent", "nan"],
)
async def test_malicious_structured_data_is_data_and_is_refused(
    client: AsyncClient, world: World, structured_data: dict[str, object]
) -> None:
    body = THROUGHPUT | {"type": "performance", "category": "latency", "structuredData": structured_data}
    response = await client.post(world.base, json=body, headers=world.ada)
    assert_error_envelope(response, 422, "invalid_requirement")


async def test_a_cursor_does_not_open_another_tenants_project(
    client: AsyncClient, outbox: InMemoryTransport, world: World
) -> None:
    for index in range(3):
        await create(client, world, title=f"Mine {index}")
    cursor = (await client.get(world.base, params={"limit": 1}, headers=world.ada)).json()["nextCursor"]
    eve = await signed_in(client, outbox, "eve@example.com")
    eve_org = (await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=eve)).json()["id"]
    eve_project = (
        await client.post(f"/api/v1/organizations/{eve_org}/projects", json={"name": "Hers"}, headers=eve)
    ).json()["id"]

    # Her cursor-less and cursor pages of her own project never include Ada's requirements...
    own = await client.get(
        f"/api/v1/projects/{eve_project}/requirements", params={"cursor": cursor}, headers=eve
    )
    assert own.json()["requirements"] == []
    # ...and a cursor is no key to Ada's project.
    theirs = await client.get(world.base, params={"cursor": cursor}, headers=eve)
    assert_error_envelope(theirs, 404, "project_not_found")


async def test_search_stays_inside_the_project(client: AsyncClient, world: World) -> None:
    await create(client, world, title="Secret payment rail")
    other = f"/api/v1/projects/{world.other_id}/requirements"
    for term in ("Secret", "%", "_", "' OR 1=1 --", "\\"):
        found = (await client.get(other, params={"search": term}, headers=world.ada)).json()["requirements"]
        assert found == [], term


async def test_a_requirement_cannot_be_moved_to_another_project(client: AsyncClient, world: World) -> None:
    created = await create(client, world)
    url = f"{world.base}/{created['id']}"
    for field in ("projectId", "project_id", "number", "source", "confidence", "type", "version"):
        response = await client.patch(
            url, json={"expectedVersion": 1, "changeReason": "x", field: world.other_id}, headers=world.ada
        )
        assert_error_envelope(response, 422, "validation_error")
    unchanged = (await client.get(url, headers=world.ada)).json()
    assert (unchanged["projectId"], unchanged["version"]) == (world.project_id, 1)
