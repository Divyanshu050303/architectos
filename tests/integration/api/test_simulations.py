"""The simulations API against a real database (Milestone 12, phase 10)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import AuditLogRecord, SimulationDeltaRecord, SimulationRecord

from .requirement_support import World, member, signed_in
from .test_cost import DAY, GHOST, WORKLOAD, prepared

pytestmark = pytest.mark.integration

DOUBLE = {"name": "Double", "workload": {"growth": "2"}}
OUTAGE = {"name": "Api down", "failures": [{"kind": "component", "target": "api"}]}


def base(world: World, aid: str) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures/{aid}/simulations"


async def run(client: AsyncClient, world: World, aid: str, **body: Any) -> dict[str, Any]:
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def architecture_ir(client: AsyncClient, world: World, aid: str) -> Any:
    response = await client.get(f"/api/v1/projects/{world.project_id}/architectures/{aid}", headers=world.ada)
    return response.json()


async def test_a_simulation_is_run_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid, snapshot = await prepared(client, world)
    before = await architecture_ir(client, world, aid)
    created = await run(
        client,
        world,
        aid,
        scenario=DOUBLE,
        workload=WORKLOAD,
        pricing={"snapshotId": snapshot, "pricingDate": DAY},
        assumptions=[{"key": "peak", "statement": "Peak lasts two hours."}],
        label="Launch",
    )
    assert (created["revision"], created["label"]) == (1, "Launch")
    runs = {r["analysis"]: r for r in created["runs"]}
    assert set(runs) == {"capacity", "cost"}
    assert runs["capacity"]["state"] == "completed"
    assert runs["cost"]["state"] in {"completed", "partial"}
    assert runs["cost"]["baselineFingerprint"] != runs["cost"]["scenarioFingerprint"]
    assert not {"score", "percentage", "grade"} & set(created["summary"])
    assert {x["code"] for x in created["limitations"]} >= {"model_based", "no_defaults"}
    assert created["inputs"]["scenario"]["name"] == "Double"
    assert created["inputs"]["provider"] == "aws"
    url = f"{base(world, aid)}/{created['id']}"
    assert (await client.get(url, headers=world.ada)).json() == created

    deltas = (await client.get(f"{url}/deltas", params={"limit": 500}, headers=world.ada)).json()["deltas"]
    monthly = next(
        d
        for d in deltas
        if (d["analysis"], d["elementId"], d["metric"]) == ("cost", "system", "monthly_cost")
    )
    assert monthly["baseline"] is not None
    assert monthly["scenario"] is not None
    assert monthly["unit"] == "USD/month"
    for d in deltas:  # a difference only where both sides are known
        assert (d["difference"] is None) or (d["baseline"] is not None and d["scenario"] is not None)
    stored = await db.scalar(
        select(func.count()).where(SimulationDeltaRecord.simulation_id == uuid.UUID(created["id"]))
    )
    assert stored == len(deltas)
    audit = await db.scalar(select(AuditLogRecord).where(AuditLogRecord.action == "architecture.simulated"))
    assert audit is not None
    assert audit.event_metadata["simulation_id"] == created["id"]
    assert await architecture_ir(client, world, aid) == before  # the architecture is never changed


async def test_a_failure_scenario(client: AsyncClient, world: World) -> None:
    aid, _ = await prepared(client, world)
    created = await run(client, world, aid, scenario=OUTAGE)
    [reliability] = created["runs"]
    assert reliability["analysis"] == "reliability"
    assert created["entries"]
    assert {e["impact"] for e in created["entries"]} <= {
        "interrupted", "degraded", "tolerated", "unknown", "unaffected",
    }  # fmt: skip
    url = f"{base(world, aid)}/{created['id']}/components"
    down = (await client.get(url, params={"unavailable": "true"}, headers=world.ada)).json()
    assert [c["nodeId"] for c in down["components"]] == ["api"]
    assert created["overlay"]["unavailable_nodes"] == ["api"]


async def test_missing_inputs_are_unsupported_not_estimated(client: AsyncClient, world: World) -> None:
    aid, _ = await prepared(client, world)
    created = await run(client, world, aid, scenario=DOUBLE)
    assert created["status"] == "unsupported"
    assert {r["analysis"]: r["reason"] for r in created["runs"]} == {
        "capacity": "no_workload",
        "cost": "no_pricing",
    }
    url = f"{base(world, aid)}/{created['id']}/deltas"
    assert (await client.get(url, headers=world.ada)).json()["deltas"] == []


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"scenario": {"name": "Ghost", "failures": [{"kind": "component", "target": "ghost"}]}}, 422,
         "invalid_simulation_request"),
        ({"scenario": {"name": "Ghost", "changes": [GHOST]}}, 422, "invalid_simulation_request"),
        ({"scenario": {"name": "Empty"}}, 422, "invalid_simulation_request"),
        ({"scenario": DOUBLE, "pricing": {"snapshotId": str(uuid.uuid4())}}, 404,
         "pricing_snapshot_not_found"),
        ({"scenario": DOUBLE, "revision": 9}, 404, "architecture_revision_not_found"),
        ({"scenario": DOUBLE, "status": "completed"}, 422, "validation_error"),
        ({"scenario": DOUBLE, "telemetry": {"p99": 3}}, 422, "validation_error"),
        ({}, 422, "validation_error"),
    ],
)  # fmt: skip
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid, _ = await prepared(client, world)
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(SimulationRecord)) == 0


async def test_simulations_are_compared_on_a_common_baseline(client: AsyncClient, world: World) -> None:
    aid, _ = await prepared(client, world)
    double = await run(client, world, aid, scenario=DOUBLE, workload=WORKLOAD)
    triple = await run(
        client, world, aid, scenario={"name": "Triple", "workload": {"growth": "3"}}, workload=WORKLOAD
    )
    url = f"{base(world, aid)}/{double['id']}/comparisons/{triple['id']}"
    comparison = (await client.get(url, headers=world.ada)).json()
    assert {a["analysis"]: a["comparable"] for a in comparison["analyses"]}["capacity"] is True
    assert comparison["deltas"]
    heavier = await run(
        client,
        world,
        aid,
        scenario=DOUBLE,
        workload=WORKLOAD | {"peakRate": {"value": 500, "unit": "requests/second"}},
    )
    url = f"{base(world, aid)}/{double['id']}/comparisons/{heavier['id']}"
    different = (await client.get(url, headers=world.ada)).json()
    capacity = next(a for a in different["analyses"] if a["analysis"] == "capacity")
    assert (capacity["comparable"], capacity["reason"]) == (False, "different_baseline")


async def test_access_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid, _ = await prepared(client, world)
    created = await run(client, world, aid, scenario=OUTAGE)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=viewer)).status_code == 200
    denied = await client.post(base(world, aid), json={"scenario": OUTAGE}, headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for method, url in (("get", f"{base(world, aid)}/{created['id']}/deltas"), ("post", base(world, aid))):
        response = await client.request(method, url, json={"scenario": OUTAGE}, headers=stranger)
        assert response.json()["error"]["code"] == "project_not_found"
    other = f"/api/v1/projects/{world.other_id}/architectures/{aid}/simulations/{created['id']}"
    assert (await client.get(other, headers=world.ada)).json()["error"]["code"] == "architecture_not_found"
    missing = await client.get(f"{base(world, aid)}/{uuid.uuid4()}", headers=world.ada)
    assert missing.json()["error"]["code"] == "simulation_not_found"
    second = await run(client, world, aid, scenario=OUTAGE)
    listed = (await client.get(base(world, aid), params={"limit": 1}, headers=world.ada)).json()
    rest = (
        await client.get(base(world, aid), params={"cursor": listed["nextCursor"]}, headers=world.ada)
    ).json()
    assert [s["id"] for s in listed["simulations"] + rest["simulations"]] == [second["id"], created["id"]]


async def test_an_engine_failure_is_stored_as_failed(app: FastAPI, client: AsyncClient, world: World) -> None:
    aid, _ = await prepared(client, world)

    class Broken:
        def simulate(self, *args: Any) -> Any:
            raise RuntimeError("internal detail sk_live_do_not_leak")

        def catalog(self) -> dict[str, Any]:
            return {}

    app.state.simulation_engine = Broken()
    created = await run(client, world, aid, scenario=OUTAGE)
    assert (created["status"], created["error"]["code"], created["summary"]) == (
        "failed",
        "engine_error",
        None,
    )
    assert "internal" not in str(created)


async def test_the_catalog(client: AsyncClient, world: World) -> None:
    catalog = (await client.get("/api/v1/simulation/catalog", headers=world.ada)).json()
    assert len(catalog["scenarioTypes"]) == 9
    assert catalog["limits"] == {
        "maxChanges": 50, "maxFailures": 50, "maxAffectedComponents": 1000, "maxDeltas": 20000,
    }  # fmt: skip
    assert (await client.get("/api/v1/simulation/catalog")).status_code == 401


async def test_simulations_are_append_only_and_reads_cost_the_same(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    from .test_query_budget import counting  # noqa: PLC0415 - shared helper of the budget tests

    aid, _ = await prepared(client, world)
    small = await run(client, world, aid, scenario=OUTAGE)
    large = await run(client, world, aid, scenario=DOUBLE, workload=WORKLOAD)
    for statement, target in (
        ("UPDATE simulations SET label = 'x' WHERE id = :id", large),
        ("DELETE FROM simulation_deltas WHERE simulation_id = :id", large),
        ("UPDATE simulation_components SET unavailable = false WHERE simulation_id = :id", small),
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": target["id"]})

    async def cost(simulation_id: str) -> list[int]:
        counts = []
        for suffix in ("", "/components", "/deltas"):
            with counting(connection) as statements:
                url = f"{base(world, aid)}/{simulation_id}{suffix}"
                assert (await client.get(url, headers=world.ada)).status_code == 200
            counts.append(len(statements))
        return counts

    assert await cost(small["id"]) == await cost(large["id"])
