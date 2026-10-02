"""Every request body rejects fields it does not declare, so a client can never set a role,
ownership, verification flag or identifier it was not meant to."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from apps.api.email.transport import InMemoryTransport

from .support import PASSWORD, WEB, assert_error_envelope, inventory, signed_in

PRIVILEGED = {
    "role": "owner",
    "isAdmin": True,
    "emailVerified": True,
    "userId": str(uuid.uuid4()),
    "status": "active",
}

# A valid body per operation. A new endpoint with a body must be added here (see the guard test).
WORKLOAD = {"name": "Peak", "type": "request_response", "peakRate": {"value": 100, "unit": "requests/second"}}
PRICE = {
    "id": "rds",
    "provider": "aws",
    "service": "rds",
    "sku": "db.r6g.large",
    "region": "eu-west-1",
    "currency": "USD",
    "unit": "instance_hour",
    "model": "per_unit",
    "unitPrice": "0.26",
    "effectiveFrom": "2026-09-01",
    "source": "user_input",
}
MIGRATION_REQUEST = {
    "architectureId": "01a0de60-a784-7244-8ecc-62afb5352f47",
    "sourceRevision": 1,
    "target": {"revision": 2},
}
DISCOVERY_ARTIFACT = {
    "path": "compose.yaml",
    "content": "name: shop\nservices:\n  db:\n    image: postgres:16\n",
}
_DISCOVERY_RUN = "_api_v1_projects__project_id__discovery_runs__run_id__"
_IDENTITY_MAPPINGS = "_api_v1_projects__project_id__architectures__architecture_id__identity_mappings_"
VALID_BODIES: dict[str, dict[str, object]] = {
    "run_discovery_api_v1_projects__project_id__discovery_runs_post": {"artifacts": [DISCOVERY_ARTIFACT]},
    f"decide_discovery_candidate{_DISCOVERY_RUN}decisions_post": {
        "subjectType": "entity",
        "subject": "compose:shop/service/db",
        "decision": "rejected",
    },
    f"accept_discovery_proposal{_DISCOVERY_RUN}accept_post": {"proposalContentHash": "0" * 64},
    "run_drift_analysis_api_v1_projects__project_id__drift_analyses_post": {
        "architectureId": str(uuid.uuid4()),
        "baselineRevision": 1,
        "discoveryRunId": str(uuid.uuid4()),
    },
    "review_drift_item_api_v1_projects__project_id__drift_items__item_id__review_post": {
        "action": "acknowledge"
    },
    "register_knowledge_source_api_v1_projects__project_id__knowledge_sources_post": {
        "document": {"path": "docs/a.md", "content": "# A"}
    },
    "reindex_knowledge_source_api_v1_projects__project_id__knowledge_sources__source_id__ingestions_post": {
        "content": "# A"
    },
    "search_knowledge_api_v1_projects__project_id__knowledge_search_post": {"text": "replica"},
    f"confirm_identity_mapping{_IDENTITY_MAPPINGS}post": {
        "baselineId": "db",
        "discoveredKey": "compose:shop/service/db",
    },
    "create_pricing_snapshot_api_v1_organizations__organization_id__pricing_snapshots_post": {
        "name": "Prices",
        "records": [PRICE],
    },
    "register_api_v1_auth_register_post": {"email": "x@example.com", "password": PASSWORD, "name": "X"},
    "verify_email_api_v1_auth_verify_email_post": {"token": "A" * 43},
    "resend_verification_api_v1_auth_resend_verification_post": {"email": "x@example.com"},
    "login_api_v1_auth_login_post": {"email": "x@example.com", "password": PASSWORD},
    "forgot_password_api_v1_auth_forgot_password_post": {"email": "x@example.com"},
    "reset_password_api_v1_auth_reset_password_post": {"token": "A" * 43, "password": PASSWORD},
    "update_profile_api_v1_me_patch": {"name": "X"},
    "delete_account_api_v1_me_delete": {"password": PASSWORD},
    "change_password_api_v1_me_password_patch": {"currentPassword": PASSWORD, "newPassword": PASSWORD + "!"},
    "create_organization_api_v1_organizations_post": {"name": "X"},
    "update_organization_api_v1_organizations__organization_id__patch": {"name": "X"},
    "change_role_api_v1_organizations__organization_id__members__member_id__patch": {"role": "viewer"},
    "create_project_api_v1_organizations__organization_id__projects_post": {"name": "Food Delivery"},
    "update_project_api_v1_projects__project_id__patch": {"name": "Orders"},
    "put_architecture_policy_api_v1_projects__project_id__architecture_policy_put": {"requireTls": True},
    "create_requirement_api_v1_projects__project_id__requirements_post": {
        "type": "functional",
        "category": "order",
        "title": "Place an order",
        "statement": "A customer can place an order.",
        "priority": "high",
        "status": "draft",  # declared: users may create draft or active requirements
    },
    "update_requirement_api_v1_projects__project_id__requirements__requirement_id__patch": {
        "expectedVersion": 1,
        "title": "Place an order quickly",
        "status": "draft",  # declared: status changes go through the lifecycle rules
    },
    "create_requirement_set_api_v1_projects__project_id__requirement_sets_post": {"name": "Baseline"},
    "analyze_requirements_api_v1_projects__project_id__requirement_analyses_post": {
        "input": "Support 2000 rps."
    },
    "promote_candidates_api_v1_projects__project_id__requirement_analyses__analysis_id__promote_post": {
        "candidateKeys": ["cand_0000000000000000"]
    },
    "create_architecture_api_v1_projects__project_id__architectures_post": {
        "name": "Sweep",
        "ir": {
            "schema_version": 1,
            "name": "Sweep",
            "nodes": [{"id": "api", "kind": "service", "name": "API"}],
        },
        "source": "import",  # declared: user or import, never ai, discovery or system
    },
    "update_architecture_api_v1_projects__project_id__architectures__architecture_id__patch": {
        "name": "Renamed"
    },
    "replace_architecture_content_api_v1_projects__project_id__architectures__architecture_id__content_put": {
        "baseVersion": 1,
        "ir": {"schema_version": 1, "name": "Sweep"},
    },
    "edit_architecture_api_v1_projects__project_id__architectures__architecture_id__commands_post": {
        "baseVersion": 1,
        "commands": [{"type": "change_replicas", "nodeId": "api", "replicas": 2}],
    },
    "save_architecture_layout_api_v1_projects__project_id__architectures__architecture_id__layout_put": {
        "positions": {"api": {"x": 1, "y": 2}}
    },
    "restore_architecture_version_api_v1_projects__project_id__architectures__architecture_id"
    "__versions__version__restore_post": {"baseVersion": 1},
    "run_validation_api_v1_projects__project_id__architectures__architecture_id__validations_post": {
        "profile": "default"
    },
    "run_capacity_analysis_api_v1_projects__project_id__architectures__architecture_id"
    "__capacity_analyses_post": {"workload": WORKLOAD},
    "run_cost_analysis_api_v1_projects__project_id__architectures__architecture_id__cost_analyses_post": {
        "snapshotId": "01a0de60-a784-7244-8ecc-62afb5352f47"
    },
    "run_reliability_analysis_api_v1_projects__project_id__architectures__architecture_id"
    "__reliability_analyses_post": {"label": "Sweep"},
    "run_security_analysis_api_v1_projects__project_id__architectures__architecture_id"
    "__security_analyses_post": {"label": "Sweep"},
    "run_observability_analysis_api_v1_projects__project_id__architectures__architecture_id"
    "__observability_analyses_post": {"label": "Sweep"},
    "run_evolution_analysis_api_v1_projects__project_id__architectures__architecture_id"
    "__evolution_analyses_post": {
        "goals": [{"type": "availability_objective", "target": {"value": "0.999", "unit": "ratio"}}]
    },
    "draft_decision_api_v1_projects__project_id__decisions_post": {
        "architectureId": "01a0de60-a784-7244-8ecc-62afb5352f47",
        "analysisId": "01a0de60-a784-7244-8ecc-62afb5352f48",
    },
    "accept_decision_api_v1_projects__project_id__decisions__decision_id__accept_post": {
        "candidateId": "evo_" + "0" * 20,
        "rationale": "Fits.",
    },
    "reject_decision_api_v1_projects__project_id__decisions__decision_id__reject_post": {"rationale": "No."},
    "supersede_decision_api_v1_projects__project_id__decisions__decision_id__supersede_post": {
        "byDecisionId": "01a0de60-a784-7244-8ecc-62afb5352f49"
    },
    "link_decision_revision_api_v1_projects__project_id__decisions__decision_id__resulting_revision_post": {
        "revision": 2
    },
    "create_plan_api_v1_projects__project_id__migration_plans_post": MIGRATION_REQUEST,
    "regenerate_plan_api_v1_projects__project_id__migration_plans__plan_id__regenerate_post": {
        "request": MIGRATION_REQUEST,
        "replaceReviewed": False,
    },
    "approve_plan_api_v1_projects__project_id__migration_plans__plan_id__versions__version__approve_post": {
        "fingerprint": "0" * 64
    },
    "reject_plan_api_v1_projects__project_id__migration_plans__plan_id__versions__version__reject_post": {
        "fingerprint": "0" * 64,
        "comment": "No.",
    },
    "run_simulation_api_v1_projects__project_id__architectures__architecture_id__simulations_post": {
        "scenario": {"name": "Sweep", "workload": {"growth": "2"}},
        "label": "Sweep",
    },
    "evaluate_component_configuration_api_v1_components__directory___entry__evaluate_post": {
        "nodeKind": "queue",
        "configuration": {"values": {"retention_seconds": 60}},
    },
    "create_invitation_api_v1_organizations__organization_id__invitations_post": {
        "email": "y@example.com",
        "role": "member",
    },
}


def test_every_body_endpoint_is_in_the_sweep(app: FastAPI) -> None:
    with_body = {op.operation_id for op in inventory(app) if op.has_body}
    assert with_body == set(VALID_BODIES)


@pytest.mark.parametrize("field", list(PRIVILEGED))
async def test_every_body_rejects_undeclared_privileged_fields(
    app: FastAPI, client: AsyncClient, outbox: InMemoryTransport, field: str
) -> None:
    ada = await signed_in(client, outbox, "ada@example.com")
    org_id = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()["id"]
    members = (await client.get(f"/api/v1/organizations/{org_id}/members", headers=ada)).json()["members"]
    member_id = members[0]["id"]
    created = await client.post(
        f"/api/v1/organizations/{org_id}/projects", json={"name": "Sweep"}, headers=ada
    )
    project_id = created.json()["id"]
    requirement_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/requirements",
            json=VALID_BODIES["create_requirement_api_v1_projects__project_id__requirements_post"],
            headers=ada,
        )
    ).json()["id"]

    for op in (o for o in inventory(app) if o.has_body):
        body = VALID_BODIES[op.operation_id] | {field: PRIVILEGED[field]}
        if field in VALID_BODIES[op.operation_id]:
            continue  # a declared field (e.g. role on change_role) is validated by the endpoint itself
        response = await client.request(
            op.method,
            op.url(
                organization_id=org_id,
                member_id=member_id,
                project_id=project_id,
                requirement_id=requirement_id,
            ),
            json=body,
            headers=ada | WEB,
        )
        assert_error_envelope(response, 422, "validation_error")
        fields = [f["field"] for f in response.json()["error"]["details"]["fields"]]
        assert any(field in f for f in fields), (op.operation_id, fields)
