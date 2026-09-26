"""The cost API against a real database (Milestone 8, phase 8)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.email.transport import InMemoryTransport
from core.architecture_ir.serialization import to_dict
from persistence.models import AuditLogRecord, CostAnalysisRecord, CostLineItemRecord
from tests.unit.cost.test_cost_capacity import IR, PRICES

from .requirement_support import World, member, signed_in

pytestmark = pytest.mark.integration

WORKLOAD = {
    "name": "Peak",
    "type": "request_response",
    "peakRate": {"value": 100, "unit": "requests/second"},
    "averageRate": {"value": 25, "unit": "requests/second"},
    "requestPayload": {"value": 2000, "unit": "B"},
    "responsePayload": {"value": 10000, "unit": "B"},
    "readRatio": "0.8",
}
DAY = "2026-09-26"
GHOST = {"elementId": "ghost", "property": "replicas", "value": 2}


async def prepared(client: AsyncClient, world: World, *, provider: str | None = "aws") -> tuple[str, str]:
    """An architecture with pricing mappings, a price list, and the project on ``provider``."""
    settings = {"cloudProvider": provider, "currency": "USD"}
    patched = await client.patch(
        f"/api/v1/projects/{world.project_id}", json={"settings": settings}, headers=world.ada
    )
    assert patched.status_code == 200, patched.text
    created = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures",
        json={"name": "Shop", "ir": to_dict(IR)},
        headers=world.ada,
    )
    assert created.status_code == 201, created.text
    snapshot = await client.post(
        f"/api/v1/organizations/{world.org_id}/pricing-snapshots",
        json={"name": "EU prices", "records": [r.to_dict() for r in PRICES]},
        headers=world.ada,
    )
    assert snapshot.status_code == 201, snapshot.text
    return str(created.json()["id"]), str(snapshot.json()["id"])


def base(world: World, aid: str) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures/{aid}/cost-analyses"


async def capacity(client: AsyncClient, world: World, aid: str) -> str:
    response = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures/{aid}/capacity-analyses",
        json={"workload": WORKLOAD},
        headers=world.ada,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def run(client: AsyncClient, world: World, aid: str, snapshot: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        base(world, aid), json={"snapshotId": snapshot, "pricingDate": DAY} | body, headers=world.ada
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_an_analysis_is_run_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid, snapshot = await prepared(client, world)
    cid = await capacity(client, world, aid)
    created = await run(
        client, world, aid, snapshot, capacityAnalysisId=cid, replicasFromCapacity=True, label="Launch",
        scenarios=[{"name": "Double", "growth": 2}, {"name": "Half-day", "operatingHoursPerMonth": 365}],
    )  # fmt: skip
    assert (created["status"], created["revision"], created["label"], created["currency"]) == (
        "completed", 1, "Launch", "USD",
    )  # fmt: skip
    totals = created["totals"]
    assert (totals["monthly"]["amount"], totals["monthly"]["display"], totals["complete"]) == (
        "253.8229872", "253.82", True,
    )  # fmt: skip
    assert totals["annual"]["amount"] == "3045.8758464"
    summary = created["summary"]
    assert summary["knownTotalIsLowerBound"] is False
    assert summary["drivers"]["largestComponent"]["key"] == "api"
    assert {s["key"] for s in summary["bySensitivity"]} == {"stepwise", "linear"}
    assert {x["code"] for x in created["limitations"]} >= {"estimate_not_invoice", "capacity_usage"}
    double, half = created["scenarios"]
    assert double["comparison"]["percentage"] == "100"
    assert half["comparison"]["changedAssumptions"] == [
        {"label": "operating_hours_per_month", "value": "365"}
    ]
    assert created["inputs"]["capacity_analysis_id"] == cid
    assert created["inputs"]["provider"] == "aws"
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=world.ada)).json() == created

    lines = (await client.get(f"{base(world, aid)}/{created['id']}/line-items", headers=world.ada)).json()
    cdn = next(x for x in lines["lineItems"] if x["elementId"] == "cdn")
    assert (cdn["quantity"], cdn["monthly"]["amount"], cdn["price"]["recordId"]) == ("657", "55.845", "cf")
    assert {"label": "capacity_analysis", "value": cid} in cdn["assumptions"]
    stored = await db.scalar(
        select(func.count()).where(CostLineItemRecord.analysis_id == uuid.UUID(created["id"]))
    )
    assert stored == 5
    audit = await db.scalar(
        select(AuditLogRecord).where(AuditLogRecord.action == "architecture.cost_analyzed")
    )
    assert audit is not None
    assert audit.event_metadata["analysis_id"] == created["id"]
    assert "55.845" not in str(audit.event_metadata)


async def test_a_partial_result_keeps_unknowns_apart(client: AsyncClient, world: World) -> None:
    aid, snapshot = await prepared(client, world)
    created = await run(client, world, aid, snapshot)
    assert created["status"] == "partial"
    assert (created["totals"]["monthly"]["amount"], created["totals"]["unknownItems"]) == ("82.568", 3)
    unknown = {(u["elementId"], tuple(u["missing"])) for u in created["summary"]["unknown"]}
    assert ("bus", ("capacity.requests_per_month",)) in unknown
    assert created["summary"]["knownTotalIsLowerBound"] is True
    page = await client.get(
        f"{base(world, aid)}/{created['id']}/line-items",
        params={"status": "unknown", "limit": 2},
        headers=world.ada,
    )
    rest = await client.get(
        f"{base(world, aid)}/{created['id']}/line-items",
        params={"status": "unknown", "cursor": page.json()["nextCursor"]},
        headers=world.ada,
    )
    items = page.json()["lineItems"] + rest.json()["lineItems"]
    assert [(x["elementId"], x["monthly"]) for x in items] == [("bus", None), ("cdn", None), ("logs", None)]


async def test_missing_pricing_and_no_provider(client: AsyncClient, world: World) -> None:
    aid, snapshot = await prepared(client, world, provider=None)
    created = await run(client, world, aid, snapshot)
    assert created["status"] == "insufficient_pricing"
    assert created["totals"]["monthly"]["amount"] == "0"
    assert "no_provider" in {x["code"] for x in created["limitations"]}


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"snapshotId": str(uuid.uuid4())}, 404, "pricing_snapshot_not_found"),
        ({"capacityAnalysisId": str(uuid.uuid4())}, 404, "capacity_analysis_not_found"),
        ({"revision": 9}, 404, "architecture_revision_not_found"),
        ({"operatingHoursPerMonth": 731}, 422, "invalid_cost_request"),
        ({"currency": "usd"}, 422, "invalid_cost_request"),
        ({"replicasFromCapacity": True}, 422, "invalid_cost_request"),
        ({"scenarios": [{"name": "Double", "growth": 2}]}, 422, "invalid_cost_request"),
        ({"scenarios": [{"name": "Bad", "changes": [GHOST]}]}, 422, "invalid_capacity_scenario"),
        ({"status": "completed"}, 422, "validation_error"),
        ({"snapshotId": None}, 422, "validation_error"),
    ],
)  # fmt: skip
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid, snapshot = await prepared(client, world)
    response = await client.post(
        base(world, aid), json={"snapshotId": snapshot, "pricingDate": DAY} | body, headers=world.ada
    )
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(CostAnalysisRecord)) == 0


async def test_a_capacity_analysis_of_another_revision_is_refused(client: AsyncClient, world: World) -> None:
    aid, snapshot = await prepared(client, world)
    cid = await capacity(client, world, aid)
    current = await client.get(f"/api/v1/projects/{world.project_id}/architectures/{aid}", headers=world.ada)
    renamed = to_dict(IR) | {"name": "Shop v2"}
    saved = await client.put(
        f"/api/v1/projects/{world.project_id}/architectures/{aid}/content",
        json={"baseVersion": current.json()["currentVersion"], "ir": renamed},
        headers=world.ada,
    )
    assert saved.status_code == 201, saved.text
    response = await client.post(
        base(world, aid),
        json={"snapshotId": snapshot, "pricingDate": DAY, "capacityAnalysisId": cid},
        headers=world.ada,
    )
    error = response.json()["error"]
    assert (response.status_code, error["code"], error["details"]) == (
        422, "incompatible_capacity_analysis", {"reason": "revision"},
    )  # fmt: skip


async def test_access_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid, snapshot = await prepared(client, world)
    created = await run(client, world, aid, snapshot)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=viewer)).status_code == 200
    denied = await client.post(base(world, aid), json={"snapshotId": snapshot}, headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for method, url in (("get", f"{base(world, aid)}/{created['id']}"), ("post", base(world, aid))):
        response = await client.request(method, url, json={"snapshotId": snapshot}, headers=stranger)
        assert response.json()["error"]["code"] == "project_not_found"
    other = f"/api/v1/projects/{world.other_id}/architectures/{aid}/cost-analyses/{created['id']}"
    assert (await client.get(other, headers=world.ada)).json()["error"]["code"] == "architecture_not_found"
    missing = f"{base(world, aid)}/{uuid.uuid4()}"
    assert (await client.get(missing, headers=world.ada)).json()["error"]["code"] == "cost_analysis_not_found"
    second = await run(client, world, aid, snapshot)
    listed = (await client.get(base(world, aid), params={"limit": 1}, headers=world.ada)).json()
    rest = (
        await client.get(base(world, aid), params={"cursor": listed["nextCursor"]}, headers=world.ada)
    ).json()
    assert [a["id"] for a in listed["analyses"] + rest["analyses"]] == [second["id"], created["id"]]


async def test_an_engine_failure_is_stored_as_failed(app: FastAPI, client: AsyncClient, world: World) -> None:
    aid, snapshot = await prepared(client, world)

    class Broken:
        def analyze(self, *args: Any) -> Any:
            raise RuntimeError("internal detail")

        def models(self) -> tuple[()]:
            return ()

    app.state.cost_engine = Broken()
    created = await run(client, world, aid, snapshot)
    assert (created["status"], created["error"]["code"], created["totals"]) == (
        "failed",
        "engine_error",
        None,
    )
    assert "internal" not in str(created)


async def test_the_model_catalog(client: AsyncClient, world: World) -> None:
    models = (await client.get("/api/v1/cost/models", headers=world.ada)).json()["models"]
    compute = next(m for m in models if m["id"] == "compute.instances")
    assert compute["usage"] == ["capacity.requests_per_month"]
    assert (await client.get("/api/v1/cost/models")).status_code == 401


async def test_analyses_are_append_only_and_reads_cost_the_same(
    client: AsyncClient, db: AsyncSession, connection: AsyncConnection, world: World
) -> None:
    from .test_query_budget import counting  # noqa: PLC0415 - shared helper of the budget tests

    aid, snapshot = await prepared(client, world)
    cid = await capacity(client, world, aid)
    small = await run(client, world, aid, snapshot)
    large = await run(
        client, world, aid, snapshot, capacityAnalysisId=cid,
        scenarios=[{"name": f"s{i}", "growth": i + 1} for i in range(10)],
    )  # fmt: skip
    for statement in (
        "UPDATE cost_analyses SET label = 'x' WHERE id = :id",
        "DELETE FROM cost_line_items WHERE analysis_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": small["id"]})

    async def cost(analysis_id: str) -> list[int]:
        counts = []
        for suffix in ("", "/line-items"):
            with counting(connection) as statements:
                assert (
                    await client.get(f"{base(world, aid)}/{analysis_id}{suffix}", headers=world.ada)
                ).status_code == 200
            counts.append(len(statements))
        return counts

    assert await cost(small["id"]) == await cost(large["id"])
