"""The architecture agent API against a real database (LLM Architecture Agent, phase 6), with a scripted
model in place of a provider: a run against a covered requirement set reaches a validated candidate; a
set with blocking gaps waits for answers and resumes; acceptance goes through the architecture
workflow (source ai) and commits with the run's record, exactly once; a stale base, a changed hash or a
decided run is refused; without a model a run fails llm_unavailable; runs are authorized per project
and tenant-isolated; the stored record is append-only where it must be."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.email.transport import InMemoryTransport
from engines.architecture_agent.factory import build_pipeline
from engines.architecture_agent.orchestrator import AgentEngines
from persistence.models import AgentRunRecord

from .requirement_support import World, create, member, signed_in

pytestmark = pytest.mark.integration

AVAILABILITY: dict[str, Any] = {
    "type": "availability",
    "category": "availability",
    "title": "Uptime",
    "statement": "The service is available 99.9% of each month.",
    "priority": "high",
    "status": "active",
    "structuredData": {"metric": "availability", "operator": ">=", "value": "99.9", "unit": "%"},
}
BASE_IR = {
    "schema_version": 1,
    "name": "Orders",
    "nodes": [{"id": "orders-api", "kind": "service", "name": "Orders API"}],
}


def runs(world: World, project_id: str | None = None) -> str:
    return f"/api/v1/projects/{project_id or world.project_id}/architecture-agent-runs"


def architectures(world: World) -> str:
    return f"/api/v1/projects/{world.project_id}/architectures"


async def requirement_set(client: AsyncClient, world: World, *, covered: bool = True) -> str:
    await create(client, world)  # traffic: 2,000 requests per second
    if covered:
        await create(client, world, **AVAILABILITY)
    created = await client.post(
        f"/api/v1/projects/{world.project_id}/requirement-sets", json={}, headers=world.ada
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def start(client: AsyncClient, world: World, set_id: str, **body: Any) -> Any:
    request = {"requirementSetId": set_id, "objective": "An order service"} | body
    response = await client.post(runs(world), json=request, headers=world.ada)
    assert response.status_code == 201, response.text
    return response.json()


async def ready(client: AsyncClient, world: World, **body: Any) -> Any:
    run = await start(client, world, await requirement_set(client, world), **body)
    assert run["status"] == "candidate_ready", run
    return run


def answers(run: Any, said: str = "About 2,000 requests per second at 99.9%") -> dict[str, Any]:
    return {"answers": [{"questionId": q["id"], "answer": said} for q in run["questions"] if q["blocking"]]}


def accept_body(run: Any) -> dict[str, Any]:
    return {"candidateContentHash": run["candidate"]["contentHash"]}


async def test_a_covered_set_reaches_a_validated_candidate(client: AsyncClient, world: World) -> None:
    run = await ready(client, world)
    assert run["stage"] == "review"
    assert run["model"] == "scripted/test-model"
    assert run["promptVersion"] == "architecture-proposal-v1"
    assert run["usage"]["modelCalls"] == 1
    assert run["usage"]["cost"] is None
    assert run["rawOutput"]["sha256"]
    candidate = run["candidate"]
    assert {n["id"] for n in candidate["architecture"]["nodes"]} == {"orders-api", "orders-db"}
    reports = {r["engine"]: r["status"] for r in run["reports"]}
    assert reports["validation"] == "evaluated"
    assert reports["capacity"] == reports["cost"] == reports["simulation"] == "not_evaluated"
    assert run["acceptability"] == {"acceptable": True, "reason": None}
    assert [q["kind"] for q in run["questions"] if q["kind"] == "proposer"] == ["proposer"]
    fetched = await client.get(f"{runs(world)}/{run['id']}", headers=world.ada)
    assert fetched.json() == run
    listed = await client.get(runs(world), headers=world.ada)
    assert [r["id"] for r in listed.json()["runs"]] == [run["id"]]
    assert listed.json()["runs"][0]["candidateContentHash"] == candidate["contentHash"]


async def test_blocking_gaps_wait_for_answers_then_resume(client: AsyncClient, world: World) -> None:
    run = await start(client, world, await requirement_set(client, world, covered=False))
    assert run["status"] == "awaiting_clarification"
    assert run["model"] is None  # the model was not called
    blocking = [q for q in run["questions"] if q["blocking"]]
    assert [q["kind"] for q in blocking] == ["missing_concern"]
    url = f"{runs(world)}/{run['id']}/answers"
    nope = {"answers": [{"questionId": "aq_nope", "answer": "x"}]}
    unknown = await client.post(url, json=nope, headers=world.ada)
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"]["reason"] == "unknown_question"
    resumed = await client.post(url, json=answers(run), headers=world.ada)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "candidate_ready"
    assert resumed.json()["id"] == run["id"]
    assert resumed.json()["answers"][0]["answer"] == "About 2,000 requests per second at 99.9%"
    again = await client.post(url, json=answers(run), headers=world.ada)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_agent_transition"


async def test_accepting_creates_an_architecture_from_exactly_the_candidate(
    client: AsyncClient, world: World
) -> None:
    run = await ready(client, world)
    url = f"{runs(world)}/{run['id']}/accept"
    changed = await client.post(url, json={"candidateContentHash": "0" * 64}, headers=world.ada)
    assert changed.status_code == 409
    assert changed.json()["error"]["details"]["reason"] == "candidate_changed"
    accepted = await client.post(url, json=accept_body(run) | {"name": "Orders platform"}, headers=world.ada)
    assert accepted.status_code == 201, accepted.text
    result = accepted.json()
    assert result["createdArchitecture"] is True
    assert result["run"]["status"] == "accepted"
    assert result["run"]["accepted"] == {"architectureId": result["architectureId"], "revisionNumber": 1}
    architecture = await client.get(f"{architectures(world)}/{result['architectureId']}", headers=world.ada)
    revision = architecture.json()["revision"]
    assert revision["source"] == "ai"
    assert revision["contentHash"] == run["candidate"]["contentHash"]
    assert revision["requirementSetId"] == run["request"]["requirementSetId"]
    assert architecture.json()["name"] == "Orders platform"
    twice = await client.post(url, json=accept_body(run), headers=world.ada)
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "invalid_agent_transition"


async def test_an_iteration_needs_its_base_to_still_be_current(client: AsyncClient, world: World) -> None:
    created = await client.post(
        architectures(world), json={"name": "Orders", "ir": BASE_IR}, headers=world.ada
    )
    assert created.status_code == 201, created.text
    architecture_id = created.json()["id"]
    run = await ready(client, world, base={"architectureId": architecture_id, "revisionNumber": 1})
    moved = await client.put(
        f"{architectures(world)}/{architecture_id}/content",
        json={"baseVersion": 1, "ir": BASE_IR | {"name": "Orders v2"}},
        headers=world.ada,
    )
    assert moved.status_code == 201, moved.text
    stale = await client.post(f"{runs(world)}/{run['id']}/accept", json=accept_body(run), headers=world.ada)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "architecture_version_conflict"
    unchanged = await client.get(f"{runs(world)}/{run['id']}", headers=world.ada)
    assert unchanged.json()["status"] == "candidate_ready"  # the acceptance rolled back with the revision


async def test_an_iteration_on_a_current_base_is_a_new_revision(client: AsyncClient, world: World) -> None:
    created = await client.post(
        architectures(world), json={"name": "Orders", "ir": BASE_IR}, headers=world.ada
    )
    architecture_id = created.json()["id"]
    run = await ready(client, world, base={"architectureId": architecture_id, "revisionNumber": 1})
    accepted = await client.post(
        f"{runs(world)}/{run['id']}/accept", json=accept_body(run), headers=world.ada
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["createdArchitecture"] is False
    assert (accepted.json()["architectureId"], accepted.json()["revisionNumber"]) == (architecture_id, 2)


async def test_rejecting_and_cancelling_are_recorded(client: AsyncClient, world: World) -> None:
    waiting = await start(client, world, await requirement_set(client, world, covered=False))
    cancelled = await client.post(f"{runs(world)}/{waiting['id']}/cancel", headers=world.ada)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["history"][-1]["userId"] is not None
    run = await ready(client, world)
    reason = {"reason": "Too many moving parts"}
    rejected = await client.post(f"{runs(world)}/{run['id']}/reject", json=reason, headers=world.ada)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decisionReason"] == "Too many moving parts"
    late = await client.post(f"{runs(world)}/{run['id']}/accept", json=accept_body(run), headers=world.ada)
    assert late.status_code == 409
    again = await client.post(f"{runs(world)}/{run['id']}/cancel", headers=world.ada)
    assert again.status_code == 409  # only a waiting run is cancelled


async def test_without_a_model_a_run_fails_llm_unavailable(
    app: FastAPI, client: AsyncClient, world: World
) -> None:
    state = app.state
    engines = AgentEngines(
        state.validation_engine, state.reliability_engine, state.security_engine, state.observability_engine
    )
    app.state.agent_pipeline = build_pipeline(
        provider="none",
        api_key=None,
        model="unused",
        timeout_seconds=10,
        engines=engines,
        catalog=state.component_catalog,
    )
    run = await start(client, world, await requirement_set(client, world))
    assert run["status"] == "failed"
    assert run["failure"]["code"] == "llm_unavailable"
    assert run["failure"]["stage"] == "proposal"
    assert run["candidate"] is None
    assert run["model"] == "none/not-configured"
    assert run["usage"]["modelCalls"] == 0


async def test_a_viewer_reads_but_does_not_run(
    client: AsyncClient, db: AsyncSession, outbox: InMemoryTransport, world: World
) -> None:
    run = await ready(client, world)
    viewer = await member(client, db, outbox, world, "viewer")
    assert (await client.get(f"{runs(world)}/{run['id']}", headers=viewer)).status_code == 200
    request = {"requirementSetId": run["request"]["requirementSetId"], "objective": "x"}
    assert (await client.post(runs(world), json=request, headers=viewer)).status_code == 403
    refused = await client.post(f"{runs(world)}/{run['id']}/reject", json={"reason": "No"}, headers=viewer)
    assert refused.status_code == 403


async def test_runs_and_sets_of_other_projects_are_not_found(
    client: AsyncClient, outbox: InMemoryTransport, world: World
) -> None:
    run = await ready(client, world)
    elsewhere = await client.get(f"{runs(world, world.other_id)}/{run['id']}", headers=world.ada)
    assert elsewhere.status_code == 404
    request = {"requirementSetId": run["request"]["requirementSetId"], "objective": "x"}
    foreign = await client.post(runs(world, world.other_id), json=request, headers=world.ada)
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "requirement_set_not_found"
    stranger = await signed_in(client, outbox, "mallory@example.com")
    assert (await client.get(f"{runs(world)}/{run['id']}", headers=stranger)).status_code == 404


async def test_the_stored_record_holds(client: AsyncClient, db: AsyncSession, world: World) -> None:
    run = await ready(client, world)
    accepted = await client.post(
        f"{runs(world)}/{run['id']}/accept", json=accept_body(run), headers=world.ada
    )
    assert accepted.status_code == 201
    assert await db.scalar(select(func.count()).select_from(AgentRunRecord)) == 1
    stored = await db.scalar(select(AgentRunRecord))
    assert stored is not None
    assert stored.request["objective"] == "An order service"
    assert stored.status == "accepted"
    for statement, message in (
        (
            "UPDATE architecture_agent_runs SET decision_reason = 'rewritten'",
            "a finished run does not change",
        ),
        ("DELETE FROM architecture_agent_runs", "an accepted run is kept"),
        ("TRUNCATE architecture_agent_runs CASCADE", "cannot be truncated"),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with db.begin_nested():
                await db.execute(text(statement))


async def test_a_waiting_runs_request_cannot_be_rewritten(
    client: AsyncClient, db: AsyncSession, world: World
) -> None:
    await start(client, world, await requirement_set(client, world, covered=False))
    with pytest.raises(DBAPIError, match="the request of a run does not change"):
        async with db.begin_nested():
            await db.execute(text("UPDATE architecture_agent_runs SET request = '{}'::jsonb"))
