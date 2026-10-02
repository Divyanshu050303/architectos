"""The migration plan API against a real database (Migration Planning, phase 10): plans generated from
exact stored revisions, persisted append-only with their review, reviewed by authorized people on an
exact version, stale when the architecture moves on, tenant-isolated, and never changing the
architecture or executing anything."""

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
from persistence.models import AuditLogRecord, MigrationPlanVersionRecord
from tests.unit.evolution.test_evolution_triggers import shop

from .requirement_support import World, member, signed_in
from .test_evolution import WORKLOAD, architecture, run

pytestmark = pytest.mark.integration


def scaled(replicas: int = 4) -> dict[str, Any]:
    """The shop with more api replicas: a configuration change the planner can plan."""
    ir = to_dict(shop())
    api = next(n for n in ir["nodes"] if n["id"] == "api")
    api["configuration"]["values"]["replicas"] = replicas
    return ir


async def revise(client: AsyncClient, world: World, aid: str, ir: dict[str, Any]) -> None:
    url = f"/api/v1/projects/{world.project_id}/architectures/{aid}"
    current = (await client.get(url, headers=world.ada)).json()
    saved = await client.put(
        f"{url}/content", json={"baseVersion": current["currentVersion"], "ir": ir}, headers=world.ada
    )
    assert saved.status_code == 201, saved.text


def plans(world: World) -> str:
    return f"/api/v1/projects/{world.project_id}/migration-plans"


async def two_revisions(client: AsyncClient, world: World, *, analyzed: bool = True) -> str:
    aid = await architecture(client, world, analyzed=analyzed)
    await revise(client, world, aid, scaled())
    return aid


def request(aid: str, **overrides: Any) -> dict[str, Any]:
    return {"architectureId": aid, "sourceRevision": 1, "target": {"revision": 2}} | overrides


async def create(client: AsyncClient, world: World, aid: str, **overrides: Any) -> dict[str, Any]:
    response = await client.post(plans(world), json=request(aid, **overrides), headers=world.ada)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def version_url(world: World, plan: dict[str, Any]) -> str:
    return f"{plans(world)}/{plan['planId']}/versions/{plan['version']}"


async def test_a_plan_is_generated_from_exact_revisions_stored_and_read_back(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid = await two_revisions(client, world)
    url = f"/api/v1/projects/{world.project_id}/architectures/{aid}"
    before = (await client.get(url, headers=world.ada)).json()
    created = await create(client, world, aid, title="Scale the API")
    assert (created["version"], created["status"], created["title"]) == (1, "draft", "Scale the API")
    assert (created["source"]["revisionNumber"], created["target"]["revisionNumber"]) == (1, 2)
    assert {s["key"] for s in created["steps"]} == {"configure:api", "verify:api"}
    assert len(created["sequence"]) == len(created["steps"])
    assert created["freshness"] == {"stale": False, "reasons": []}
    coverage = {(c["source"], c["side"]): c["state"] for c in created["coverage"]}
    assert coverage[("capacity", "source")] == "current"  # the stored analysis of revision 1, reused
    assert coverage[("capacity", "target")] == "missing"
    assert coverage[("simulation", "target")] == "unsupported"
    assert not {"score", "probability", "executed"} & set(created["summary"])

    assert (await client.get(f"{plans(world)}/{created['planId']}", headers=world.ada)).json() == created
    assert (await client.get(version_url(world, created), headers=world.ada)).json() == created
    steps = (await client.get(f"{version_url(world, created)}/steps", headers=world.ada)).json()
    assert steps == {"steps": created["steps"], "sequence": created["sequence"]}
    for part in ("risks", "checkpoints", "rollbacks"):
        response = await client.get(f"{version_url(world, created)}/{part}", headers=world.ada)
        assert response.json()[part] == created[part]

    stored = await db.scalar(
        select(MigrationPlanVersionRecord).where(MigrationPlanVersionRecord.id == uuid.UUID(created["id"]))
    )
    assert stored is not None
    assert stored.fingerprint == created["fingerprint"]
    audit = await db.scalar(select(AuditLogRecord).where(AuditLogRecord.action == "migration_plan.created"))
    assert audit is not None
    assert audit.event_metadata["plan_id"] == created["planId"]
    assert (await client.get(url, headers=world.ada)).json() == before  # the architecture is unchanged


async def test_an_exact_version_is_reviewed_by_authorized_people(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await two_revisions(client, world, analyzed=False)
    created = await create(client, world, aid)
    url = version_url(world, created)
    submitted = await client.post(f"{url}/submit", headers=world.ada)
    assert submitted.json()["status"] == "ready_for_review"

    colleague = await member(client, db, outbox, world, "member")
    exact = {"fingerprint": created["fingerprint"]}
    denied = await client.post(f"{url}/approve", json=exact, headers=colleague)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    wrong = await client.post(f"{url}/approve", json={"fingerprint": "0" * 64}, headers=world.ada)
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (409, "migration_plan_version_mismatch")

    approved = await client.post(f"{url}/approve", json=exact | {"comment": "Reviewed."}, headers=world.ada)
    assert approved.status_code == 200, approved.text
    review = approved.json()["reviews"][-1]
    assert (review["toStatus"], review["fingerprint"], review["comment"]) == (
        "approved", created["fingerprint"], "Reviewed.",
    )  # fmt: skip
    assert approved.json()["steps"] == created["steps"]  # approval changes no content and executes nothing

    regenerate = f"{plans(world)}/{created['planId']}/regenerate"
    revised = {"request": request(aid, title="Revised")}
    refused = await client.post(regenerate, json=revised, headers=world.ada)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "reviewed_migration_plan")
    replaced = await client.post(regenerate, json=revised | {"replaceReviewed": True}, headers=world.ada)
    assert replaced.status_code == 200, replaced.text
    assert (replaced.json()["created"], replaced.json()["plan"]["version"]) == (True, 2)

    history = (await client.get(f"{plans(world)}/{created['planId']}/versions", headers=world.ada)).json()
    first, second = history["versions"]
    assert (first["status"], second["status"]) == ("superseded", "draft")
    assert [r["toStatus"] for r in first["reviews"]] == ["ready_for_review", "approved", "superseded"]
    assert second["reviews"] == []  # independently reviewable


async def test_an_identical_regeneration_creates_nothing(client: AsyncClient, world: World) -> None:
    aid = await two_revisions(client, world, analyzed=False)
    created = await create(client, world, aid)
    again = await client.post(f"{plans(world)}/{created['planId']}/regenerate", json={}, headers=world.ada)
    assert (again.json()["created"], again.json()["plan"]["version"]) == (False, 1)


async def test_a_rejection_keeps_its_feedback(client: AsyncClient, world: World) -> None:
    aid = await two_revisions(client, world, analyzed=False)
    created = await create(client, world, aid)
    url = version_url(world, created)
    await client.post(f"{url}/submit", headers=world.ada)
    exact = {"fingerprint": created["fingerprint"]}
    missing = await client.post(f"{url}/reject", json=exact, headers=world.ada)
    assert missing.status_code == 422  # a rejection says why
    rejected = await client.post(
        f"{url}/reject", json=exact | {"comment": "Add a rollback plan."}, headers=world.ada
    )
    assert rejected.json()["status"] == "rejected"
    again = (await client.get(url, headers=world.ada)).json()
    assert again["reviews"][-1]["comment"] == "Add a rollback plan."


async def test_a_new_revision_makes_a_plan_stale_and_unreviewable(client: AsyncClient, world: World) -> None:
    aid = await two_revisions(client, world, analyzed=False)
    created = await create(client, world, aid)
    await revise(client, world, aid, scaled(6))
    read = (await client.get(f"{plans(world)}/{created['planId']}", headers=world.ada)).json()
    assert read["freshness"] == {"stale": True, "reasons": ["newer_revision"]}
    refused = await client.post(f"{version_url(world, created)}/submit", headers=world.ada)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "stale_migration_plan")
    assert refused.json()["error"]["details"] == {"reasons": ["newer_revision"]}


async def test_a_plan_can_target_an_evolution_candidate(client: AsyncClient, world: World) -> None:
    aid = await architecture(client, world)
    analysis = await run(client, world, aid)
    base = f"/api/v1/projects/{world.project_id}/architectures/{aid}/evolution-analyses/{analysis['id']}"
    candidates = (await client.get(f"{base}/candidates", headers=world.ada)).json()["candidates"]
    scaling = next(c for c in candidates if c["rule"]["id"] == "scale-replicas")
    target = {"analysisId": analysis["id"], "candidateId": scaling["id"]}
    created = await create(client, world, aid, target=target)
    assert (created["target"]["kind"], created["target"]["candidateId"]) == ("candidate", scaling["id"])
    assert created["target"]["revisionNumber"] == 1  # its baseline: the source revision
    assert {c["state"] for c in created["coverage"] if c["side"] == "target"} == {"unsupported"}
    assert created["freshness"]["stale"] is False


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"target": {"revision": 1}}, 422, "invalid_migration_request"),  # not after the source
        ({"target": {"revision": 99}}, 404, "architecture_revision_not_found"),
        ({"target": {}}, 422, "invalid_migration_request"),
        ({"target": {"revision": 2}, "executeNow": True}, 422, "validation_error"),
        ({"target": {"analysisId": str(uuid.uuid4()), "candidateId": "evo_" + "a" * 20}}, 404,
         "evolution_analysis_not_found"),
    ],
)  # fmt: skip
async def test_invalid_requests_are_refused_and_store_nothing(
    client: AsyncClient, db: AsyncSession, world: World, body: dict[str, Any], status: int, code: str
) -> None:
    aid = await two_revisions(client, world, analyzed=False)
    response = await client.post(plans(world), json=request(aid) | body, headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert await db.scalar(select(func.count()).select_from(MigrationPlanVersionRecord)) == 0


async def test_access_isolation_and_listing(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    aid = await two_revisions(client, world, analyzed=False)
    created = await create(client, world, aid)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{plans(world)}/{created['planId']}", headers=viewer)).status_code == 200
    denied = await client.post(plans(world), json=request(aid), headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "permission_denied")
    stranger = await signed_in(client, outbox, "eve@example.com")
    for method, url in (("get", f"{plans(world)}/{created['planId']}"), ("post", plans(world))):
        response = await client.request(method, url, json=request(aid), headers=stranger)
        assert response.json()["error"]["code"] == "project_not_found"
    assert (await client.get(f"{plans(world)}/{created['planId']}")).status_code == 401
    other = f"/api/v1/projects/{world.other_id}/migration-plans"
    hidden = await client.get(f"{other}/{created['planId']}", headers=world.ada)
    assert hidden.json()["error"]["code"] == "migration_plan_not_found"  # another project's plan
    foreign = await client.post(other, json=request(aid), headers=world.ada)
    assert foreign.json()["error"]["code"] == "architecture_not_found"

    second = await create(client, world, aid, title="Second")
    listed = (await client.get(plans(world), params={"limit": 1}, headers=world.ada)).json()
    rest = (await client.get(plans(world), params={"cursor": listed["nextCursor"]}, headers=world.ada)).json()
    assert [p["planId"] for p in listed["plans"] + rest["plans"]] == [second["planId"], created["planId"]]
    approved = (await client.get(plans(world), params={"status": "approved"}, headers=world.ada)).json()
    assert approved["plans"] == []


async def test_a_versions_content_never_changes_and_is_never_deleted(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid = await two_revisions(client, world, analyzed=False)
    created = await create(client, world, aid)
    for statement, message in (
        ("UPDATE migration_plan_versions SET title = 'x' WHERE id = :id", "only the review status"),
        ("DELETE FROM migration_plan_versions WHERE id = :id", "append-only"),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with db.begin_nested():
                await db.execute(text(statement), {"id": created["id"]})


async def test_an_engine_failure_exposes_nothing_and_stores_nothing(
    app: FastAPI, client: AsyncClient, db: AsyncSession, world: World
) -> None:
    aid = await two_revisions(client, world, analyzed=False)

    class Broken:
        def plan(self, inputs: Any) -> Any:
            raise RuntimeError("internal detail sk_live_do_not_leak")

        def models(self) -> dict[str, int]:
            return {}

    app.state.migration_engine = Broken()
    response = await client.post(plans(world), json=request(aid), headers=world.ada)
    assert (response.status_code, response.json()["error"]["code"]) == (500, "internal_error")
    assert "sk_live" not in json.dumps(response.json())
    assert await db.scalar(select(func.count()).select_from(MigrationPlanVersionRecord)) == 0


async def test_stored_analyses_of_the_target_become_evaluated_checkpoints(
    client: AsyncClient, world: World
) -> None:
    aid = await two_revisions(client, world)
    url = f"/api/v1/projects/{world.project_id}/architectures/{aid}"
    validation = await client.post(f"{url}/validations", json={"revision": 2}, headers=world.ada)
    assert validation.status_code == 201, validation.text
    capacity = await client.post(
        f"{url}/capacity-analyses", json={"workload": WORKLOAD, "revision": 2}, headers=world.ada
    )
    assert capacity.status_code == 201, capacity.text
    created = await create(client, world, aid)
    coverage = {(c["source"], c["side"]): c for c in created["coverage"]}
    assert coverage[("validation", "target")]["analysisIds"] == [validation.json()["id"]]
    assert coverage[("capacity", "target")]["state"] == "current"
    checkpoints = {c["key"]: c for c in created["checkpoints"]}
    evaluated = checkpoints["validation:target"]
    assert evaluated["status"] in {"pass", "warning", "fail"}  # from the stored run, never recomputed
    assert evaluated["basis"] == "modeled"
    assert [e["reference"] for e in evaluated["evidence"]] == [validation.json()["id"]]
    cited = {(e["source"], e["reference"]) for e in created["evidence"] if e["state"] == "current"}
    assert ("capacity", capacity.json()["id"]) in cited
