"""Architecture workflows over HTTP (Autonomous Architecture Workflow, phase 5), with a real worker: a goal
is queued, carried by the worker to a review package and becomes an architecture only when a person
approves exactly a reviewed candidate — through the architecture workflow, with a stale base refused; a
goal without requirements waits for a person to confirm them; permissions are re-checked before every
action; another project's or tenant's workflow is not found."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from apps.api.email.transport import InMemoryTransport
from persistence.models import OrganizationMemberRecord, UserRecord

from .requirement_support import World, member, signed_in
from .test_architecture_agent import BASE_IR, architectures, requirement_set
from .workflow_support import approvable, drain, read_workflow, start_workflow, workflows

pytestmark = pytest.mark.integration


async def reviewed(
    app: FastAPI, client: AsyncClient, connection: AsyncConnection, world: World, **body: Any
) -> dict[str, Any]:
    """A workflow carried by the worker to its review package."""
    set_id = await requirement_set(client, world)
    started = await start_workflow(client, world.ada, world.project_id, requirementSetId=set_id, **body)
    await drain(app, connection)
    flow = await read_workflow(client, world.ada, world.project_id, started["id"])
    assert flow["status"] == "review_ready", flow
    return flow


async def test_a_goal_becomes_an_architecture_only_when_a_person_approves(
    app: FastAPI, client: AsyncClient, connection: AsyncConnection, world: World
) -> None:
    set_id = await requirement_set(client, world)
    started = await start_workflow(client, world.ada, world.project_id, requirementSetId=set_id)
    assert started["status"] == "queued"
    assert started["steps"] == []  # nothing runs in the request
    assert started["candidates"] == []
    assert await drain(app, connection) == 1
    flow = await read_workflow(client, world.ada, world.project_id, started["id"])
    assert flow["status"] == "review_ready"
    assert [s["ordinal"] for s in flow["steps"]] == list(range(1, len(flow["steps"]) + 1))
    assert {s["action"] for s in flow["steps"]} >= {"generate_architecture", "validate_architecture"}
    assert flow["usage"]["llmCalls"] >= 1
    assert flow["usage"]["toolCalls"] == len(flow["steps"])
    listed = await client.get(architectures(world), headers=world.ada)
    assert listed.json()["architectures"] == []  # the workflow wrote no architecture
    body = approvable(flow)
    url = f"{workflows(world.project_id)}/{flow['id']}"
    detail = await client.get(f"{url}/candidates/{body['candidateId']}", headers=world.ada)
    assert detail.status_code == 200, detail.text
    assert detail.json()["architecture"]["schema_version"] == 1
    changed = await client.post(
        f"{url}/approve", json=body | {"candidateContentHash": "0" * 64}, headers=world.ada
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["details"]["reason"] == "candidate_changed"
    approved = await client.post(f"{url}/approve", json=body | {"name": "Orders platform"}, headers=world.ada)
    assert approved.status_code == 201, approved.text
    result = approved.json()
    assert result["createdArchitecture"] is True
    assert result["revisionNumber"] == 1
    assert result["candidate"]["status"] == "accepted"
    architecture = await client.get(f"{architectures(world)}/{result['architectureId']}", headers=world.ada)
    revision = architecture.json()["revision"]
    assert revision["source"] == "ai"
    assert revision["contentHash"] == body["candidateContentHash"]
    assert revision["requirementSetId"] == set_id
    after = await read_workflow(client, world.ada, world.project_id, flow["id"])
    assert after["status"] == "approved"
    assert after["approved"] == {
        "candidateId": body["candidateId"], "architectureId": result["architectureId"], "revisionNumber": 1,
    }  # fmt: skip
    twice = await client.post(f"{url}/approve", json=body, headers=world.ada)
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "invalid_workflow_transition"


async def test_requirements_are_confirmed_by_a_person_before_design(
    app: FastAPI, client: AsyncClient, connection: AsyncConnection, world: World
) -> None:
    started = await start_workflow(client, world.ada, world.project_id)
    await drain(app, connection)
    waiting = await read_workflow(client, world.ada, world.project_id, started["id"])
    assert waiting["status"] == "needs_input"
    assert waiting["inputNeeded"]["kind"] == "confirm_requirements"
    assert waiting["inputNeeded"]["analysisId"] == waiting["requirementAnalysisId"]
    assert waiting["candidates"] == []  # no design before a person confirms requirements
    url = f"{workflows(world.project_id)}/{started['id']}/input"
    wrong = await client.post(url, json={"answers": [{"questionId": "q", "answer": "a"}]}, headers=world.ada)
    assert wrong.status_code == 422
    missing = await client.post(url, json={"requirementSetId": str(uuid.uuid4())}, headers=world.ada)
    assert missing.status_code == 404
    set_id = await requirement_set(client, world)
    confirmed = await client.post(url, json={"requirementSetId": set_id}, headers=world.ada)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "queued"
    await drain(app, connection)
    flow = await read_workflow(client, world.ada, world.project_id, started["id"])
    assert flow["status"] == "review_ready"
    assert flow["requirementSetId"] == set_id
    assert [e["status"] for e in flow["history"]][:4] == ["running", "needs_input", "queued", "running"]


async def test_two_workflows_on_one_revision_only_one_is_approved(
    app: FastAPI, client: AsyncClient, connection: AsyncConnection, world: World
) -> None:
    created = await client.post(
        architectures(world), json={"name": "Orders", "ir": BASE_IR}, headers=world.ada
    )
    assert created.status_code == 201, created.text
    architecture_id = created.json()["id"]
    base = {"architectureId": architecture_id, "revisionNumber": 1}
    set_id = await requirement_set(client, world)
    first = await start_workflow(client, world.ada, world.project_id, requirementSetId=set_id, base=base)
    second = await start_workflow(client, world.ada, world.project_id, requirementSetId=set_id, base=base)
    await drain(app, connection)
    one = await read_workflow(client, world.ada, world.project_id, first["id"])
    two = await read_workflow(client, world.ada, world.project_id, second["id"])
    assert one["status"] == two["status"] == "review_ready"
    url = workflows(world.project_id)
    approved = await client.post(f"{url}/{one['id']}/approve", json=approvable(one), headers=world.ada)
    assert approved.status_code == 201, approved.text
    assert approved.json()["createdArchitecture"] is False
    assert approved.json()["revisionNumber"] == 2
    stale = await client.post(f"{url}/{two['id']}/approve", json=approvable(two), headers=world.ada)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "workflow_candidate_not_approvable"
    assert stale.json()["error"]["details"]["reason"] == "stale_candidate"
    unchanged = await read_workflow(client, world.ada, world.project_id, two["id"])
    assert unchanged["status"] == "review_ready"  # the refusal rolled back with the revision
    architecture = await client.get(f"{architectures(world)}/{architecture_id}", headers=world.ada)
    assert architecture.json()["currentVersion"] == 2


async def test_cancelling_and_rejecting_are_recorded(
    app: FastAPI, client: AsyncClient, connection: AsyncConnection, world: World
) -> None:
    set_id = await requirement_set(client, world)
    started = await start_workflow(client, world.ada, world.project_id, requirementSetId=set_id)
    url = f"{workflows(world.project_id)}/{started['id']}"
    cancelled = await client.post(f"{url}/cancel", headers=world.ada)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert await drain(app, connection) == 0  # a cancelled workflow is never claimed
    early = await client.post(f"{url}/reject", json={"reason": "Not now"}, headers=world.ada)
    assert early.status_code == 409
    flow = await reviewed(app, client, connection, world)
    rejected = await client.post(
        f"{workflows(world.project_id)}/{flow['id']}/reject", json={"reason": "Too costly"}, headers=world.ada
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decisionReason"] == "Too costly"
    listed = await client.get(workflows(world.project_id), params={"status": "rejected"}, headers=world.ada)
    assert [w["id"] for w in listed.json()["workflows"]] == [flow["id"]]


async def test_a_viewer_reads_but_does_not_run_or_decide(
    app: FastAPI,
    client: AsyncClient,
    connection: AsyncConnection,
    db: AsyncSession,
    outbox: InMemoryTransport,
    world: World,
) -> None:
    flow = await reviewed(app, client, connection, world)
    viewer = await member(client, db, outbox, world, "viewer")
    url = f"{workflows(world.project_id)}/{flow['id']}"
    assert (await client.get(url, headers=viewer)).status_code == 200
    assert (await client.get(workflows(world.project_id), headers=viewer)).status_code == 200
    refused = await client.post(workflows(world.project_id), json={"objective": "x"}, headers=viewer)
    assert refused.status_code == 403
    for action, body in (("approve", approvable(flow)), ("reject", {"reason": "No"}), ("cancel", None)):
        assert (await client.post(f"{url}/{action}", json=body, headers=viewer)).status_code == 403, action
    after = await read_workflow(client, world.ada, world.project_id, flow["id"])
    assert after["status"] == "review_ready"


async def test_permissions_are_checked_again_before_every_action(
    app: FastAPI,
    client: AsyncClient,
    connection: AsyncConnection,
    db: AsyncSession,
    outbox: InMemoryTransport,
    world: World,
) -> None:
    architect = await member(client, db, outbox, world, "member")
    set_id = await requirement_set(client, world)
    started = await start_workflow(client, architect, world.project_id, requirementSetId=set_id)
    user = await db.scalar(select(UserRecord).where(UserRecord.email == "member@example.com"))
    assert user is not None
    await db.execute(
        update(OrganizationMemberRecord)
        .where(OrganizationMemberRecord.user_id == user.id)
        .values(role="viewer")
    )  # demoted after starting it
    await db.flush()
    await drain(app, connection)
    flow = await read_workflow(client, world.ada, world.project_id, started["id"])
    assert flow["status"] == "failed"
    assert flow["failure"]["code"] == "permission_denied"
    assert flow["candidates"] == []


async def test_workflows_of_other_projects_and_tenants_are_not_found(
    app: FastAPI, client: AsyncClient, connection: AsyncConnection, outbox: InMemoryTransport, world: World
) -> None:
    flow = await reviewed(app, client, connection, world)
    elsewhere = await client.get(f"{workflows(world.other_id)}/{flow['id']}", headers=world.ada)
    assert elsewhere.status_code == 404
    assert elsewhere.json()["error"]["code"] == "architecture_workflow_not_found"
    candidate = flow["candidates"][0]["id"]
    hidden = await client.get(
        f"{workflows(world.other_id)}/{flow['id']}/candidates/{candidate}", headers=world.ada
    )
    assert hidden.status_code == 404
    foreign_set = await client.post(
        workflows(world.other_id),
        json={"objective": "x", "requirementSetId": flow["requirementSetId"]},
        headers=world.ada,
    )
    assert foreign_set.status_code == 404
    assert foreign_set.json()["error"]["code"] == "requirement_set_not_found"
    stranger = await signed_in(client, outbox, "mallory@example.com")
    url = f"{workflows(world.project_id)}/{flow['id']}"
    assert (await client.get(url, headers=stranger)).status_code == 404
    taken = await client.post(f"{url}/approve", json=approvable(flow), headers=stranger)
    assert taken.status_code == 404


@pytest.mark.parametrize(
    ("body", "status", "reason"),
    [
        ({"capacityAnalysisId": str(uuid.uuid4())}, 422, "needs_base"),
        ({"budget": {"maxIterations": 5}}, 422, "above_configured"),  # lowered, never raised
        ({"objective": "x" * 4001}, 422, None),
        ({"constraints": ["c"] * 21}, 422, None),
        ({"requirementSetId": str(uuid.uuid4())}, 404, None),
        ({"base": {"architectureId": str(uuid.uuid4()), "revisionNumber": 1}}, 404, None),
    ],
)
async def test_a_goal_is_checked_before_it_is_queued(
    client: AsyncClient, world: World, body: dict[str, Any], status: int, reason: str | None
) -> None:
    request = {"objective": "Orders"} | body
    response = await client.post(workflows(world.project_id), json=request, headers=world.ada)
    assert response.status_code == status, response.text
    if reason is not None:
        assert response.json()["error"]["details"]["reason"] == reason
    listed = await client.get(workflows(world.project_id), headers=world.ada)
    assert listed.json()["workflows"] == []


async def test_a_lowered_budget_is_kept(client: AsyncClient, world: World) -> None:
    set_id = await requirement_set(client, world)
    lowered = {"maxIterations": 0, "maxLlmCalls": 4}
    started = await start_workflow(
        client, world.ada, world.project_id, requirementSetId=set_id, budget=lowered
    )
    assert started["budget"]["max_iterations"] == 0
    assert started["budget"]["max_llm_calls"] == 4
