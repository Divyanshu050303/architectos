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
from core.architecture_ir.serialization import to_dict
from core.domain.audit.entities import AuditAction
from tests.unit.evolution.test_evolution_triggers import shop as evolving_shop

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
_DECISION = "_api_v1_projects__project_id__decisions__decision_id__"
_PLAN = "_api_v1_projects__project_id__migration_plans__plan_id__"
_PLAN_VERSION = _PLAN + "versions__version__"
_DISCOVERY = "_api_v1_projects__project_id__discovery_runs__run_id__"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# POSTs that change nothing (and so write no audit entry).
_KNOWLEDGE = "_api_v1_projects__project_id__knowledge_sources__source_id__"
SEARCH = "search_knowledge_api_v1_projects__project_id__knowledge_search_post"
READ_ONLY = {
    "validate_requirement_api_v1_projects__project_id__requirements__requirement_id__validate_post",
    SEARCH,  # a search: nothing changes but a record snapshot's staleness, which is not an action
}
READ_BODIES: dict[str, dict[str, Any]] = {SEARCH: {"text": "replica failover"}}
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
    evolution_id: str = ""  # set by with_evolution
    decision_id: str = ""  # set by with_proposal
    candidate_id: str = ""  # an option of that decision
    successor_id: str = ""  # set by with_accepted_pair
    plan_id: str = ""  # set by with_plan (its version 1)
    fingerprint: str = ""  # of that version
    discovery_run_id: str = ""  # set by with_discovery
    proposal_hash: str = ""  # of that run's proposal
    drift_architecture_id: str = ""  # set by with_drift: accepted from that run
    drift_analysis_id: str = ""
    drift_item_id: str = ""
    knowledge_source_id: str = ""  # set by with_knowledge


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


# An architecture the engines find something in (a single database replica, a flow without tls): its
# evolution analysis proposes candidates, so decisions have options to act on.
EVOLVING = to_dict(evolving_shop())
AVAILABILITY_GOAL = {"type": "availability_objective", "target": {"value": "0.999", "unit": "ratio"}}


async def with_evolution(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """An architecture with a reliability analysis and an evolution analysis that has candidates."""
    created = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures",
        json={"name": "Evolving", "ir": EVOLVING},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    target.architecture_id = created.json()["id"]
    base = f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}"
    reliability = await client.post(f"{base}/reliability-analyses", json={}, headers=auth)
    assert reliability.status_code == 201, reliability.text
    evolution = await client.post(
        f"{base}/evolution-analyses", json={"goals": [AVAILABILITY_GOAL]}, headers=auth
    )
    assert evolution.status_code == 201, evolution.text
    target.evolution_id = evolution.json()["id"]


async def _draft(client: AsyncClient, auth: dict[str, str], target: Target) -> str:
    drafted = await client.post(
        f"/api/v1/projects/{target.project_id}/decisions",
        json={"architectureId": target.architecture_id, "analysisId": target.evolution_id},
        headers=auth,
    )
    assert drafted.status_code == 201, drafted.text
    target.candidate_id = drafted.json()["options"][0]["candidateId"]
    return str(drafted.json()["id"])


async def _accept(client: AsyncClient, auth: dict[str, str], target: Target, decision_id: str) -> None:
    accepted = await client.post(
        f"/api/v1/projects/{target.project_id}/decisions/{decision_id}/accept",
        json={"candidateId": target.candidate_id, "rationale": "Fits."},
        headers=auth,
    )
    assert accepted.status_code == 200, accepted.text


async def with_proposal(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    await with_evolution(client, auth, target)
    target.decision_id = await _draft(client, auth, target)


async def with_accepted_pair(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    await with_proposal(client, auth, target)
    await _accept(client, auth, target, target.decision_id)
    target.successor_id = await _draft(client, auth, target)
    await _accept(client, auth, target, target.successor_id)


async def with_accepted_and_revision(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """An accepted decision, then a separate architecture change (revision 2) a person may link."""
    await with_proposal(client, auth, target)
    await _accept(client, auth, target, target.decision_id)
    edited = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/commands",
        json={"baseVersion": 1, "commands": [{"type": "change_replicas", "nodeId": "db", "replicas": 2}]},
        headers=auth,
    )
    assert edited.status_code == 201, edited.text


def migration_request(target: Target, title: str | None = None) -> dict[str, Any]:
    """From revision 1 to revision 2 (the latest, so the plan is never stale)."""
    request: dict[str, Any] = {
        "architectureId": target.architecture_id,
        "sourceRevision": 1,
        "target": {"revision": 2},
        "goals": [CANARY_STATEMENT],
    }
    return request | ({"title": title} if title is not None else {})


async def with_plan(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """An architecture with two revisions and a draft migration plan between them."""
    await with_two_revisions(client, auth, target)
    created = await client.post(
        f"/api/v1/projects/{target.project_id}/migration-plans", json=migration_request(target), headers=auth
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "draft", created.text
    target.plan_id = str(created.json()["planId"])
    target.fingerprint = str(created.json()["fingerprint"])


async def with_submitted_plan(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    await with_plan(client, auth, target)
    submitted = await client.post(
        f"/api/v1/projects/{target.project_id}/migration-plans/{target.plan_id}/versions/1/submit",
        headers=auth,
    )
    assert submitted.status_code == 200, submitted.text


# A discovered database whose label carries a canary: the audit log must record none of it.
DISCOVERY_REQUEST = {
    "artifacts": [
        {
            "path": "compose.yaml",
            "content": "name: shop\nservices:\n  db:\n    image: postgres:16\n"
            f"    labels: {{note: '{CANARY_TITLE}'}}\n",
        }
    ],
    "label": "Audited discovery",
}


async def with_discovery(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """A discovery run whose proposal (one database) can be accepted."""
    run = await client.post(
        f"/api/v1/projects/{target.project_id}/discovery-runs", json=DISCOVERY_REQUEST, headers=auth
    )
    assert run.status_code == 201, run.text
    target.discovery_run_id = str(run.json()["id"])
    proposal = await client.get(
        f"/api/v1/projects/{target.project_id}/discovery-runs/{target.discovery_run_id}/proposal",
        headers=auth,
    )
    assert proposal.status_code == 200, proposal.text
    target.proposal_hash = str(proposal.json()["contentHash"])


async def with_drift_baseline(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """The discovery's proposal accepted as an architecture: a baseline drift can be detected against."""
    await with_discovery(client, auth, target)
    accepted = await client.post(
        f"/api/v1/projects/{target.project_id}/discovery-runs/{target.discovery_run_id}/accept",
        json={"proposalContentHash": target.proposal_hash, "name": CANARY_TITLE},
        headers=auth,
    )
    assert accepted.status_code == 201, accepted.text
    target.drift_architecture_id = str(accepted.json()["architectureId"])


def drift_request(target: Target) -> dict[str, Any]:
    return {
        "architectureId": target.drift_architecture_id,
        "baselineRevision": 1,
        "discoveryRunId": target.discovery_run_id,
        "label": "Audited drift",
    }


async def with_drift(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """A drift analysis against a later discovery that adds a cache: one item to review."""
    await with_drift_baseline(client, auth, target)
    changed = DISCOVERY_REQUEST | {
        "artifacts": [
            {
                "path": "compose.yaml",
                "content": DISCOVERY_REQUEST["artifacts"][0]["content"]  # type: ignore[index]
                + "  cache:\n    image: redis:7\n",
            }
        ]
    }
    run = await client.post(
        f"/api/v1/projects/{target.project_id}/discovery-runs", json=changed, headers=auth
    )
    assert run.status_code == 201, run.text
    analysis = await client.post(
        f"/api/v1/projects/{target.project_id}/drift-analyses",
        json=drift_request(target) | {"discoveryRunId": run.json()["id"]},
        headers=auth,
    )
    assert analysis.status_code == 201, analysis.text
    target.drift_analysis_id = str(analysis.json()["id"])
    listed = await client.get(f"/api/v1/projects/{target.project_id}/drift-items", headers=auth)
    target.drift_item_id = str(listed.json()["items"][0]["id"])


# A knowledge document whose name and text carry canaries: the audit log must record none of them.
KNOWLEDGE_REQUEST = {
    "name": CANARY_TITLE,
    "document": {
        "path": "docs/runbook.md",
        "content": f"# Runbook\n\n{CANARY_STATEMENT}\n\nPromote the replica on failover.\n",
    },
}


async def with_knowledge(client: AsyncClient, auth: dict[str, str], target: Target) -> None:
    """An indexed knowledge document."""
    registered = await client.post(
        f"/api/v1/projects/{target.project_id}/knowledge-sources", json=KNOWLEDGE_REQUEST, headers=auth
    )
    assert registered.status_code == 201, registered.text
    target.knowledge_source_id = str(registered.json()["source"]["id"])


async def knowledge_ids(client: AsyncClient, auth: dict[str, str], target: Target) -> dict[str, str]:
    """An indexed document, its ingestion and a passage: the ids the knowledge reads need."""
    await with_knowledge(client, auth, target)
    url = f"/api/v1/projects/{target.project_id}/knowledge-sources/{target.knowledge_source_id}"
    runs = (await client.get(f"{url}/ingestions", headers=auth)).json()["runs"]
    found = await client.post(
        f"/api/v1/projects/{target.project_id}/knowledge/search", json=READ_BODIES[SEARCH], headers=auth
    )
    return {
        "source_id": target.knowledge_source_id,
        "ingestion_id": runs[0]["id"],
        "chunk_id": found.json()["passages"][0]["citation"]["chunkId"],
    }


@dataclass(frozen=True)
class Plan:
    actions: set[str]  # must all be recorded
    # "project", "requirement", "created" (the resource the call creates), or "promoted" — or, when
    # the call records actions about different resources, the resource of each action
    resource: str | dict[str, str]
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
    f"run_security_analysis{_ARCH}security_analyses_post": Plan(
        {"architecture.security_analyzed"}, "architecture", {"label": "Audited"}, with_architecture
    ),
    f"run_observability_analysis{_ARCH}observability_analyses_post": Plan(
        {"architecture.observability_analyzed"}, "architecture", {"label": "Audited"}, with_architecture
    ),
    f"run_simulation{_ARCH}simulations_post": Plan(
        {"architecture.simulated"},
        "architecture",
        {
            "scenario": {"name": "Outage", "failures": [{"kind": "component", "target": "api"}]},
            "label": "Audited",
        },
        with_architecture,
    ),
    f"run_evolution_analysis{_ARCH}evolution_analyses_post": Plan(
        {"architecture.evolution_analyzed"},
        "architecture",
        {"goals": [AVAILABILITY_GOAL], "label": "Audited"},
        with_architecture,
    ),
    "draft_decision_api_v1_projects__project_id__decisions_post": Plan(
        {"decision.proposed"},
        "created",
        lambda target: {"architectureId": target.architecture_id, "analysisId": target.evolution_id},
        with_evolution,
    ),
    f"accept_decision{_DECISION}accept_post": Plan(
        {"decision.accepted"},
        "decision",
        lambda target: {"candidateId": target.candidate_id, "rationale": CANARY_REASON},
        with_proposal,
    ),
    f"reject_decision{_DECISION}reject_post": Plan(
        {"decision.rejected"}, "decision", {"rationale": CANARY_REASON}, with_proposal
    ),
    f"supersede_decision{_DECISION}supersede_post": Plan(
        {"decision.superseded"},
        "decision",
        lambda target: {"byDecisionId": target.successor_id},
        with_accepted_pair,
    ),
    f"link_decision_revision{_DECISION}resulting_revision_post": Plan(
        {"decision.revision_linked"}, "decision", {"revision": 2}, with_accepted_and_revision
    ),
    "create_plan_api_v1_projects__project_id__migration_plans_post": Plan(
        {"migration_plan.created"}, "created_plan", migration_request, with_two_revisions
    ),
    f"regenerate_plan{_PLAN}regenerate_post": Plan(
        {"migration_plan.regenerated"},
        "plan",
        lambda target: {"request": migration_request(target, CANARY_TITLE)},
        with_plan,
    ),
    f"submit_plan{_PLAN_VERSION}submit_post": Plan({"migration_plan.submitted"}, "plan", None, with_plan),
    f"approve_plan{_PLAN_VERSION}approve_post": Plan(
        {"migration_plan.approved"},
        "plan",
        lambda target: {"fingerprint": target.fingerprint, "comment": CANARY_REASON},
        with_submitted_plan,
    ),
    f"reject_plan{_PLAN_VERSION}reject_post": Plan(
        {"migration_plan.rejected"},
        "plan",
        lambda target: {"fingerprint": target.fingerprint, "comment": CANARY_REASON},
        with_submitted_plan,
    ),
    f"archive_plan{_PLAN_VERSION}archive_post": Plan({"migration_plan.archived"}, "plan", None, with_plan),
    "run_discovery_api_v1_projects__project_id__discovery_runs_post": Plan(
        {"discovery_run.created"}, "created", DISCOVERY_REQUEST
    ),
    f"decide_discovery_candidate{_DISCOVERY}decisions_post": Plan(
        {"discovery_run.reviewed"},
        "discovery",
        {
            "subjectType": "entity",
            "subject": "compose:shop/service/db",
            "decision": "accepted",
            "comment": CANARY_REASON,
        },
        with_discovery,
    ),
    f"accept_discovery_proposal{_DISCOVERY}accept_post": Plan(
        {"discovery_run.accepted", "architecture.created"},
        {"discovery_run.accepted": "discovery", "architecture.created": "accepted_architecture"},
        lambda target: {"proposalContentHash": target.proposal_hash, "name": CANARY_TITLE},
        with_discovery,
    ),
    f"delete_discovery_run{_DISCOVERY}delete": Plan(
        {"discovery_run.deleted"}, "discovery", None, with_discovery
    ),
    "run_drift_analysis_api_v1_projects__project_id__drift_analyses_post": Plan(
        {"drift_analysis.created"}, "created", drift_request, with_drift_baseline
    ),
    "review_drift_item_api_v1_projects__project_id__drift_items__item_id__review_post": Plan(
        {"drift_item.reviewed"}, "drift_item", {"action": "note", "note": CANARY_REASON}, with_drift
    ),
    f"confirm_identity_mapping{_ARCH}identity_mappings_post": Plan(
        {"drift_identity.confirmed"},
        "drift_architecture",
        {"baselineId": "compose:shop/service/db", "discoveredKey": "compose:shop/service/db"},
        with_drift_baseline,
    ),
    "register_knowledge_source_api_v1_projects__project_id__knowledge_sources_post": Plan(
        {"knowledge_source.registered", "knowledge_source.ingested"}, "created_source", KNOWLEDGE_REQUEST
    ),
    f"reindex_knowledge_source{_KNOWLEDGE}ingestions_post": Plan(
        {"knowledge_source.ingested"},
        "knowledge",
        {"content": f"# Runbook\n\n{CANARY_REASON}\n"},
        with_knowledge,
    ),
    f"archive_knowledge_source{_KNOWLEDGE}archive_post": Plan(
        {"knowledge_source.archived"}, "knowledge", None, with_knowledge
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
    scoped = {
        "project",
        "requirement",
        "requirement_set",
        "requirement_analysis",
        "architecture",
        "decision",
        "migration_plan",
        "discovery_run",
        "drift_analysis",
        "drift_item",
        "drift_identity",
        "knowledge_source",
    }
    declared = {a.value for a in AuditAction if a.value.split(".")[0] in scoped}
    assert declared == set().union(*(plan.actions for plan in PLANS.values()))


def _expected_resource(resource: str, target: Target, response: Any) -> str | None:
    existing = {
        "project": target.project_id,
        "requirement": target.requirement_id,
        "architecture": target.architecture_id,
        "decision": target.decision_id,
        "plan": target.plan_id,
        "discovery": target.discovery_run_id,
        "drift_item": target.drift_item_id,
        "drift_architecture": target.drift_architecture_id,
        "knowledge": target.knowledge_source_id,
    }
    match resource:
        case "promoted":
            return str(response.json()["promotions"][0]["requirement"]["id"])
        case "created_plan":  # the plan's id, not its version's
            return str(response.json()["planId"])
        case "created_source":  # the knowledge source the registration created
            return str(response.json()["source"]["id"])
        case "accepted_architecture":  # the architecture the accepted proposal created
            return str(response.json()["architectureId"])
        case "created":
            return str(response.json()["id"]) if response.content else None
        case _:
            return existing[resource]


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
        values = {
            "organization_id": org_id,
            "project_id": target.project_id,
            "requirement_id": target.requirement_id,
            "analysis_id": target.analysis_id,
            "architecture_id": target.drift_architecture_id or target.architecture_id,
            "decision_id": target.decision_id,
            "plan_id": target.plan_id,
            **({"run_id": target.discovery_run_id} if target.discovery_run_id else {}),
            **({"item_id": target.drift_item_id} if target.drift_item_id else {}),
            **({"source_id": target.knowledge_source_id} if target.knowledge_source_id else {}),
        }
        url = op.url(**values)
        body = plan.body(target) if callable(plan.body) else plan.body
        response = await client.request(op.method, url, json=body, headers=auth)
        assert response.is_success, (operation_id, response.text)

        new = [e for e in await audit_entries(client, auth, org_id) if e["id"] not in before]
        assert {e["action"] for e in new} == plan.actions, operation_id
        for entry in new:
            resource = plan.resource if isinstance(plan.resource, str) else plan.resource[entry["action"]]
            assert entry["resourceId"] == _expected_resource(resource, target, response), (
                operation_id,
                entry,
            )
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
    security = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/security-analyses",
        json={},
        headers=auth,
    )
    assert security.status_code == 201, security.text
    observability = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/observability-analyses",
        json={},
        headers=auth,
    )
    assert observability.status_code == 201, observability.text
    simulation = await client.post(
        f"/api/v1/projects/{target.project_id}/architectures/{target.architecture_id}/simulations",
        json={"scenario": {"name": "Outage", "failures": [{"kind": "component", "target": "api"}]}},
        headers=auth,
    )
    assert simulation.status_code == 201, simulation.text
    decided = Target(
        org_id, target.project_id, target.requirement_id, target.analysis_id, target.candidate_key
    )
    await with_proposal(client, auth, decided)  # its own architecture: an analysis with candidates
    planned = await fresh_target(client, auth, org_id)
    await with_plan(client, auth, planned)  # its own project and architecture, with two revisions
    discovered = Target(org_id, target.project_id, "", "", "", target.architecture_id)
    await with_discovery(client, auth, discovered)
    baseline = {"architectureId": target.architecture_id, "revision": 1}
    compared = await client.post(
        f"/api/v1/projects/{target.project_id}/discovery-runs",
        json=DISCOVERY_REQUEST | {"baseline": baseline},
        headers=auth,
    )
    assert compared.status_code == 201, compared.text
    drifted = await fresh_target(client, auth, org_id)
    await with_drift(client, auth, drifted)  # its own project: an accepted architecture, one item
    drift_findings = await client.get(
        f"/api/v1/projects/{drifted.project_id}/drift-analyses/{drifted.drift_analysis_id}/findings",
        headers=auth,
    )
    knowledge = await knowledge_ids(client, auth, target)  # an indexed document in the main project
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
        "security_analysis_id": security.json()["id"],
        "observability_analysis_id": observability.json()["id"],
        "simulation_id": simulation.json()["id"],
        "other_simulation_id": simulation.json()["id"],
        **knowledge,
    }
    evolving = ids | {
        "architecture_id": decided.architecture_id,
        "evolution_analysis_id": decided.evolution_id,
        "candidate_id": decided.candidate_id,
        "decision_id": decided.decision_id,
    }
    for op in reads:
        uses = ("{evolution_analysis_id}", "{decision_id}", "/decisions")
        values = evolving if any(u in op.path for u in uses) else ids
        if "/migration-plans" in op.path:
            values = ids | {"project_id": planned.project_id, "plan_id": planned.plan_id}
        if "/discovery-runs" in op.path:  # the run with a baseline, compared with the other run
            values = ids | {"run_id": compared.json()["id"], "other_run_id": discovered.discovery_run_id}
        if "/drift-" in op.path or "/identity-mappings" in op.path:
            values = ids | {
                "project_id": drifted.project_id,
                "architecture_id": drifted.drift_architecture_id,
                "drift_analysis_id": drifted.drift_analysis_id,
                "finding_id": drift_findings.json()["findings"][0]["id"],
                "item_id": drifted.drift_item_id,
            }
        body = READ_BODIES.get(op.operation_id)
        response = await client.request(op.method, op.url(**values), json=body, headers=auth)
        assert response.is_success, (op.operation_id, response.text)

    assert await audit_entries(client, auth, org_id) == before
