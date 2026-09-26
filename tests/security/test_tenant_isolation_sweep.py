"""A member of one organization cannot reach any organization-scoped endpoint of another, and
probing leaves the target's members, invitations and audit trail untouched."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from apps.api.email.transport import InMemoryTransport

from .support import assert_error_envelope, inventory, signed_in
from .test_mass_assignment_sweep import VALID_BODIES


@pytest.fixture
async def acme(client: AsyncClient, outbox: InMemoryTransport) -> tuple[str, dict[str, str]]:
    ada = await signed_in(client, outbox, "ada@example.com")
    org_id: str = (await client.post("/api/v1/organizations", json={"name": "Acme"}, headers=ada)).json()[
        "id"
    ]
    await client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        json={"email": "bob@example.com", "role": "member"},
        headers=ada,
    )
    return org_id, ada


async def snapshot(client: AsyncClient, org_id: str, owner: dict[str, str]) -> tuple[object, ...]:
    base = f"/api/v1/organizations/{org_id}"
    return (
        (await client.get(base, headers=owner)).json(),
        (await client.get(f"{base}/members", headers=owner)).json(),
        (await client.get(f"{base}/invitations", headers=owner)).json(),
        (await client.get(f"{base}/audit-log", headers=owner)).json(),
    )


async def test_a_stranger_gets_404_everywhere_and_changes_nothing(
    app: FastAPI, client: AsyncClient, outbox: InMemoryTransport, acme: tuple[str, dict[str, str]]
) -> None:
    org_id, ada = acme
    members = (await client.get(f"/api/v1/organizations/{org_id}/members", headers=ada)).json()["members"]
    invitations = (await client.get(f"/api/v1/organizations/{org_id}/invitations", headers=ada)).json()[
        "invitations"
    ]
    before = await snapshot(client, org_id, ada)
    grace = await signed_in(client, outbox, "grace@example.com")
    await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)  # she owns something

    scoped = [op for op in inventory(app) if "{organization_id}" in op.path]
    assert len(scoped) >= 10
    for op in scoped:
        url = op.url(organization_id=org_id, member_id=members[0]["id"], invitation_id=invitations[0]["id"])
        body = {"name": "Hijacked", "role": "owner", "email": "mallory@example.com"} if op.has_body else None
        response = await client.request(op.method, url, headers=grace, json=body)
        assert_error_envelope(response, 404, "organization_not_found")

    assert await snapshot(client, org_id, ada) == before


async def test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing(
    app: FastAPI, client: AsyncClient, outbox: InMemoryTransport, acme: tuple[str, dict[str, str]]
) -> None:
    """Covers projects and everything under them (requirements), with valid bodies and with
    bodies that try to smuggle in another organization."""
    org_id, ada = acme
    project = (
        await client.post(f"/api/v1/organizations/{org_id}/projects", json={"name": "Secret"}, headers=ada)
    ).json()
    requirement = (
        await client.post(
            f"/api/v1/projects/{project['id']}/requirements",
            json=VALID_BODIES["create_requirement_api_v1_projects__project_id__requirements_post"],
            headers=ada,
        )
    ).json()
    architecture = (
        await client.post(
            f"/api/v1/projects/{project['id']}/architectures", json={"name": "Secret design"}, headers=ada
        )
    ).json()
    run = (
        await client.post(
            f"/api/v1/projects/{project['id']}/architectures/{architecture['id']}/validations",
            json={},
            headers=ada,
        )
    ).json()
    analysis = (
        await client.post(
            f"/api/v1/projects/{project['id']}/architectures/{architecture['id']}/capacity-analyses",
            json={
                "workload": {
                    "name": "Peak",
                    "type": "request_response",
                    "peakRate": {"value": 100, "unit": "requests/second"},
                }
            },
            headers=ada,
        )
    ).json()
    snapshot = (
        await client.post(
            f"/api/v1/organizations/{org_id}/pricing-snapshots",
            json=VALID_BODIES[
                "create_pricing_snapshot_api_v1_organizations__organization_id__pricing_snapshots_post"
            ],
            headers=ada,
        )
    ).json()
    cost = (
        await client.post(
            f"/api/v1/projects/{project['id']}/architectures/{architecture['id']}/cost-analyses",
            json={"snapshotId": snapshot["id"]},
            headers=ada,
        )
    ).json()
    reliability = (
        await client.post(
            f"/api/v1/projects/{project['id']}/architectures/{architecture['id']}/reliability-analyses",
            json={},
            headers=ada,
        )
    ).json()
    ids = {
        "capacity_analysis_id": analysis["id"],
        "cost_analysis_id": cost["id"],
        "reliability_analysis_id": reliability["id"],
        "project_id": project["id"],
        "requirement_id": requirement["id"],
        "architecture_id": architecture["id"],
        "run_id": run["id"],
    }

    async def state() -> tuple[object, ...]:
        return (
            (await client.get(f"/api/v1/projects/{project['id']}", headers=ada)).json(),
            (await client.get(f"/api/v1/projects/{project['id']}/requirements", headers=ada)).json(),
            (await client.get(f"/api/v1/organizations/{org_id}/audit-log", headers=ada)).json(),
        )

    before = await state()
    grace = await signed_in(client, outbox, "grace@example.com")
    await client.post("/api/v1/organizations", json={"name": "Globex"}, headers=grace)

    scoped = [op for op in inventory(app) if "{project_id}" in op.path]
    assert len(scoped) >= 10
    for op in scoped:
        valid = VALID_BODIES.get(op.operation_id) if op.has_body else None
        response = await client.request(op.method, op.url(**ids), headers=grace, json=valid)
        assert_error_envelope(response, 404, "project_not_found")
        if op.has_body:
            smuggled = (valid or {}) | {"organizationId": org_id, "projectId": project["id"]}
            response = await client.request(op.method, op.url(**ids), headers=grace, json=smuggled)
            # Unknown fields may be rejected before authorization (422); anything else must be a 404.
            if response.status_code != 422:
                assert_error_envelope(response, 404, "project_not_found")

    assert await state() == before
