"""The architecture API (Milestone 5: architecture CRUD and versioning)."""

import json
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from core.architecture_ir.serialization import to_dict
from tests.unit.architecture_ir.builders import api_and_postgres, discovered, service_cache_queue

from .requirement_support import World, create, member, signed_in

pytestmark = pytest.mark.integration

IR = to_dict(api_and_postgres())
DANGLING = {"id": "c", "source_id": "api", "target_id": "ghost", "kind": "request"}


def base(world: World, project_id: str | None = None) -> str:
    return f"/api/v1/projects/{project_id or world.project_id}/architectures"


async def created(client: AsyncClient, world: World, **body: Any) -> dict[str, Any]:
    response = await client.post(
        base(world), json={"name": "Orders platform", "ir": IR} | body, headers=world.ada
    )
    assert response.status_code == 201, response.text
    architecture: dict[str, Any] = response.json()
    return architecture


def url(world: World, architecture: dict[str, Any], suffix: str = "") -> str:
    return f"{base(world)}/{architecture['id']}{suffix}"


async def commands(
    client: AsyncClient, world: World, architecture: dict[str, Any], version: int, *items: dict[str, Any]
) -> Response:
    return await client.post(
        url(world, architecture, "/commands"),
        json={"baseVersion": version, "commands": list(items)},
        headers=world.ada,
    )


async def versions(client: AsyncClient, world: World, architecture: dict[str, Any]) -> list[dict[str, Any]]:
    page: list[dict[str, Any]] = (
        await client.get(url(world, architecture, "/versions"), headers=world.ada)
    ).json()["versions"]
    return page


# --- create, read, list, metadata ------------------------------------------------------------------


async def test_create_and_read_back_exactly(client: AsyncClient, world: World) -> None:
    ir = to_dict(service_cache_queue())
    ir["requirement_refs"] = []  # the fixture cites requirements this project does not have
    for element in ir["nodes"]:
        element["requirement_refs"] = []
    architecture = await created(client, world, ir=ir, description="Main design", reason="First")
    assert (architecture["name"], architecture["description"], architecture["status"]) == (
        "Orders platform",
        "Main design",
        "active",
    )
    assert architecture["currentVersion"] == 1
    revision = architecture["revision"]
    assert (revision["version"], revision["parentVersion"], revision["reason"]) == (1, None, "First")
    assert revision["summary"] == "Created with 4 nodes and 3 connections."
    assert architecture["ir"] == ir  # the canonical document, exactly
    assert architecture["layout"] == {"positions": {}, "updatedAt": None}
    again = await client.get(url(world, architecture), headers=world.ada)
    assert again.json() == architecture


async def test_an_empty_architecture(client: AsyncClient, world: World) -> None:
    response = await client.post(base(world), json={"name": "Draft"}, headers=world.ada)
    architecture = response.json()
    assert (response.status_code, architecture["ir"]["nodes"], architecture["ir"]["name"]) == (
        201,
        [],
        "Draft",
    )


async def test_listing_paginates_filters_and_stays_in_the_project(client: AsyncClient, world: World) -> None:
    made = [await created(client, world, name=name) for name in ("Alpha", "Beta", "Gamma")]
    await client.post(base(world, world.other_id), json={"name": "Elsewhere"}, headers=world.ada)
    await client.post(url(world, made[0], "/archive"), headers=world.ada)
    first = (await client.get(base(world), params={"limit": 2}, headers=world.ada)).json()
    rest = (await client.get(base(world), params={"cursor": first["nextCursor"]}, headers=world.ada)).json()
    names = [a["name"] for a in first["architectures"] + rest["architectures"]]
    assert sorted(names) == ["Alpha", "Beta", "Gamma"]
    assert rest["nextCursor"] is None
    archived = (await client.get(base(world), params={"status": "archived"}, headers=world.ada)).json()
    assert [a["name"] for a in archived["architectures"]] == ["Alpha"]
    found = (await client.get(base(world), params={"search": "amm"}, headers=world.ada)).json()
    assert [a["name"] for a in found["architectures"]] == ["Gamma"]
    assert "ir" not in first["architectures"][0]  # listings never carry content
    bad = await client.get(base(world), params={"cursor": "bogus"}, headers=world.ada)
    assert bad.json()["error"]["code"] == "invalid_cursor"


async def test_metadata_updates_create_no_revision(client: AsyncClient, world: World) -> None:
    architecture = await created(client, world)
    other = await created(client, world, name="Other")
    response = await client.patch(
        url(world, architecture), json={"name": "Checkout", "description": "New"}, headers=world.ada
    )
    updated = response.json()
    assert (updated["name"], updated["description"], updated["currentVersion"]) == ("Checkout", "New", 1)
    assert len(await versions(client, world, architecture)) == 1
    taken = await client.patch(url(world, other), json={"name": "checkout"}, headers=world.ada)
    assert (taken.status_code, taken.json()["error"]["code"]) == (409, "architecture_name_taken")
    empty = await client.patch(url(world, other), json={}, headers=world.ada)
    assert empty.json()["error"]["code"] == "nothing_to_update"
    blank = await client.patch(url(world, other), json={"name": "  "}, headers=world.ada)
    assert blank.json()["error"] == blank.json()["error"] | {
        "code": "invalid_architecture_metadata",
        "details": {"field": "name", "reason": "length"},
    }


async def test_invalid_architectures_say_exactly_what_is_wrong(client: AsyncClient, world: World) -> None:
    ir = json.loads(json.dumps(IR))
    ir["connections"][0]["target_id"] = "ghost"
    ir["nodes"][0]["configuration"]["values"]["replicas"] = -2
    response = await client.post(base(world), json={"name": "Bad", "ir": ir}, headers=world.ada)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["violations"] == [
        {
            "element": "node",
            "elementId": "api",
            "field": "configuration.replicas",
            "rule": "out_of_range",
            "message": "replicas must be at least 0.",
        }
    ]
    newer = await client.post(
        base(world), json={"name": "Future", "ir": IR | {"schema_version": 99}}, headers=world.ada
    )
    assert newer.json()["error"]["details"]["violations"][0]["rule"] == "unsupported_schema_version"
    assert (await client.get(base(world), headers=world.ada)).json()[
        "architectures"
    ] == []  # nothing was created


# --- content revisions and history -----------------------------------------------------------------


async def test_edits_create_revisions_with_their_changes(client: AsyncClient, world: World) -> None:
    architecture = await created(client, world)
    link = {
        "id": "api-cache",
        "source_id": "api",
        "target_id": "cache",
        "kind": "data_access",
        "protocol": "redis",
    }
    response = await commands(
        client,
        world,
        architecture,
        1,
        {"type": "change_replicas", "nodeId": "api", "replicas": 6},
        {"type": "update_configuration", "nodeId": "api", "values": {"cpu_limit_cores": "1.5"}},
        {"type": "add_node", "node": {"id": "cache", "kind": "cache", "name": "Cache"}},
        {"type": "add_connection", "connection": link},
    )
    second = response.json()
    assert (
        response.status_code,
        second["created"],
        second["revision"]["version"],
        second["currentVersion"],
    ) == (
        201,
        True,
        2,
        2,
    )
    api = next(n for n in second["ir"]["nodes"] if n["id"] == "api")
    assert api["configuration"]["values"]["cpu_limit_cores"] == "1.5"
    assert api["field_provenance"]["configuration.replicas"]["source"] == "user_edit"
    assert second["changes"]["summary"] == "1 node added (Cache); 1 node modified; 1 connection added."
    history = await versions(client, world, architecture)
    assert [(v["version"], v["current"]) for v in history] == [(2, True), (1, False)]
    first = (await client.get(url(world, architecture, "/versions/1"), headers=world.ada)).json()
    assert (first["ir"], first["revision"]["version"], first["currentVersion"]) == (IR, 1, 2)


async def test_saving_whole_content(client: AsyncClient, world: World) -> None:
    architecture = await created(client, world)
    new = json.loads(json.dumps(IR))
    new["nodes"].append({"id": "q", "kind": "queue", "name": "Queue"})
    body = {"baseVersion": 1, "ir": new, "source": "import", "reason": "Imported"}
    response = await client.put(url(world, architecture, "/content"), json=body, headers=world.ada)
    saved = response.json()
    assert (response.status_code, saved["created"], saved["revision"]["source"]) == (201, True, "import")
    assert [c["elementId"] for c in saved["changes"]["nodes"]] == ["q"]
    same = await client.put(
        url(world, architecture, "/content"), json=body | {"baseVersion": 2}, headers=world.ada
    )
    assert (same.status_code, same.json()["created"], same.json()["revision"]["version"]) == (200, False, 2)
    assert same.json()["changes"]["nodes"] == []
    assert len(await versions(client, world, architecture)) == 2  # no duplicate revision


async def test_a_stale_update_is_a_conflict(client: AsyncClient, world: World) -> None:
    architecture = await created(client, world)
    await commands(
        client, world, architecture, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 2}
    )
    stale = await commands(
        client, world, architecture, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 9}
    )
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "architecture_version_conflict")
    assert stale.json()["error"]["details"] == {"latestVersion": 2}
    stale_put = await client.put(
        url(world, architecture, "/content"), json={"baseVersion": 1, "ir": IR}, headers=world.ada
    )
    assert stale_put.status_code == 409
    current = (await client.get(url(world, architecture), headers=world.ada)).json()
    assert current["revision"]["version"] == 2
    assert (
        next(n for n in current["ir"]["nodes"] if n["id"] == "api")["configuration"]["values"]["replicas"]
        == 2
    )


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ({"type": "rename_node", "nodeId": "ghost", "name": "x"}, "invalid_architecture_command"),
        ({"type": "change_replicas", "nodeId": "api", "replicas": -1}, "invalid_architecture"),
        (
            {"type": "add_node", "node": {"id": "x", "kind": "teleporter", "name": "X"}},
            "invalid_architecture",
        ),
        ({"type": "add_connection", "connection": DANGLING}, "invalid_architecture_command"),
        ({"type": "teleport"}, "validation_error"),
    ],
)
async def test_invalid_edits_create_nothing(
    client: AsyncClient, world: World, command: dict[str, Any], code: str
) -> None:
    architecture = await created(client, world)
    response = await commands(client, world, architecture, 1, command)
    assert (response.status_code, response.json()["error"]["code"]) == (422, code)
    assert len(await versions(client, world, architecture)) == 1


# --- restore ---------------------------------------------------------------------------------------


async def test_restoring_a_revision_creates_a_new_one_and_keeps_history(
    client: AsyncClient, world: World
) -> None:
    architecture = await created(client, world)
    await commands(
        client, world, architecture, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 6}
    )
    await commands(
        client,
        world,
        architecture,
        2,
        {"type": "add_node", "node": {"id": "q", "kind": "queue", "name": "Q"}},
    )
    response = await client.post(
        url(world, architecture, "/versions/1/restore"),
        json={"baseVersion": 3, "reason": "Roll back"},
        headers=world.ada,
    )
    restored = response.json()
    assert (response.status_code, restored["created"]) == (201, True)
    revision = restored["revision"]
    assert (revision["version"], revision["parentVersion"], revision["restoredFromVersion"]) == (4, 3, 1)
    assert restored["ir"] == IR
    assert revision["contentHash"] == architecture["revision"]["contentHash"]
    history = await versions(client, world, architecture)
    assert [(v["version"], v["restoredFromVersion"], v["current"]) for v in history] == [
        (4, 1, True),
        (3, None, False),
        (2, None, False),
        (1, None, False),
    ]
    stale = await client.post(
        url(world, architecture, "/versions/2/restore"), json={"baseVersion": 3}, headers=world.ada
    )
    assert stale.json()["error"]["code"] == "architecture_version_conflict"
    missing = await client.post(
        url(world, architecture, "/versions/9/restore"), json={"baseVersion": 4}, headers=world.ada
    )
    assert missing.json()["error"]["code"] == "architecture_revision_not_found"


# --- layout and comparison -------------------------------------------------------------------------


async def test_layout_is_saved_without_a_revision(client: AsyncClient, world: World) -> None:
    architecture = await created(client, world)
    saved = await client.put(
        url(world, architecture, "/layout"),
        json={"positions": {"api": {"x": 10, "y": 20.5}}},
        headers=world.ada,
    )
    assert saved.status_code == 204
    current = (await client.get(url(world, architecture), headers=world.ada)).json()
    assert (current["revision"]["version"], current["layout"]["positions"]) == (
        1,
        {"api": {"x": 10.0, "y": 20.5}},
    )
    ghost = await client.put(
        url(world, architecture, "/layout"),
        json={"positions": {"ghost": {"x": 0, "y": 0}}},
        headers=world.ada,
    )
    assert ghost.json()["error"]["details"] == {"reason": "unknown_node", "nodeId": "ghost"}


async def test_compare_two_revisions(client: AsyncClient, world: World) -> None:
    architecture = await created(client, world)
    other = await created(client, world, name="Other")
    await commands(
        client, world, architecture, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 7}
    )
    comparison = (
        await client.get(url(world, architecture, "/compare"), params={"from": 1, "to": 2}, headers=world.ada)
    ).json()
    assert (comparison["from"]["version"], comparison["to"]["version"]) == (1, 2)
    assert (comparison["capacity"], comparison["cost"]) == (None, None)
    [change] = comparison["nodes"]
    assert change["fields"][0] == {
        "field": "configuration.replicas",
        "before": 3,
        "after": 7,
        "category": "resources",
    }
    # Revision 2 exists, but not in the other architecture.
    elsewhere = await client.get(
        url(world, other, "/compare"), params={"from": 1, "to": 2}, headers=world.ada
    )
    assert elsewhere.json()["error"]["code"] == "architecture_revision_not_found"


# --- lifecycle -------------------------------------------------------------------------------------


async def test_archive_restore_and_delete(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    architecture = await created(client, world)
    early = await client.delete(url(world, architecture), headers=world.ada)
    assert (early.status_code, early.json()["error"]["code"]) == (409, "architecture_not_archived")
    archived = (await client.post(url(world, architecture, "/archive"), headers=world.ada)).json()
    assert (archived["status"], archived["archivedAt"] is not None) == ("archived", True)
    frozen = await commands(
        client, world, architecture, 1, {"type": "change_replicas", "nodeId": "api", "replicas": 2}
    )
    assert (frozen.status_code, frozen.json()["error"]["code"]) == (409, "architecture_archived")
    assert (await client.get(url(world, architecture), headers=world.ada)).status_code == 200  # readable
    restored = (await client.post(url(world, architecture, "/restore"), headers=world.ada)).json()
    assert (restored["status"], restored["archivedAt"]) == ("active", None)
    await client.post(url(world, architecture, "/archive"), headers=world.ada)
    admin_less = await member(client, db, outbox, world, "member")
    denied = await client.delete(url(world, architecture), headers=admin_less)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    assert (await client.delete(url(world, architecture), headers=world.ada)).status_code == 204
    gone = await client.get(url(world, architecture), headers=world.ada)
    assert (gone.status_code, gone.json()["error"]["code"]) == (404, "architecture_not_found")
    assert (await client.get(url(world, architecture, "/versions/1"), headers=world.ada)).status_code == 404
    await created(client, world)  # the name is free again


# --- access ----------------------------------------------------------------------------------------


async def test_who_may_read_and_change(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    architecture = await created(client, world)
    viewer = await member(client, db, outbox, world, "viewer")
    eve = await signed_in(client, outbox, "eve@example.com")
    assert (await client.get(url(world, architecture), headers=viewer)).status_code == 200
    change = {"baseVersion": 1, "commands": [{"type": "change_replicas", "nodeId": "api", "replicas": 2}]}
    for response in (
        await client.post(url(world, architecture, "/commands"), json=change, headers=viewer),
        await client.patch(url(world, architecture), json={"name": "x"}, headers=viewer),
        await client.post(
            url(world, architecture, "/versions/1/restore"), json={"baseVersion": 1}, headers=viewer
        ),
        await client.post(base(world), json={"name": "Viewer's"}, headers=viewer),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (403, "permission_denied")
    for response in (
        await client.get(url(world, architecture), headers=eve),
        await client.get(base(world), headers=eve),
        await client.get(url(world, architecture, "/versions/1"), headers=eve),
        await client.post(url(world, architecture, "/commands"), json=change, headers=eve),
        await client.put(url(world, architecture, "/layout"), json={"positions": {}}, headers=eve),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (404, "project_not_found")


async def test_an_architecture_is_only_reachable_through_its_own_project(
    client: AsyncClient, world: World
) -> None:
    """Guessing an architecture id under another (accessible) project finds nothing."""
    architecture = await created(client, world)
    through_other = f"{base(world, world.other_id)}/{architecture['id']}"
    for response in (
        await client.get(through_other, headers=world.ada),
        await client.get(f"{through_other}/versions/1", headers=world.ada),
        await client.patch(through_other, json={"name": "hijack"}, headers=world.ada),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (404, "architecture_not_found")


async def test_access_is_checked_before_the_body_is_validated(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    architecture = await created(client, world)
    eve = await signed_in(client, outbox, "eve@example.com")
    viewer = await member(client, db, outbox, world, "viewer")
    invalid_ir = {"schema_version": 1, "name": "", "nodes": [{"id": "x", "kind": "teleporter", "name": "X"}]}
    for headers, status, code in ((eve, 404, "project_not_found"), (viewer, 403, "permission_denied")):
        for response in (
            await client.post(base(world), json={"name": "X", "ir": invalid_ir}, headers=headers),
            await client.put(
                url(world, architecture, "/content"),
                json={"baseVersion": 1, "ir": invalid_ir},
                headers=headers,
            ),
        ):
            assert (response.status_code, response.json()["error"]["code"]) == (status, code)


async def test_requirement_references_are_checked(client: AsyncClient, world: World) -> None:
    requirement = await create(client, world)
    ir = json.loads(json.dumps(IR))
    ir["requirement_refs"] = [{"requirement_id": requirement["id"], "version": 1}]
    await created(client, world, ir=ir)
    other = await client.post(base(world, world.other_id), json={"name": "X", "ir": ir}, headers=world.ada)
    assert other.json()["error"]["details"]["violations"][0]["rule"] == "unknown_requirement"


async def test_an_import_keeps_unknowns_and_provenance(client: AsyncClient, world: World) -> None:
    architecture = await created(client, world, ir=to_dict(discovered()), source="import")
    assert architecture["revision"]["source"] == "import"
    db = next(n for n in architecture["ir"]["nodes"] if n["id"] == "aws_db_instance.orders")
    assert db["configuration"]["unknown"] == ["backup_retention_seconds", "max_connections"]
    assert db["provenance"]["source"] == "terraform"


async def test_large_documents_on_content_routes_only(client: AsyncClient, world: World) -> None:
    nodes = [
        {"id": f"svc-{i}", "kind": "service", "name": f"Service {i}", "description": "x" * 500}
        for i in range(400)
    ]
    big = {"schema_version": 1, "name": "Big", "nodes": nodes}
    assert len(json.dumps({"ir": big})) > 64 * 1024  # over the default body limit
    architecture = await created(client, world, name="Big", ir=big)
    replaced = await client.put(
        url(world, architecture, "/content"),
        json={"baseVersion": 1, "ir": big | {"description": "d"}},
        headers=world.ada,
    )
    assert replaced.status_code == 201
    huge = await client.post(
        base(world),
        json={"name": "Huge", "ir": {"schema_version": 1, "name": "x" * 3_000_000}},
        headers=world.ada,
    )
    assert (huge.status_code, huge.json()["error"]["code"]) == (413, "payload_too_large")
    patch = await client.patch(
        url(world, architecture), json={"description": "y" * 70_000}, headers=world.ada
    )
    assert patch.status_code == 413  # other routes keep the default limit
