"""The architecture API (Architecture IR phase 5): create, read, edit, lay out, compare."""

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from core.architecture_ir.serialization import to_dict
from tests.unit.architecture_ir.builders import api_and_postgres, discovered, service_cache_queue

from .requirement_support import World, create, member, signed_in

pytestmark = pytest.mark.integration


def url(world: World, suffix: str = "") -> str:
    return f"/api/v1/projects/{world.project_id}/architecture{suffix}"


async def created(
    client: AsyncClient, world: World, ir: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    response = await client.post(
        url(world), json={"ir": ir or to_dict(api_and_postgres())} | extra, headers=world.ada
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def edit(
    client: AsyncClient, world: World, base: int, *commands: dict[str, Any], status: int = 201
) -> dict[str, Any]:
    response = await client.post(
        url(world, "/commands"), json={"baseVersion": base, "commands": list(commands)}, headers=world.ada
    )
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    return body


# --- create and read -------------------------------------------------------------------------------


async def test_create_and_read_back_exactly(client: AsyncClient, world: World) -> None:
    ir = to_dict(service_cache_queue())
    ir["requirement_refs"] = []  # the fixture cites requirements this project does not have
    for element in ir["nodes"]:
        element["requirement_refs"] = []
    architecture = await created(client, world, ir, reason="First design")
    assert (architecture["version"], architecture["currentVersion"], architecture["source"]) == (1, 1, "user")
    assert architecture["ir"] == ir  # the canonical document, unchanged
    assert architecture["reason"] == "First design"
    assert architecture["layout"] == {"positions": {}, "updatedAt": None}
    assert architecture["summary"] == "Created with 4 nodes and 3 connections."
    again = await client.get(url(world), headers=world.ada)
    assert again.json() == architecture


async def test_no_architecture_yet(client: AsyncClient, world: World) -> None:
    response = await client.get(url(world), headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "architecture_not_found")


async def test_an_import_keeps_unknowns_and_provenance(client: AsyncClient, world: World) -> None:
    ir = to_dict(discovered())
    architecture = await created(client, world, ir, source="import")
    assert architecture["source"] == "import"
    db = next(n for n in architecture["ir"]["nodes"] if n["id"] == "aws_db_instance.orders")
    assert db["configuration"]["unknown"] == ["backup_retention_seconds", "max_connections"]
    assert db["provenance"]["source"] == "terraform"


async def test_invalid_architectures_say_exactly_what_is_wrong(client: AsyncClient, world: World) -> None:
    ir = to_dict(api_and_postgres())
    ir["connections"][0]["target_id"] = "ghost"
    ir["nodes"][0]["configuration"]["values"]["replicas"] = -2
    response = await client.post(url(world), json={"ir": ir}, headers=world.ada)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_architecture"
    assert error["details"]["violations"] == [
        {
            "element": "node",
            "elementId": "api",
            "field": "configuration.replicas",
            "rule": "out_of_range",
            "message": "replicas must be at least 0.",
        }
    ]  # graph rules are checked once every element is valid on its own
    newer = await client.post(url(world), json={"ir": ir | {"schema_version": 99}}, headers=world.ada)
    assert newer.json()["error"]["details"]["violations"][0]["rule"] == "unsupported_schema_version"


async def test_one_architecture_per_project(client: AsyncClient, world: World) -> None:
    await created(client, world)
    again = await client.post(url(world), json={"ir": to_dict(api_and_postgres())}, headers=world.ada)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "architecture_already_exists")


async def test_requirement_references_are_checked(client: AsyncClient, world: World) -> None:
    requirement = await create(client, world)
    ir = to_dict(api_and_postgres())
    ir["requirement_refs"] = [{"requirement_id": requirement["id"], "version": 1}]
    await created(client, world, ir)
    other = await client.post(
        f"/api/v1/projects/{world.other_id}/architecture", json={"ir": ir}, headers=world.ada
    )
    assert other.json()["error"]["details"]["violations"][0]["rule"] == "unknown_requirement"


# --- edits and history -----------------------------------------------------------------------------


async def test_edits_create_revisions_with_their_changes(client: AsyncClient, world: World) -> None:
    await created(client, world)
    link = {
        "id": "api-cache",
        "source_id": "api",
        "target_id": "cache",
        "kind": "data_access",
        "protocol": "redis",
    }
    configure = {"cpu_limit_cores": "1.5", "runtime": None}
    second = await edit(
        client,
        world,
        1,
        {"type": "change_replicas", "nodeId": "api", "replicas": 6},
        {"type": "update_configuration", "nodeId": "api", "values": configure},
        {"type": "add_node", "node": {"id": "cache", "kind": "cache", "name": "Cache"}},
        {"type": "add_connection", "connection": link},
        {"type": "rename_node", "nodeId": "db", "name": "Primary DB"},
    )
    assert (second["version"], second["parentVersion"], second["currentVersion"]) == (2, 1, 2)
    api = next(n for n in second["ir"]["nodes"] if n["id"] == "api")
    assert api["configuration"]["values"]["cpu_limit_cores"] == "1.5"
    assert api["field_provenance"]["configuration.replicas"]["source"] == "user_edit"
    changes = second["changes"]
    assert changes["summary"] == "1 node added (Cache); 2 nodes modified; 1 connection added."
    assert [c["elementId"] for c in changes["nodes"]] == ["api", "cache", "db"]

    third = await edit(
        client, world, 2, {"type": "remove_nodes", "nodeIds": ["cache"]}, {"type": "remove_connections",
        "connectionIds": ["web-api"]}
    )  # fmt: skip
    assert [c["id"] for c in third["ir"]["connections"]] == ["api-db"]

    history = (await client.get(url(world, "/versions"), headers=world.ada)).json()
    assert [v["version"] for v in history["versions"]] == [3, 2, 1]
    first = (await client.get(url(world, "/versions/1"), headers=world.ada)).json()
    assert first["ir"] == to_dict(api_and_postgres())  # history is untouched
    assert first["currentVersion"] == 3
    page = (await client.get(url(world, "/versions"), params={"limit": 2}, headers=world.ada)).json()
    rest = await client.get(url(world, "/versions"), params={"cursor": page["nextCursor"]}, headers=world.ada)
    assert [v["version"] for v in rest.json()["versions"]] == [1]


async def test_a_stale_edit_is_a_conflict(client: AsyncClient, world: World) -> None:
    await created(client, world)
    await edit(client, world, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 2})
    stale = await edit(
        client, world, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 9}, status=409
    )
    assert stale["error"]["code"] == "architecture_version_conflict"
    assert stale["error"]["details"] == {"latestVersion": 2}


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ({"type": "rename_node", "nodeId": "ghost", "name": "x"}, "invalid_architecture_command"),
        ({"type": "change_replicas", "nodeId": "api", "replicas": -1}, "invalid_architecture"),
        ({"type": "rename_node", "nodeId": "api", "name": "Orders API"}, "architecture_unchanged"),
        (
            {"type": "add_node", "node": {"id": "x", "kind": "teleporter", "name": "X"}},
            "invalid_architecture",
        ),
        ({"type": "teleport"}, "validation_error"),
        ({"type": "change_replicas", "nodeId": "api", "replicas": 2, "extra": 1}, "validation_error"),
    ],
)
async def test_invalid_edits_create_nothing(
    client: AsyncClient, world: World, command: dict[str, Any], code: str
) -> None:
    await created(client, world)
    response = await edit(client, world, 1, command, status=422)
    assert response["error"]["code"] == code
    history = (await client.get(url(world, "/versions"), headers=world.ada)).json()
    assert len(history["versions"]) == 1


async def test_command_errors_name_the_command(client: AsyncClient, world: World) -> None:
    await created(client, world)
    response = await edit(
        client,
        world,
        1,
        {"type": "change_replicas", "nodeId": "api", "replicas": 2},
        {"type": "remove_nodes", "nodeIds": ["ghost"]},
        status=422,
    )
    assert response["error"]["details"] == {
        "index": 1,
        "command": "remove_nodes",
        "reason": "unknown_node",
        "elementId": "ghost",
    }


# --- layout and comparison -------------------------------------------------------------------------


async def test_layout_is_saved_without_a_revision(client: AsyncClient, world: World) -> None:
    await created(client, world)
    saved = await client.put(
        url(world, "/layout"), json={"positions": {"api": {"x": 10, "y": 20.5}}}, headers=world.ada
    )
    assert saved.status_code == 204
    architecture = (await client.get(url(world), headers=world.ada)).json()
    assert (architecture["version"], architecture["layout"]["positions"]) == (
        1,
        {"api": {"x": 10.0, "y": 20.5}},
    )
    ghost = await client.put(
        url(world, "/layout"), json={"positions": {"ghost": {"x": 0, "y": 0}}}, headers=world.ada
    )
    assert ghost.json()["error"] == ghost.json()["error"] | {
        "code": "invalid_architecture_layout",
        "details": {"reason": "unknown_node", "nodeId": "ghost"},
    }
    # A removed node's position is not shown with revisions that no longer have it.
    await edit(client, world, 1, {"type": "remove_nodes", "nodeIds": ["web"]})
    await client.put(url(world, "/layout"), json={"positions": {"db": {"x": 1, "y": 1}}}, headers=world.ada)
    first = (await client.get(url(world, "/versions/1"), headers=world.ada)).json()
    assert first["layout"]["positions"] == {"db": {"x": 1.0, "y": 1.0}}


async def test_compare_two_revisions(client: AsyncClient, world: World) -> None:
    await created(client, world)
    await edit(client, world, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 7})
    response = await client.get(url(world, "/compare"), params={"from": 1, "to": 2}, headers=world.ada)
    comparison = response.json()
    assert (comparison["from"]["version"], comparison["to"]["version"]) == (1, 2)
    assert (comparison["capacity"], comparison["cost"]) == (None, None)
    [change] = comparison["nodes"]
    assert change["fields"][0] == {
        "field": "configuration.replicas",
        "before": 3,
        "after": 7,
        "category": "resources",
    }
    missing = await client.get(url(world, "/compare"), params={"from": 1, "to": 9}, headers=world.ada)
    assert missing.json()["error"]["code"] == "architecture_revision_not_found"


# --- access and limits -----------------------------------------------------------------------------


async def test_who_may_read_and_change(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    await created(client, world)
    viewer = await member(client, db, outbox, world, "viewer")
    eve = await signed_in(client, outbox, "eve@example.com")
    assert (await client.get(url(world), headers=viewer)).status_code == 200
    change = {"baseVersion": 1, "commands": [{"type": "change_replicas", "nodeId": "api", "replicas": 2}]}
    assert (await client.post(url(world, "/commands"), json=change, headers=viewer)).status_code == 403
    for response in (
        await client.get(url(world), headers=eve),
        await client.get(url(world, "/versions/1"), headers=eve),
        await client.post(url(world, "/commands"), json=change, headers=eve),
        await client.put(url(world, "/layout"), json={"positions": {}}, headers=eve),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (404, "project_not_found")


async def test_archived_projects_are_read_only(client: AsyncClient, world: World) -> None:
    await created(client, world)
    await client.post(f"/api/v1/projects/{world.project_id}/archive", headers=world.ada)
    change = {"baseVersion": 1, "commands": [{"type": "change_replicas", "nodeId": "api", "replicas": 2}]}
    response = await client.post(url(world, "/commands"), json=change, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "project_archived")
    assert (await client.get(url(world), headers=world.ada)).status_code == 200


async def test_a_large_architecture_can_be_created_but_not_an_absurd_one(
    client: AsyncClient, world: World
) -> None:
    nodes = [
        {"id": f"svc-{i}", "kind": "service", "name": f"Service {i}", "description": "x" * 500}
        for i in range(400)
    ]
    ir = {"schema_version": 1, "name": "Big", "nodes": nodes}
    assert len(json.dumps({"ir": ir})) > 64 * 1024  # over the default body limit
    await created(client, world, ir)
    huge = {"schema_version": 1, "name": "Huge", "description": "x" * 3_000_000}
    response = await client.post(
        f"/api/v1/projects/{world.other_id}/architecture", json={"ir": huge}, headers=world.ada
    )
    assert (response.status_code, response.json()["error"]["code"]) == (413, "payload_too_large")
    edit_body = {
        "baseVersion": 1,
        "commands": [{"type": "rename_node", "nodeId": "svc-1", "name": "y" * 70_000}],
    }
    too_big = await client.post(url(world, "/commands"), json=edit_body, headers=world.ada)
    assert too_big.status_code == 413  # other architecture routes keep the default limit
