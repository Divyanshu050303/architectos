"""Every mutating project, requirement and requirement-set endpoint writes an audit entry for the
right organization and resource, and no entry carries requirement text.

The endpoint list comes from OpenAPI: a new mutating endpoint under a project fails this test
until it is classified here, either with a plan (how to call it and what it must record) or as
read-only.
"""

import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from apps.api.email.transport import InMemoryTransport
from core.domain.audit.entities import AuditAction

from .support import inventory, signed_in

CANARY_TITLE = "Canary title 7f3a"
CANARY_STATEMENT = "Canary statement: the vault is at 10.9.8.7"
CANARY_REASON = "Canary reason e41b"
REQUIREMENT = {
    "type": "capacity",
    "category": "throughput",
    "title": CANARY_TITLE,
    "statement": CANARY_STATEMENT,
    "priority": "critical",
    "status": "active",
    "structuredData": {
        "metric": "requests_per_second",
        "operator": ">=",
        "value": 2000,
        "unit": "requests/second",
    },
}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# POSTs that change nothing (and so write no audit entry).
READ_ONLY = {"validate_requirement_api_v1_projects__project_id__requirements__requirement_id__validate_post"}
_names = itertools.count()


@dataclass
class Target:
    org_id: str
    project_id: str
    requirement_id: str


Prepare = Callable[[AsyncClient, dict[str, str], Target], Awaitable[None]]


async def nothing(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    return None


async def archive(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    await client.post(f"/api/v1/projects/{target.project_id}/archive", headers=auth)


@dataclass(frozen=True)
class Plan:
    actions: set[str]  # must all be recorded
    resource: str  # "project", "requirement", or "created" (the resource the call creates)
    body: dict[str, Any] | None = None
    prepare: Prepare = nothing


PLANS: dict[str, Plan] = {
    "create_project_api_v1_organizations__organization_id__projects_post": Plan(
        {"project.created"}, "created", {"name": "Audited"}
    ),
    "update_project_api_v1_projects__project_id__patch": Plan(
        {"project.updated"}, "project", {"name": "Renamed"}
    ),
    "archive_project_api_v1_projects__project_id__archive_post": Plan({"project.archived"}, "project"),
    "restore_project_api_v1_projects__project_id__restore_post": Plan(
        {"project.restored"}, "project", None, archive
    ),
    "delete_project_api_v1_projects__project_id__delete": Plan({"project.deleted"}, "project", None, archive),
    "create_requirement_api_v1_projects__project_id__requirements_post": Plan(
        {"requirement.created"}, "created", REQUIREMENT
    ),
    "update_requirement_api_v1_projects__project_id__requirements__requirement_id__patch": Plan(
        {"requirement.version_created", "requirement.updated", "requirement.status_changed"},
        "requirement",
        {
            "expectedVersion": 1,
            "statement": CANARY_STATEMENT + " (moved)",
            "status": "satisfied",
            "changeReason": CANARY_REASON,
        },
    ),
    "delete_requirement_api_v1_projects__project_id__requirements__requirement_id__delete": Plan(
        {"requirement.deleted"}, "requirement"
    ),
    "create_requirement_set_api_v1_projects__project_id__requirement_sets_post": Plan(
        {"requirement_set.created"}, "created", {"name": "Audited set"}
    ),
}


def in_scope(path: str) -> bool:
    return "{project_id}" in path or path.endswith("/organizations/{organization_id}/projects")


def test_every_mutating_project_endpoint_is_classified(app: FastAPI) -> None:
    mutating = {op.operation_id for op in inventory(app) if op.method in MUTATING and in_scope(op.path)}
    assert mutating == set(PLANS) | READ_ONLY


def test_every_project_scoped_audit_action_is_exercised() -> None:
    """No action is declared and then never written."""
    declared = {
        a.value for a in AuditAction if a.value.split(".")[0] in {"project", "requirement", "requirement_set"}
    }
    assert declared == set().union(*(plan.actions for plan in PLANS.values()))


async def audit_entries(client: AsyncClient, auth: dict[str, str], org_id: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = (
            await client.get(f"/api/v1/organizations/{org_id}/audit-log", params=params, headers=auth)
        ).json()
        entries += page["entries"]
        cursor = page["nextCursor"]
        if cursor is None:
            return entries


async def fresh_target(client: AsyncClient, auth: dict[str, str], org_id: str) -> Target:
    project = await client.post(
        f"/api/v1/organizations/{org_id}/projects", json={"name": f"Project {next(_names)}"}, headers=auth
    )
    requirement = await client.post(
        f"/api/v1/projects/{project.json()['id']}/requirements", json=REQUIREMENT, headers=auth
    )
    assert requirement.status_code == 201, requirement.text
    return Target(org_id, project.json()["id"], requirement.json()["id"])


async def test_every_mutation_is_audited_without_requirement_text(
    app: FastAPI, client: AsyncClient, outbox: InMemoryTransport
) -> None:
    auth = await signed_in(client, outbox, "ada@example.com")
    org_id = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=auth)).json()["id"]
    operations = {op.operation_id: op for op in inventory(app)}

    for operation_id, plan in PLANS.items():
        target = await fresh_target(client, auth, org_id)
        await plan.prepare(client, auth, target)
        before = {entry["id"] for entry in await audit_entries(client, auth, org_id)}

        op = operations[operation_id]
        url = op.url(
            organization_id=org_id, project_id=target.project_id, requirement_id=target.requirement_id
        )
        response = await client.request(op.method, url, json=plan.body, headers=auth)
        assert response.is_success, (operation_id, response.text)

        new = [e for e in await audit_entries(client, auth, org_id) if e["id"] not in before]
        assert {e["action"] for e in new} == plan.actions, operation_id
        expected_resource = {
            "project": target.project_id,
            "requirement": target.requirement_id,
            "created": response.json().get("id") if response.content else None,
        }[plan.resource]
        for entry in new:
            assert entry["resourceId"] == expected_resource, (operation_id, entry)
            assert entry["actorUserId"] is not None
            assert entry["ipAddress"] is not None
            text = repr(entry)
            for canary in (CANARY_TITLE, CANARY_STATEMENT, CANARY_REASON, "10.9.8.7"):
                assert canary not in text, (operation_id, entry)


async def test_read_only_endpoints_write_nothing(
    app: FastAPI, client: AsyncClient, outbox: InMemoryTransport
) -> None:
    auth = await signed_in(client, outbox, "ada@example.com")
    org_id = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=auth)).json()["id"]
    target = await fresh_target(client, auth, org_id)
    await client.post(f"/api/v1/projects/{target.project_id}/requirement-sets", json={}, headers=auth)
    before = await audit_entries(client, auth, org_id)

    reads = [
        op
        for op in inventory(app)
        if in_scope(op.path) and (op.method == "GET" or op.operation_id in READ_ONLY)
    ]
    assert len(reads) >= 10
    sets = (await client.get(f"/api/v1/projects/{target.project_id}/requirement-sets", headers=auth)).json()
    ids = {
        "organization_id": org_id,
        "project_id": target.project_id,
        "requirement_id": target.requirement_id,
        "set_id": sets["requirementSets"][0]["id"],
    }
    for op in reads:
        response = await client.request(op.method, op.url(**ids), headers=auth)
        assert response.is_success, (op.operation_id, response.text)

    assert await audit_entries(client, auth, org_id) == before
