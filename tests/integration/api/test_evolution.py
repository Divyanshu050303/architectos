"""The evolution and decisions APIs against a real database (Milestone 13, phase 9)."""

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from core.architecture_ir.serialization import to_dict
from persistence.models import AuditLogRecord, EvolutionAnalysisRecord, EvolutionCandidateRecord
from tests.unit.evolution.test_evolution_triggers import shop

from .requirement_support import World, member, signed_in

pytestmark = pytest.mark.integration

WORKLOAD = {"name": "Peak", "type": "request_response", "peakRate": {"value": 200, "unit": "requests/second"}}
SCALE = {"type": "increase_workload", "target": {"value": 200, "unit": "requests/second"}}
AVAILABLE = {"type": "availability_objective", "target": {"value": "99.9", "unit": "%"}}


async def architecture(client: AsyncClient, world: World, *, analyzed: bool = True) -> str:
    created = await client.post(
        f"/api/v1/projects/{world.project_id}/architectures", json={"name": "Shop", "ir": to_dict(shop())},
        headers=world.ada,
    )  # fmt: skip
    assert created.status_code == 201, created.text
    aid = str(created.json()["id"])
    if analyzed:
        base = f"/api/v1/projects/{world.project_id}/architectures/{aid}"
        for path, body in (("capacity-analyses", {"workload": WORKLOAD}), ("reliability-analyses", {})):
            response = await client.post(f"{base}/{path}", json=body, headers=world.ada)
            assert response.status_code == 201, response.text
    return aid


def base(world: World, aid: str) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures/{aid}/evolution-analyses"


async def run(client: AsyncClient, world: World, aid: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        base(world, aid), json={"goals": [SCALE, AVAILABLE]} | body, headers=world.ada
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_an_analysis_is_run_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid = await architecture(client, world)
    before = (
        await client.get(f"/api/v1/projects/{world.project_id}/architectures/{aid}", headers=world.ada)
    ).json()
    created = await run(client, world, aid, label="Growth")
    assert created["status"] in {"completed", "partial"}
    assert created["label"] == "Growth"
    assert {x["code"] for x in created["limitations"]} >= {
        "proposals_only",
        "configuration_only",
        "model_based",
    }
    assert {e["source"] for e in created["evidence"] if e["state"] == "current"} == {
        "capacity",
        "reliability",
    }
    assert not {"score", "rank", "winner", "best"} & set(created["summary"])
    url = f"{base(world, aid)}/{created['id']}"
    assert (await client.get(url, headers=world.ada)).json() == created

    candidates = (await client.get(f"{url}/candidates", headers=world.ada)).json()["candidates"]
    assert {c["rule"]["id"] for c in candidates} == {"scale-replicas", "add-replica"}
    assert all(c["status"] == "proposed" for c in candidates)
    scaling = next(c for c in candidates if c["rule"]["id"] == "scale-replicas")
    assert scaling["changes"] == [{"elementId": "api", "property": "replicas", "value": 4}]
    assert scaling["validation"] == "valid"
    capacity = next(i for i in scaling["impacts"] if i["dimension"] == "capacity")
    assert (capacity["state"], capacity["source"]) == ("completed", "simulation")
    assert {r["dimension"] for r in scaling["consequences"]} >= {"capacity", "dependencies", "expertise"}

    detail = (await client.get(f"{url}/candidates/{scaling['id']}", headers=world.ada)).json()
    assert detail["candidate"] == scaling
    assert detail["overlay"]["changes"] == [{"element_id": "api", "property": "replicas", "change": "2 -> 4"}]
    alternatives = (await client.get(f"{url}/alternatives", headers=world.ada)).json()["alternatives"]
    assert {a["goal"] for a in alternatives} == {g["key"] for g in created["goals"]}

    stored = await db.scalar(
        select(func.count()).where(EvolutionCandidateRecord.analysis_id == uuid.UUID(created["id"]))
    )
    assert stored == len(candidates)
    audit = await db.scalar(
        select(AuditLogRecord).where(AuditLogRecord.action == "architecture.evolution_analyzed")
    )
    assert audit is not None
    assert audit.event_metadata["analysis_id"] == created["id"]
    after = (
        await client.get(f"/api/v1/projects/{world.project_id}/architectures/{aid}", headers=world.ada)
    ).json()
    assert after == before  # nothing applied, no revision created


async def test_fixture_11_stale_evidence_after_a_new_revision(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    current = (
        await client.get(f"/api/v1/projects/{world.project_id}/architectures/{aid}", headers=world.ada)
    ).json()
    saved = await client.put(
        f"/api/v1/projects/{world.project_id}/architectures/{aid}/content",
        json={"baseVersion": current["currentVersion"], "ir": to_dict(shop("Shop v2"))},
        headers=world.ada,
    )
    assert saved.status_code == 201, saved.text
    created = await run(client, world, aid)
    assert created["revision"] == 2
    assert {(e["source"], e["state"]) for e in created["evidence"]} == {
        ("capacity", "stale"), ("reliability", "stale"),
    }  # fmt: skip
    assert created["status"] == "insufficient_evidence"
    url = f"{base(world, aid)}/{created['id']}/candidates"
    assert (await client.get(url, headers=world.ada)).json()["candidates"] == []  # nothing invented


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"goals": []}, 422, "validation_error"),
        ({"goals": [{"type": "reduce_operational_complexity"}]}, 422, "validation_error"),
        ({"goals": [{"type": "increase_workload", "target": {"value": 5, "unit": "ms"}}]}, 422,
         "invalid_evolution_request"),
        ({"goals": [{"type": "cost_ceiling", "amount": "500"}]}, 422, "invalid_evolution_request"),
        ({"goals": [SCALE], "evidence": [{"source": "capacity", "analysisId": str(uuid.uuid4())}]}, 422,
         "invalid_evolution_request"),
        ({"goals": [{"type": "satisfy_requirement", "requirementId": str(uuid.uuid4())}]}, 422,
         "invalid_evolution_request"),
        ({"goals": [SCALE], "revision": 9}, 404, "architecture_revision_not_found"),
        ({"goals": [SCALE], "winner": "evo_x"}, 422, "validation_error"),
    ],
)  # fmt: skip
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid = await architecture(client, world, analyzed=False)
    response = await client.post(base(world, aid), json=body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(EvolutionAnalysisRecord)) == 0


async def test_access_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{base(world, aid)}/{created['id']}", headers=viewer)).status_code == 200
    denied = await client.post(base(world, aid), json={"goals": [SCALE]}, headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for method, url in (
        ("get", f"{base(world, aid)}/{created['id']}/candidates"),
        ("post", base(world, aid)),
    ):
        response = await client.request(method, url, json={"goals": [SCALE]}, headers=stranger)
        assert response.json()["error"]["code"] == "project_not_found"
    missing = await client.get(f"{base(world, aid)}/{uuid.uuid4()}", headers=world.ada)
    assert missing.json()["error"]["code"] == "evolution_analysis_not_found"
    no_candidate = await client.get(
        f"{base(world, aid)}/{created['id']}/candidates/evo_{'0' * 20}", headers=world.ada
    )
    assert no_candidate.json()["error"]["code"] == "evolution_candidate_not_found"
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
            raise RuntimeError("internal detail sk_live_do_not_leak")

        def catalog(self) -> dict[str, Any]:
            return {}

    app.state.evolution_engine = Broken()
    created = await run(client, world, aid)
    assert (created["status"], created["error"]["code"], created["summary"]) == (
        "failed",
        "engine_error",
        None,
    )
    assert "internal" not in json.dumps(created)


async def test_the_catalog(client: AsyncClient, world: World) -> None:
    catalog = (await client.get("/api/v1/evolution/catalog", headers=world.ada)).json()
    assert [r["id"] for r in catalog["rules"]] == [
        "add-replica", "enable-signal", "encrypt-at-rest", "require-tls", "scale-cpu", "scale-replicas",
    ]  # fmt: skip
    assert all(r["preconditions"] and r["unsupported"] for r in catalog["rules"])
    assert (await client.get("/api/v1/evolution/catalog")).status_code == 401


async def test_analyses_are_append_only(client: AsyncClient, db: AsyncSession, world: World) -> None:
    aid = await architecture(client, world)
    created = await run(client, world, aid)
    for statement in (
        "UPDATE evolution_analyses SET label = 'x' WHERE id = :id",
        "DELETE FROM evolution_candidates WHERE analysis_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": created["id"]})


async def test_decisions_are_drafted_and_decided_by_people(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await architecture(client, world)
    analysis = await run(client, world, aid)
    decisions = f"/api/v1/projects/{world.project_id}/decisions"
    drafted = await client.post(
        decisions, json={"architectureId": aid, "analysisId": analysis["id"]}, headers=world.ada
    )
    assert drafted.status_code == 201, drafted.text
    draft = drafted.json()
    assert (draft["status"], draft["reference"], draft["chosenOption"]) == ("proposed", "ADR-1", None)
    assert len(draft["options"]) == 2
    option = draft["options"][0]["candidateId"]
    url = f"{decisions}/{draft['id']}"
    viewer = await member(client, db, outbox, world, "viewer")
    refused = await client.post(
        f"{url}/accept", json={"candidateId": option, "rationale": "x"}, headers=viewer
    )
    assert refused.json()["error"]["code"] == "permission_denied"
    accepted = await client.post(
        f"{url}/accept", json={"candidateId": option, "rationale": "Fits the budget."}, headers=world.ada
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    again = await client.post(f"{url}/reject", json={"rationale": "Too late."}, headers=world.ada)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "invalid_decision_transition")
    early = await client.post(f"{url}/resulting-revision", json={"revision": 1}, headers=world.ada)
    assert (early.status_code, early.json()["error"]["code"]) == (422, "invalid_decision")
    document = (await client.get(f"{url}/document", headers=viewer)).json()
    assert document["reference"] == "ADR-1"
    assert "Accepted:" in document["markdown"]
    architecture_now = (
        await client.get(f"/api/v1/projects/{world.project_id}/architectures/{aid}", headers=world.ada)
    ).json()
    assert architecture_now["currentVersion"] == 1  # accepting applied nothing
    listed = (await client.get(decisions, params={"status": "accepted"}, headers=viewer)).json()
    assert [d["reference"] for d in listed["decisions"]] == ["ADR-1"]
    stranger = await signed_in(client, outbox, "eve@example.com")
    hidden = await client.get(url, headers=stranger)
    assert hidden.json()["error"]["code"] == "project_not_found"
    actions = {
        a.action
        for a in await db.scalars(select(AuditLogRecord).where(AuditLogRecord.resource_type == "decision"))
    }
    assert actions == {"decision.proposed", "decision.accepted"}
