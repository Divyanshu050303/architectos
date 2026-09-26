"""Every mutating project, requirement, requirement-set and architecture endpoint writes an audit
entry for the right organization and resource, and no entry carries requirement or architecture
text.

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
# An architecture whose names carry the canaries: the audit log must record none of them.
ARCHITECTURE = {
    "schema_version": 1,
    "name": CANARY_TITLE,
    "nodes": [{"id": "api", "kind": "service", "name": CANARY_TITLE, "description": CANARY_STATEMENT}],
}
# The shared middle of architecture operation ids.
_ARCH = "_api_v1_projects__project_id__architectures__architecture_id__"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# POSTs that change nothing (and so write no audit entry).
READ_ONLY = {"validate_requirement_api_v1_projects__project_id__requirements__requirement_id__validate_post"}
# Mutations deliberately not audited, each with the reason.
NOT_AUDITED = {
    # Where boxes are drawn: presentation, never an architecture change or a revision.
    f"save_architecture_layout{_ARCH}layout_put",
}
_names = itertools.count()


@dataclass
class Target:
    org_id: str
    project_id: str
    requirement_id: str
    analysis_id: str
    candidate_key: str
    architecture_id: str = ""  # set by with_architecture
    snapshot_id: str = ""  # set by with_prices


Prepare = Callable[[AsyncClient, dict[str, str], Target], Awaitable[None]]


async def nothing(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    return None


async def archive(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    await client.post(f"/api/v1/projects/{target.project_id}/archive", headers=auth)


async def with_architecture(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    response = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures",
        json={"name": CANARY_TITLE, "description": CANARY_STATEMENT, "ir": ARCHITECTURE},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    target.architecture_id = response.json()["id"]


PRICE = {
    "id": "ec2", "provider": "aws", "service": "ec2", "sku": "m7g.large", "region": "eu-west-1",
    "currency": "USD", "unit": "instance_hour", "model": "per_unit", "unitPrice": "0.08",
    "effectiveFrom": "2026-09-01", "source": "user_input",
}  # fmt: skip


async def create_snapshot(client: AsyncClient, auth: dict[str, str], org_id: str) -> str:
    response = await client.post(
        f"/api/v1/organizations/{org_id}/pricing-snapshots",
        json={"name": "Prices", "records": [PRICE]},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def with_prices(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """An architecture and a pricing snapshot of the organization (its audit entry is not the plan's)."""
    await with_architecture(client, auth, target)
    target.snapshot_id = await create_snapshot(client, auth, target.org_id)


async def with_archived_architecture(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    await with_architecture(client, auth, target)
    archived = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/archive", headers=auth
    )
    assert archived.status_code == 200, archived.text


async def with_two_revisions(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    await with_architecture(client, auth, target)
    edited = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/commands",
        json={"baseVersion": 1, "commands": [{"type": "change_replicas", "nodeId": "api", "replicas": 4}]},
        headers=auth,
    )
    assert edited.status_code == 201, edited.text


@dataclass(frozen=True)
class Plan:
    actions: set[str]  # must all be recorded
    # "project", "requirement", "created" (the resource the call creates), or "promoted"
    resource: str
    body: dict[str, Any] | Callable[[Target], dict[str, Any]] | None = None
    prepare: Prepare = nothing


CAPACITY_WORKLOAD = {
    "name": "Peak",
    "type": "request_response",
    "peakRate": {"value": 100, "unit": "requests/second"},
}

PLANS: dict[str, Plan] = {
    "create_project_api_v1_organizations__organization_id__projects_post": Plan(
        {"project.created"}, "created", {"name": "Audited"}
    ),
    "update_project_api_v1_projects__project_id__patch": Plan(
        {"project.updated"}, "project", {"name": "Renamed"}
    ),
    "archive_project_api_v1_projects__project_id__archive_post": Plan({"project.archived"}, "project"),
    "put_architecture_policy_api_v1_projects__project_id__architecture_policy_put": Plan(
        {"project.policy_updated"}, "project", {"prohibitedTechnologies": ["mongodb"], "requireTls": True}
    ),
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
    "analyze_requirements_api_v1_projects__project_id__requirement_analyses_post": Plan(
        {"requirement_analysis.created"}, "created", {"input": CANARY_STATEMENT + " Support 2000 rps."}
    ),
    "promote_candidates_api_v1_projects__project_id__requirement_analyses__analysis_id__promote_post": Plan(
        {"requirement.promoted"}, "promoted", lambda target: {"candidateKeys": [target.candidate_key]}
    ),
    "create_architecture_api_v1_projects__project_id__architectures_post": Plan(
        {"architecture.created"},
        "created",
        {"name": CANARY_TITLE, "description": CANARY_STATEMENT, "ir": ARCHITECTURE, "reason": CANARY_REASON},
    ),
    f"update_architecture{_ARCH}patch": Plan(
        {"architecture.updated"}, "architecture", {"name": CANARY_TITLE + " 2"}, with_architecture
    ),
    f"replace_architecture_content{_ARCH}content_put": Plan(
        {"architecture.revised"},
        "architecture",
        {"baseVersion": 1, "ir": ARCHITECTURE | {"description": CANARY_STATEMENT}, "reason": CANARY_REASON},
        with_architecture,
    ),
    f"edit_architecture{_ARCH}commands_post": Plan(
        {"architecture.revised"},
        "architecture",
        {
            "baseVersion": 1,
            "commands": [{"type": "rename_node", "nodeId": "api", "name": CANARY_TITLE + " 2"}],
            "reason": CANARY_REASON,
        },
        with_architecture,
    ),
    f"restore_architecture_version{_ARCH}versions__version__restore_post": Plan(
        {"architecture.revision_restored"},
        "architecture",
        {"baseVersion": 2, "reason": CANARY_REASON},
        with_two_revisions,
    ),
    f"archive_architecture{_ARCH}archive_post": Plan(
        {"architecture.archived"}, "architecture", None, with_architecture
    ),
    f"restore_architecture{_ARCH}restore_post": Plan(
        {"architecture.restored"}, "architecture", None, with_archived_architecture
    ),
    f"run_capacity_analysis{_ARCH}capacity_analyses_post": Plan(
        {"architecture.capacity_analyzed"},
        "architecture",
        {"workload": CAPACITY_WORKLOAD, "label": "Audited"},
        with_architecture,
    ),
    f"run_cost_analysis{_ARCH}cost_analyses_post": Plan(
        {"architecture.cost_analyzed"},
        "architecture",
        lambda target: {"snapshotId": target.snapshot_id, "pricingDate": "2026-09-26", "label": "Audited"},
        with_prices,
    ),
    f"run_reliability_analysis{_ARCH}reliability_analyses_post": Plan(
        {"architecture.reliability_analyzed"}, "architecture", {"label": "Audited"}, with_architecture
    ),
    f"run_validation{_ARCH}validations_post": Plan(
        {"architecture.validated"}, "architecture", {"profile": "default"}, with_architecture
    ),
    f"delete_architecture{_ARCH}delete": Plan(
        {"architecture.deleted"}, "architecture", None, with_archived_architecture
    ),
}


def in_scope(path: str) -> bool:
    return "{project_id}" in path or path.endswith("/organizations/{organization_id}/projects")


def test_every_mutating_project_endpoint_is_classified(app: FastAPI) -> None:
    mutating = {op.operation_id for op in inventory(app) if op.method in MUTATING and in_scope(op.path)}
    assert mutating == set(PLANS) | READ_ONLY | NOT_AUDITED


def test_every_project_scoped_audit_action_is_exercised() -> None:
    """No action is declared and then never written."""
    scoped = {"project", "requirement", "requirement_set", "requirement_analysis", "architecture"}
    declared = {a.value for a in AuditAction if a.value.split(".")[0] in scoped}
    assert declared == set().union(*(plan.actions for plan in PLANS.values()))


def _expected_resource(resource: str, target: Target, response: Any) -> str | None:
    match resource:
        case "project":
            return target.project_id
        case "requirement":
            return target.requirement_id
        case "promoted":
            return str(response.json()["promotions"][0]["requirement"]["id"])
        case "architecture":
            return target.architecture_id
        case _:  # "created"
            return str(response.json()["id"]) if response.content else None


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
    analysis = await client.post(
        f"/api/v1/projects/{project.json()['id']}/requirement-analyses",
        json={"input": "Support at least 2000 rps."},
        headers=auth,
    )
    assert analysis.status_code == 201, analysis.text
    return Target(
        org_id,
        project.json()["id"],
        requirement.json()["id"],
        analysis.json()["id"],
        analysis.json()["candidates"][0]["key"],
    )


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
            organization_id=org_id,
            project_id=target.project_id,
            requirement_id=target.requirement_id,
            analysis_id=target.analysis_id,
            architecture_id=target.architecture_id,
        )
        body = plan.body(target) if callable(plan.body) else plan.body
        response = await client.request(op.method, url, json=body, headers=auth)
        assert response.is_success, (operation_id, response.text)

        new = [e for e in await audit_entries(client, auth, org_id) if e["id"] not in before]
        assert {e["action"] for e in new} == plan.actions, operation_id
        expected_resource = _expected_resource(plan.resource, target, response)
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
    await with_architecture(client, auth, target)
    run = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/validations",
        json={},
        headers=auth,
    )
    assert run.status_code == 201, run.text
    analysis = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/capacity-analyses",
        json={"workload": CAPACITY_WORKLOAD},
        headers=auth,
    )
    assert analysis.status_code == 201, analysis.text
    cost = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/cost-analyses",
        json={"snapshotId": await create_snapshot(client, auth, org_id)},
        headers=auth,
    )
    assert cost.status_code == 201, cost.text
    reliability = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/reliability-analyses",
        json={},
        headers=auth,
    )
    assert reliability.status_code == 201, reliability.text
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
        "analysis_id": target.analysis_id,
        "architecture_id": target.architecture_id,
        "run_id": run.json()["id"],
        "capacity_analysis_id": analysis.json()["id"],
        "cost_analysis_id": cost.json()["id"],
        "reliability_analysis_id": reliability.json()["id"],
    }
    for op in reads:
        response = await client.request(op.method, op.url(**ids), headers=auth)
        assert response.is_success, (op.operation_id, response.text)

    assert await audit_entries(client, auth, org_id) == before
