"""Every endpoint, taken from the OpenAPI document, is either deliberately public or refuses
requests that are not properly authenticated."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from apps.api.email.transport import InMemoryTransport
from tests.unit.identity.fakes import FakeClock

from .support import WEB, assert_error_envelope, forged_token, inventory, signed_in

# Changing this set is a security decision: every other endpoint must require a Bearer token.
PUBLIC = {
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/verify-email"),
    ("POST", "/api/v1/auth/resend-verification"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/forgot-password"),
    ("POST", "/api/v1/auth/reset-password"),
}


def test_the_public_surface_is_exactly_the_expected_one(app: FastAPI) -> None:
    public = {(op.method, op.path) for op in inventory(app) if not op.protected}
    assert public == PUBLIC


# The endpoint list of the authentication specification, exactly: nothing missing, nothing extra.
SPECIFIED = PUBLIC | {
    ("GET", "/api/v1/me"),
    ("PATCH", "/api/v1/me"),
    ("PATCH", "/api/v1/me/password"),
    ("DELETE", "/api/v1/me"),
    ("GET", "/api/v1/me/sessions"),
    ("DELETE", "/api/v1/me/sessions/{session_id}"),
    ("GET", "/api/v1/organizations"),
    ("POST", "/api/v1/organizations"),
    ("GET", "/api/v1/organizations/{organization_id}"),
    ("PATCH", "/api/v1/organizations/{organization_id}"),
    ("DELETE", "/api/v1/organizations/{organization_id}"),
    ("GET", "/api/v1/organizations/{organization_id}/members"),
    ("PATCH", "/api/v1/organizations/{organization_id}/members/{member_id}"),
    ("DELETE", "/api/v1/organizations/{organization_id}/members/{member_id}"),
    ("GET", "/api/v1/organizations/{organization_id}/invitations"),
    ("POST", "/api/v1/organizations/{organization_id}/invitations"),
    ("DELETE", "/api/v1/organizations/{organization_id}/invitations/{invitation_id}"),
    ("POST", "/api/v1/invitations/{invitation_token}/accept"),
    ("GET", "/api/v1/organizations/{organization_id}/audit-log"),
    # Projects and requirements specification.
    ("GET", "/api/v1/organizations/{organization_id}/projects"),
    ("POST", "/api/v1/organizations/{organization_id}/projects"),
    ("GET", "/api/v1/projects/{project_id}"),
    ("PATCH", "/api/v1/projects/{project_id}"),
    ("DELETE", "/api/v1/projects/{project_id}"),
    ("POST", "/api/v1/projects/{project_id}/archive"),
    ("POST", "/api/v1/projects/{project_id}/restore"),
    ("GET", "/api/v1/projects/{project_id}/architecture-policy"),
    ("PUT", "/api/v1/projects/{project_id}/architecture-policy"),
    ("GET", "/api/v1/projects/{project_id}/requirements"),
    ("POST", "/api/v1/projects/{project_id}/requirements"),
    ("GET", "/api/v1/projects/{project_id}/requirements/{requirement_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/requirements/{requirement_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/requirements/{requirement_id}"),
    ("GET", "/api/v1/projects/{project_id}/requirements/analysis"),
    ("POST", "/api/v1/projects/{project_id}/requirements/{requirement_id}/validate"),
    ("GET", "/api/v1/projects/{project_id}/requirements/{requirement_id}/versions"),
    ("GET", "/api/v1/projects/{project_id}/requirements/{requirement_id}/versions/{version}"),
    ("POST", "/api/v1/projects/{project_id}/requirement-sets"),
    ("GET", "/api/v1/projects/{project_id}/requirement-sets"),
    ("GET", "/api/v1/projects/{project_id}/requirement-sets/{set_id}"),
    ("GET", "/api/v1/projects/{project_id}/requirement-sets/{set_id}/planning-input"),
    ("POST", "/api/v1/projects/{project_id}/requirement-analyses"),
    ("GET", "/api/v1/projects/{project_id}/requirement-analyses/{analysis_id}"),
    ("POST", "/api/v1/projects/{project_id}/requirement-analyses/{analysis_id}/promote"),
    # Architecture IR specification and Milestone 5 (architecture CRUD and versioning).
    ("POST", "/api/v1/projects/{project_id}/architectures"),
    ("GET", "/api/v1/projects/{project_id}/architectures"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/architectures/{architecture_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/architectures/{architecture_id}"),
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/archive"),
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/restore"),
    ("PUT", "/api/v1/projects/{project_id}/architectures/{architecture_id}/content"),
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/commands"),
    ("PUT", "/api/v1/projects/{project_id}/architectures/{architecture_id}/layout"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/versions"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/versions/{version}"),
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/versions/{version}/restore"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/compare"),
    # Milestone 6 (deterministic validation).
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/validations"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/validations"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/validations/{run_id}"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/validations/{run_id}/findings"),
    ("GET", "/api/v1/validation/rules"),
    # Milestone 7 (deterministic capacity engine).
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/capacity-analyses"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/capacity-analyses"),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/capacity-analyses/{capacity_analysis_id}",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/capacity-analyses/{capacity_analysis_id}/components",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/capacity-analyses/{capacity_analysis_id}/bottlenecks",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/capacity-analyses/{capacity_analysis_id}/scenarios",
    ),
    ("GET", "/api/v1/capacity/models"),
    # Milestone 8 (deterministic cost engine): an organization's pricing snapshots.
    ("POST", "/api/v1/organizations/{organization_id}/pricing-snapshots"),
    ("GET", "/api/v1/organizations/{organization_id}/pricing-snapshots"),
    ("GET", "/api/v1/organizations/{organization_id}/pricing-snapshots/{snapshot_id}"),
    ("GET", "/api/v1/organizations/{organization_id}/pricing-snapshots/{snapshot_id}/records"),
    # Milestone 8: cost analyses of an architecture.
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/cost-analyses"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/cost-analyses"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/cost-analyses/{cost_analysis_id}"),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/cost-analyses/{cost_analysis_id}/line-items",
    ),
    ("GET", "/api/v1/cost/models"),
    # Milestone 9: reliability analyses of an architecture.
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/reliability-analyses"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/reliability-analyses"),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/reliability-analyses/{reliability_analysis_id}",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/reliability-analyses/{reliability_analysis_id}/components",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/reliability-analyses/{reliability_analysis_id}/findings",
    ),
    ("GET", "/api/v1/reliability/models"),
    # Milestone 10: security analyses of an architecture.
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/security-analyses"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/security-analyses"),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/security-analyses/{security_analysis_id}",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/security-analyses/{security_analysis_id}/components",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/security-analyses/{security_analysis_id}/findings",
    ),
    ("GET", "/api/v1/security/analyzers"),
    # Milestone 11: observability analyses of an architecture.
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/observability-analyses"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/observability-analyses"),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/observability-analyses/{observability_analysis_id}",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/observability-analyses/{observability_analysis_id}/components",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/observability-analyses/{observability_analysis_id}/findings",
    ),
    ("GET", "/api/v1/observability/analyzers"),
    # Milestone 12: simulations of an architecture.
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/simulations"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/simulations"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/simulations/{simulation_id}"),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/simulations/{simulation_id}/components",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/simulations/{simulation_id}/deltas",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/simulations/{simulation_id}"
        "/comparisons/{other_simulation_id}",
    ),
    ("GET", "/api/v1/simulation/catalog"),
    # Milestone 13: evolution analyses of an architecture, and decision records of a project.
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/evolution-analyses"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/evolution-analyses"),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/evolution-analyses/{evolution_analysis_id}",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/evolution-analyses/{evolution_analysis_id}"
        "/candidates",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/evolution-analyses/{evolution_analysis_id}"
        "/candidates/{candidate_id}",
    ),
    (
        "GET",
        "/api/v1/projects/{project_id}/architectures/{architecture_id}/evolution-analyses/{evolution_analysis_id}"
        "/alternatives",
    ),
    ("GET", "/api/v1/evolution/catalog"),
    ("GET", "/api/v1/components/categories"),
    ("GET", "/api/v1/components"),
    ("GET", "/api/v1/components/{directory}/{entry}"),
    ("GET", "/api/v1/components/{directory}/{entry}/versions"),
    ("POST", "/api/v1/components/{directory}/{entry}/evaluate"),
    ("POST", "/api/v1/projects/{project_id}/decisions"),
    ("GET", "/api/v1/projects/{project_id}/decisions"),
    ("GET", "/api/v1/projects/{project_id}/decisions/{decision_id}"),
    ("GET", "/api/v1/projects/{project_id}/decisions/{decision_id}/document"),
    ("POST", "/api/v1/projects/{project_id}/decisions/{decision_id}/accept"),
    ("POST", "/api/v1/projects/{project_id}/decisions/{decision_id}/reject"),
    ("POST", "/api/v1/projects/{project_id}/decisions/{decision_id}/supersede"),
    ("POST", "/api/v1/projects/{project_id}/decisions/{decision_id}/resulting-revision"),
    # Migration planning: plans between exact revisions of an architecture, and their review.
    ("POST", "/api/v1/projects/{project_id}/migration-plans"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans/{plan_id}"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/steps"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/risks"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/checkpoints"),
    ("GET", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/rollbacks"),
    ("POST", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/regenerate"),
    ("POST", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/submit"),
    ("POST", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/approve"),
    ("POST", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/reject"),
    ("POST", "/api/v1/projects/{project_id}/migration-plans/{plan_id}/versions/{version}/archive"),
    # Discovery: runs over supplied artifacts, their review and explicit acceptance.
    ("POST", "/api/v1/projects/{project_id}/discovery-runs"),
    ("GET", "/api/v1/projects/{project_id}/discovery-runs"),
    ("GET", "/api/v1/projects/{project_id}/discovery-runs/{run_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/discovery-runs/{run_id}"),
    ("GET", "/api/v1/projects/{project_id}/discovery-runs/{run_id}/findings"),
    ("GET", "/api/v1/projects/{project_id}/discovery-runs/{run_id}/proposal"),
    ("POST", "/api/v1/projects/{project_id}/discovery-runs/{run_id}/decisions"),
    ("POST", "/api/v1/projects/{project_id}/discovery-runs/{run_id}/accept"),
    ("GET", "/api/v1/projects/{project_id}/discovery-runs/{run_id}/comparison"),
    ("GET", "/api/v1/projects/{project_id}/discovery-runs/{run_id}/baseline-comparison"),
    # Drift: analyses of a revision against a discovery run, their review, confirmed identities.
    ("POST", "/api/v1/projects/{project_id}/drift-analyses"),
    ("GET", "/api/v1/projects/{project_id}/drift-analyses"),
    ("GET", "/api/v1/projects/{project_id}/drift-analyses/{drift_analysis_id}"),
    ("GET", "/api/v1/projects/{project_id}/drift-analyses/{drift_analysis_id}/findings"),
    ("GET", "/api/v1/projects/{project_id}/drift-analyses/{drift_analysis_id}/findings/{finding_id}"),
    ("GET", "/api/v1/projects/{project_id}/drift-items"),
    ("GET", "/api/v1/projects/{project_id}/drift-items/{item_id}"),
    ("POST", "/api/v1/projects/{project_id}/drift-items/{item_id}/review"),
    ("GET", "/api/v1/projects/{project_id}/architectures/{architecture_id}/identity-mappings"),
    ("POST", "/api/v1/projects/{project_id}/architectures/{architecture_id}/identity-mappings"),
    # Knowledge: sources, their ingestions and passages, and search.
    ("POST", "/api/v1/projects/{project_id}/knowledge-sources"),
    ("GET", "/api/v1/projects/{project_id}/knowledge-sources"),
    ("GET", "/api/v1/projects/{project_id}/knowledge-sources/{source_id}"),
    ("POST", "/api/v1/projects/{project_id}/knowledge-sources/{source_id}/ingestions"),
    ("GET", "/api/v1/projects/{project_id}/knowledge-sources/{source_id}/ingestions"),
    ("GET", "/api/v1/projects/{project_id}/knowledge-sources/{source_id}/ingestions/{ingestion_id}"),
    ("POST", "/api/v1/projects/{project_id}/knowledge-sources/{source_id}/archive"),
    ("GET", "/api/v1/projects/{project_id}/knowledge-sources/{source_id}/passages/{chunk_id}"),
    ("POST", "/api/v1/projects/{project_id}/knowledge/search"),
}


def test_the_api_is_exactly_the_specified_endpoint_list(app: FastAPI) -> None:
    assert {(op.method, op.path) for op in inventory(app)} == SPECIFIED


@pytest.mark.parametrize(
    ("label", "header"),
    [
        ("missing", None),
        ("empty", "Bearer "),
        ("wrong-scheme", "Basic YWRhOnB3"),
        ("garbage", "Bearer not-a-jwt"),
        ("forged", f"Bearer {forged_token(secret='a-different-secret-' + 'y' * 32)}"),
    ],
)
async def test_every_protected_endpoint_refuses_bad_credentials(
    app: FastAPI, client: AsyncClient, label: str, header: str | None
) -> None:
    headers = {"Authorization": header} if header else {}
    for op in (o for o in inventory(app) if o.protected):
        response = await client.request(
            op.method, op.url(), headers=headers, json={} if op.has_body else None
        )
        assert_error_envelope(response, 401)
        assert response.headers["www-authenticate"].startswith("Bearer"), (label, op)


async def test_every_protected_endpoint_refuses_an_expired_token(
    app: FastAPI, client: AsyncClient, outbox: InMemoryTransport, clock: FakeClock
) -> None:
    auth = await signed_in(client, outbox, "ada@example.com")
    clock.advance(timedelta(minutes=15))
    for op in (o for o in inventory(app) if o.protected):
        response = await client.request(op.method, op.url(), headers=auth, json={} if op.has_body else None)
        assert_error_envelope(response, 401, "access_token_expired")


async def test_every_protected_endpoint_refuses_a_token_whose_session_ended(
    app: FastAPI, client: AsyncClient, outbox: InMemoryTransport
) -> None:
    auth = await signed_in(client, outbox, "ada@example.com")
    client.cookies.clear()
    # Log out by revoking this session through the API itself.
    sessions = (await client.get("/api/v1/me/sessions", headers=auth)).json()["sessions"]
    assert (await client.delete(f"/api/v1/me/sessions/{sessions[0]['id']}", headers=auth)).status_code == 204
    for op in (o for o in inventory(app) if o.protected):
        response = await client.request(op.method, op.url(), headers=auth, json={} if op.has_body else None)
        assert_error_envelope(response, 401, "session_revoked")


async def test_cookie_endpoints_refuse_cross_site_requests(app: FastAPI, client: AsyncClient) -> None:
    for path in ("/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout"):
        response = await client.post(path, json={}, headers={"Origin": "https://evil.example"})
        assert_error_envelope(response, 403, "csrf_rejected")
    assert WEB  # the web app's headers are what makes these pass elsewhere
