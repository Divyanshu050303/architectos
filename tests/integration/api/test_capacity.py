"""The capacity API against a real database (Milestone 7, phase 7)."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord, CapacityAnalysisRecord, CapacityComponentRecord

from .requirement_support import World, member

pytestmark = pytest.mark.integration

RPS = "requests/second"
IR: dict[str, Any] = {
    "schema_version": 1,
    "name": "Shop",
    "nodes": [
        {"id": "web", "kind": "client", "name": "Web"},
        {
            "id": "api",
            "kind": "service",
            "name": "API",
            "configuration": {"values": {"replicas": 4, "throughput_per_replica_per_second": 300}},
        },
        {
            "id": "db",
            "kind": "database",
            "name": "DB",
            "configuration": {
                "values": {
                    "throughput_limit_per_second": 5000,
                    "max_connections": 100,
                    "storage_bytes": 1000000000000,
                }
            },
        },
    ],
    "connections": [
        {
            "id": "web-api",
            "source_id": "web",
            "target_id": "api",
            "kind": "request",
            "protocol": "https",
            "interaction": "synchronous",
            "configuration": {"values": {"traffic_ratio": 1}},
        },
        {
            "id": "api-db",
            "source_id": "api",
            "target_id": "db",
            "kind": "data_access",
            "protocol": "postgresql",
            "configuration": {"values": {"calls_per_request": 2, "pool_size": 10}},
        },
    ],
}
WORKLOAD = {"name": "Peak", "type": "request_response", "peakRate": {"value": 1000, "unit": RPS}}


async def architecture(client: AsyncClient, world: World) -> str:
    response = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": "Shop", "ir": IR},
        headers=world.ada,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def base(world: World, aid: str) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures/{aid}/capacity-analyses"


async def run(client: AsyncClient, world: World, aid: str, **body: Any) -> dict[str, Any]:
    response = await client.post(base(world, aid), json={"workload": WORKLOAD} | body, headers=world.ada)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_an_analysis_is_run_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid, scenarios=[{"name": "Double", "growth": 2}], label="Peak check")
    assert (created["status"], created["revision"], created["label"]) == ("completed", 1, "Peak check")
    assert created["summary"]["saturationMultiple"] == "1.2"  # api: 1200 / 1000
    assert created["summary"]["saturationComplete"] is True
    assert {x["code"] for x in created["limitations"]} >= {"catalog_unavailable", "no_measurements"}
    assert created["inputs"]["workload"]["peak_rate"] == {"value": "1000", "unit": RPS}
    [double] = created["scenarios"]
    assert {"nodeId": "api", "resource": "work_rate", "condition": "exceeds_capacity"} in double[
        "comparison"
    ]["newBottlenecks"]
    assert double["scaling"][0]["required"] == {"value": "7", "unit": "replicas"}
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=world.ada)).json() == created

    stored = await db.scalar(
        select(func.count()).where(CapacityComponentRecord.analysis_id == uuid.UUID(created["id"]))
    )
    assert stored == 2  # api and db
    audit = await db.scalar(
        select(AuditLogRecord).where(AuditLogRecord.action == "architecture.capacity_analyzed")
    )
    assert audit is not None
    assert audit.event_metadata["analysis_id"] == created["id"]


async def test_components_bottlenecks_and_scenarios(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    created = await run(
        client,
        world,
        aid,
        workload=WORKLOAD | {"peakRate": {"value": 3000, "unit": RPS}},
        scenarios=[
            {"name": "Scale out", "changes": [{"elementId": "api", "property": "replicas", "value": 10}]}
        ],
    )
    one = f"{base(world, aid)}/{created['id']}"
    components = (await client.get(f"{one}/components", headers=world.ada)).json()["components"]
    api = next(c for c in components if c["nodeId"] == "api")
    [throughput] = [u for u in api["utilization"] if u["resource"] == "work_rate"]
    assert (throughput["demand"], throughput["capacity"], throughput["ratio"]) == (
        {"value": "3000", "unit": RPS},
        {"value": "1200", "unit": RPS},
        "2.5",
    )
    db = next(c for c in components if c["nodeId"] == "db")
    assert {e["resource"]: e["quantity"] for e in db["resources"]}["connections"] == {
        "value": "40",
        "unit": "connections",
    }
    bottlenecks = (await client.get(f"{one}/bottlenecks", headers=world.ada)).json()["bottlenecks"]
    assert [(b["nodeId"], b["certainty"]) for b in bottlenecks] == [
        ("api", "modeled"),
        ("db", "modeled"),  # 2.5, then 6000 / 5000 = 1.2
    ]
    modeled = (
        await client.get(f"{one}/bottlenecks", params={"certainty": "candidate"}, headers=world.ada)
    ).json()
    assert modeled["bottlenecks"] == []
    [scale] = (await client.get(f"{one}/scenarios", headers=world.ada)).json()["scenarios"]
    assert {"nodeId": "api", "resource": "work_rate", "condition": "exceeds_capacity"} in scale["comparison"][
        "resolvedBottlenecks"
    ]
    page = (await client.get(f"{one}/components", params={"limit": 1}, headers=world.ada)).json()
    rest = (
        await client.get(f"{one}/components", params={"cursor": page["nextCursor"]}, headers=world.ada)
    ).json()
    assert [c["nodeId"] for c in page["components"] + rest["components"]] == ["api", "db"]


async def test_unknowns_stay_unknown(client: AsyncClient, world: World) -> None:
    ir = IR | {
        "connections": [IR["connections"][0], IR["connections"][1] | {"configuration": {"values": {}}}]
    }
    response = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": "Undeclared", "ir": ir},
        headers=world.ada,
    )
    aid = response.json()["id"]
    created = await run(client, world, aid)
    assert ("api-db", "routing_unspecified") in {(u["elementId"], u["code"]) for u in created["unsupported"]}
    assert created["summary"]["saturationComplete"] is False
    components = (
        await client.get(f"{base(world, aid)}/{created['id']}/components", headers=world.ada)
    ).json()["components"]
    db = next(c for c in components if c["nodeId"] == "db")
    [throughput] = [u for u in db["utilization"] if u["resource"] == "work_rate"]
    assert (throughput["demand"], throughput["state"]) == (None, "unknown")


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"workload": WORKLOAD | {"peakRate": {"value": -5, "unit": RPS}}}, 422, "invalid_capacity_quantity"),
        (
            {"workload": WORKLOAD | {"peakRate": {"value": 5, "unit": "rps"}}},
            422,
            "invalid_capacity_quantity",
        ),
        ({"workload": WORKLOAD | {"readRatio": "1.5"}}, 422, "invalid_workload_profile"),
        ({"workload": {"name": "B", "type": "batch", "batchSize": 5}}, 422, "invalid_workload_profile"),
        ({"workload": WORKLOAD, "models": ["no-such-model"]}, 422, "invalid_capacity_config"),
        ({"workload": WORKLOAD, "entries": ["ghost"]}, 422, "invalid_capacity_config"),
        ({"workload": WORKLOAD, "scenarios": [{"name": "x", "growth": 0}]}, 422, "invalid_capacity_scenario"),
        (
            {
                "workload": WORKLOAD,
                "scenarios": [
                    {"name": "x", "changes": [{"elementId": "api", "property": "replicas", "value": 2.5}]}
                ],
            },
            422,
            "invalid_capacity_scenario",
        ),
        ({"workload": WORKLOAD, "revision": 9}, 404, "architecture_revision_not_found"),
        ({"workload": WORKLOAD, "status": "completed"}, 422, "validation_error"),
        ({}, 422, "validation_error"),
    ],
)
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid = await architecture(client, world)
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(CapacityAnalysisRecord)) == 0


async def test_access_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=viewer)).status_code == 200
    denied = await client.post(base(world, aid), json={"workload": WORKLOAD}, headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    other = f"/api/v1/projects/{world.other_id}/architectures/{aid}/capacity-analyses/{created['id']}"
    assert (await client.get(other, headers=world.ada)).json()["error"]["code"] == "architecture_not_found"
    missing = f"{base(world, aid)}/{uuid.uuid4()}"
    assert (await client.get(missing, headers=world.ada)).json()["error"][
        "code"
    ] == "capacity_analysis_not_found"
    second = await run(client, world, aid)
    listed = (await client.get(base(world, aid), params={"limit": 1}, headers=world.ada)).json()
    rest = (
        await client.get(base(world, aid), params={"cursor": listed["nextCursor"]}, headers=world.ada)
    ).json()
    assert [a["id"] for a in listed["analyses"] + rest["analyses"]] == [second["id"], created["id"]]


async def test_the_model_catalog(client: AsyncClient, world: World) -> None:
    models = (await client.get("/api/v1/capacity/models", headers=world.ada)).json()["models"]
    replica = next(m for m in models if m["id"] == "replica-throughput")
    assert replica["configuration"] == ["throughput_per_replica_per_second", "replicas"]
    assert any("linearly" in x for x in replica["limitations"])
    assert (await client.get("/api/v1/capacity/models")).status_code == 401


async def test_analyses_are_append_only_and_reads_cost_the_same(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    from .test_query_budget import counting  # noqa: PLC0415 - shared helper of the budget tests

    aid = await architecture(client, world)
    small = await run(client, world, aid)
    large = await run(client, world, aid, scenarios=[{"name": f"s{i}", "growth": i + 1} for i in range(10)])
    for statement in (
        "UPDATE capacity_analyses SET label = 'x' WHERE id = :id",
        "DELETE FROM capacity_components WHERE analysis_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": small["id"]})

    async def cost(analysis_id: str) -> list[int]:
        counts = []
        for suffix in ("", "/components", "/bottlenecks", "/scenarios"):
            with counting(connection) as statements:
                assert (
                    await client.get(f"{base(world, aid)}/{analysis_id}{suffix}", headers=world.ada)
                ).status_code == 200
            counts.append(len(statements))
        return counts

    assert await cost(small["id"]) == await cost(large["id"])
