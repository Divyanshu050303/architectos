"""The reliability API against a real database (Milestone 9, phase 8)."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.email.transport import InMemoryTransport
from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import to_dict
from persistence.models import (
    AuditLogRecord,
    ReliabilityAnalysisRecord,
    ReliabilityComponentRecord,
    ReliabilityFindingRecord,
)
from tests.unit.architecture_ir.builders import connection, node

from .requirement_support import World, member, signed_in

pytestmark = pytest.mark.integration

SYNC: dict[str, Any] = {
    "kind": ConnectionKind.REQUEST,
    "protocol": "https",
    "interaction": Interaction.SYNCHRONOUS,
}
REDUNDANT: dict[str, Any] = {
    "replicas": 3,
    "min_healthy_replicas": 2,
    "replica_availability": Decimal("0.99"),
    "failure_independence": "independent",
    "failover_mode": "automatic",
    "failover_seconds": 30,
    "availability_zones": ("a", "b", "c"),
}
IR = ArchitectureIR(
    "Shop",
    nodes=(
        node("web", NodeKind.CLIENT),
        node("api", configuration=Configuration(REDUNDANT)),
        node(
            "db",
            NodeKind.DATABASE,
            configuration=Configuration(
                {"replicas": 1, "mtbf_seconds": 99, "mttr_seconds": 1, "backup_interval_seconds": 3600}
            ),
        ),
    ),
    connections=(
        connection("web-api", "web", "api", **SYNC),
        connection(
            "api-db", "api", "db", **(SYNC | {"kind": ConnectionKind.DATA_ACCESS, "protocol": "postgresql"})
        ),
    ),
)
INVALID = "invalid_reliability_request"
GIGABYTES = {"value": 5, "unit": "GB"}
OBJECTIVES = [
    {"key": "slo", "kind": "availability", "target": "0.98"},
    {"key": "rto", "kind": "recovery_time", "duration": {"value": 5, "unit": "min"}},
]


async def architecture(client: AsyncClient, world: World) -> str:
    response = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": "Shop", "ir": to_dict(IR)},
        headers=world.ada,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def base(world: World, aid: str) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures/{aid}/reliability-analyses"


async def run(client: AsyncClient, world: World, aid: str, **body: Any) -> dict[str, Any]:
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_an_analysis_is_run_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid, objectives=OBJECTIVES, label="Launch")
    assert (created["status"], created["revision"], created["label"]) == ("completed", 1, "Launch")
    [path] = created["paths"]
    # api 3 replicas, 2 needed, 0.99 each: 0.999702; db 99/(99+1) = 0.99
    assert (path["availability"]["quantity"], path["complete"]) == (
        {"value": "0.98970498", "unit": "ratio"},
        True,
    )
    assert path["nodeIds"] == ["web", "api", "db"]
    verdicts = {o["key"]: o["verdict"] for o in created["objectives"]}
    assert verdicts == {"slo": "satisfied", "rto": "satisfied"}  # api fails over in 30 s, db repairs in 1 s
    assert created["summary"]["pathsEstimated"] == 1
    assert {x["code"] for x in created["limitations"]} >= {"not_measured", "no_catalog"}
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=world.ada)).json() == created

    components = (
        await client.get(f"{base(world, aid)}/{created['id']}/components", headers=world.ada)
    ).json()
    api = next(c for c in components["components"] if c["nodeId"] == "api")
    availability = next(e for e in api["estimates"] if e["resource"] == "availability")
    assert (availability["quantity"]["value"], availability["modelId"]) == ("0.999702", "replicas")
    findings = (await client.get(f"{base(world, aid)}/{created['id']}/findings", headers=world.ada)).json()[
        "findings"
    ]
    spof = next(f for f in findings if f["type"] == "single_point_of_failure")
    assert (spof["nodeIds"], spof["severity"], spof["certainty"]) == (["db"], "high", "modeled")
    assert spof["id"].startswith("rel_")

    analysis_id = uuid.UUID(created["id"])
    assert (
        await db.scalar(select(func.count()).where(ReliabilityComponentRecord.analysis_id == analysis_id))
        == 2
    )
    assert await db.scalar(
        select(func.count()).where(ReliabilityFindingRecord.analysis_id == analysis_id)
    ) == len(findings)
    audit = await db.scalar(
        select(AuditLogRecord).where(AuditLogRecord.action == "architecture.reliability_analyzed")
    )
    assert audit is not None
    assert audit.event_metadata["analysis_id"] == created["id"]


async def test_findings_are_filtered_and_paged(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    created = await run(
        client, world, aid, objectives=[{"key": "slo", "kind": "availability", "target": "0.999"}]
    )
    url = f"{base(world, aid)}/{created['id']}/findings"
    everything = (await client.get(url, headers=world.ada)).json()["findings"]
    first = (await client.get(url, params={"limit": 1}, headers=world.ada)).json()
    rest = (await client.get(url, params={"cursor": first["nextCursor"]}, headers=world.ada)).json()
    assert first["findings"] + rest["findings"] == everything
    below = (await client.get(url, params={"type": "availability_below_objective"}, headers=world.ada)).json()
    assert [(f["objective"], f["nodeIds"]) for f in below["findings"]] == [("slo", ["web"])]


async def test_a_partial_result_when_inputs_are_missing(client: AsyncClient, world: World) -> None:
    thin = ArchitectureIR(
        "Thin",
        nodes=(node("web", NodeKind.CLIENT), node("api")),
        connections=(connection("web-api", "web", "api", **SYNC),),
    )
    response = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": "Thin", "ir": to_dict(thin)},
        headers=world.ada,
    )
    aid = response.json()["id"]
    created = await run(client, world, aid, objectives=[OBJECTIVES[0]])
    assert created["status"] == "insufficient_input"
    [path] = created["paths"]
    assert (path["availability"]["quantity"], path["availability"]["missing"]) == (None, ["api.availability"])
    assert created["objectives"][0]["verdict"] == "not_verifiable"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"entries": ["ghost"]}, 422, "invalid_reliability_request"),
        ({"objectives": [{"key": "a", "kind": "availability", "target": "1.5"}]}, 422, INVALID),
        ({"objectives": [{"key": "a", "kind": "uptime_score", "target": "1"}]}, 422, "validation_error"),
        ({"objectives": [{"key": "r", "kind": "recovery_time", "duration": GIGABYTES}]}, 422, INVALID),
        ({"revision": 9}, 404, "architecture_revision_not_found"),
        ({"status": "completed"}, 422, "validation_error"),
    ],
)  # fmt: skip
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid = await architecture(client, world)
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(ReliabilityAnalysisRecord)) == 0


async def test_access_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=viewer)).status_code == 200
    denied = await client.post(base(world, aid), json={}, headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for method, url in (("get", f"{base(world, aid)}/{created['id']}/findings"), ("post", base(world, aid))):
        response = await client.request(method, url, json={}, headers=stranger)
        assert response.json()["error"]["code"] == "project_not_found"
    other = f"/api/v1/projects/{world.other_id}/architectures/{aid}/reliability-analyses/{created['id']}"
    assert (await client.get(other, headers=world.ada)).json()["error"]["code"] == "architecture_not_found"
    missing = f"{base(world, aid)}/{uuid.uuid4()}"
    assert (await client.get(missing, headers=world.ada)).json()["error"][
        "code"
    ] == "reliability_analysis_not_found"
    second = await run(client, world, aid)
    listed = (await client.get(base(world, aid), params={"limit": 1}, headers=world.ada)).json()
    rest = (
        await client.get(base(world, aid), params={"cursor": listed["nextCursor"]}, headers=world.ada)
    ).json()
    assert [a["id"] for a in listed["analyses"] + rest["analyses"]] == [second["id"], created["id"]]


async def test_an_engine_failure_is_stored_as_failed(app: FastAPI, client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)

    class Broken:
        def analyze(self, *args: Any) -> Any:
            raise RuntimeError("internal detail")

        def models(self) -> tuple[()]:
            return ()

    app.state.reliability_engine = Broken()
    created = await run(client, world, aid)
    assert (created["status"], created["error"]["code"], created["summary"]) == (
        "failed",
        "engine_error",
        None,
    )
    assert "internal" not in str(created)


async def test_the_model_catalog(client: AsyncClient, world: World) -> None:
    models = (await client.get("/api/v1/reliability/models", headers=world.ada)).json()["models"]
    mtbf = next(m for m in models if m["id"] == "mtbf-mttr")
    assert (mtbf["type"], mtbf["formula"]) == ("model", "mtbf_seconds / (mtbf_seconds + mttr_seconds)")
    assert next(m["id"] for m in models if m["type"] == "step") == "request-paths"
    assert (await client.get("/api/v1/reliability/models")).status_code == 401


async def test_analyses_are_append_only_and_reads_cost_the_same(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    from .test_query_budget import counting  # noqa: PLC0415 - shared helper of the budget tests

    aid = await architecture(client, world)
    small = await run(client, world, aid)
    large = await run(
        client,
        world,
        aid,
        objectives=[{"key": f"o{i}", "kind": "availability", "target": "0.9"} for i in range(40)],
    )
    for statement in (
        "UPDATE reliability_analyses SET label = 'x' WHERE id = :id",
        "DELETE FROM reliability_findings WHERE analysis_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": small["id"]})

    async def cost(analysis_id: str) -> list[int]:
        counts = []
        for suffix in ("", "/components", "/findings"):
            with counting(connection) as statements:
                assert (
                    await client.get(f"{base(world, aid)}/{analysis_id}{suffix}", headers=world.ada)
                ).status_code == 200
            counts.append(len(statements))
        return counts

    assert await cost(small["id"]) == await cost(large["id"])
