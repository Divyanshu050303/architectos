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
VALID_BODIES: dict[str, dict[str, object]] = {
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
    member_id = (await client.get(f"/api/v1/organizations/{org_id}/members", headers=ada)).json()["members"][
        0
    ]["id"]

    for op in (o for o in inventory(app) if o.has_body):
        body = VALID_BODIES[op.operation_id] | {field: PRIVILEGED[field]}
        if field in VALID_BODIES[op.operation_id]:
            continue  # a declared field (e.g. role on change_role) is validated by the endpoint itself
        response = await client.request(
            op.method, op.url(organization_id=org_id, member_id=member_id), json=body, headers=ada | WEB
        )
        assert_error_envelope(response, 422, "validation_error")
        fields = [f["field"] for f in response.json()["error"]["details"]["fields"]]
        assert any(field in f for f in fields), (op.operation_id, fields)
