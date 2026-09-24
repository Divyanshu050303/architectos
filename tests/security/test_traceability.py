"""The security test matrix (spec section 28) and edge cases (section 29), each mapped to the tests
that prove it. Fails if a mapped test is renamed or removed, keeping docs/security honest."""

import importlib

import pytest

MATRIX: dict[str, list[str]] = {
    "wrong password": [
        "tests.integration.api.test_sessions::test_wrong_password_and_unknown_email_are_indistinguishable"
    ],
    "unknown email": [
        "tests.unit.identity.test_sessions::test_wrong_password_and_unknown_email_fail_identically"
    ],
    "expired access token": [
        "tests.integration.api.test_sessions::test_expired_access_token_asks_for_a_refresh",
        "tests.security.test_authentication_sweep::test_every_protected_endpoint_refuses_an_expired_token",
    ],
    "invalid access token": [
        "tests.unit.api.test_access_tokens::test_forged_or_foreign_tokens_are_rejected",
        "tests.security.test_authentication_sweep::test_every_protected_endpoint_refuses_bad_credentials",
    ],
    "expired refresh token": ["tests.integration.api.test_sessions::test_expired_session_clears_the_cookie"],
    "revoked refresh token": [
        "tests.unit.identity.test_sessions::test_revoked_session_cannot_refresh",
        "tests.integration.api.test_session_management::test_logout_ends_the_session_everywhere",
    ],
    "refresh token reuse": [
        "tests.integration.api.test_sessions::test_reused_refresh_token_revokes_the_session_everywhere",
        "tests.integration.database.test_refresh_race::test_concurrent_refreshes_rotate_once_and_revoke_nothing",
    ],
    "expired verification token": ["tests.integration.api.test_email_verification::test_expired_token"],
    "verification token reuse": [
        "tests.integration.api.test_email_verification::test_valid_token_verifies_once",
        "tests.integration.database.test_email_verification_race::test_concurrent_verification_consumes_the_token_once",
    ],
    "expired password reset token": ["tests.integration.api.test_passwords::test_expired_reset_token"],
    "password reset token reuse": [
        "tests.integration.api.test_passwords::test_reset_token_cannot_be_reused",
        "tests.integration.database.test_password_reset_race::test_concurrent_resets_with_one_link_succeed_once",
    ],
    "cross-organization access": [
        "tests.security.test_tenant_isolation_sweep::test_a_stranger_gets_404_everywhere_and_changes_nothing",
        "tests.integration.api.test_organizations::test_a_non_member_cannot_see_modify_or_delete_another_tenant",
        "tests.integration.api.test_members::test_members_of_another_organization_are_out_of_reach",
        "tests.integration.api.test_invitations::test_other_tenants_cannot_see_or_revoke_invitations",
        "tests.integration.api.test_audit_log::test_tenants_only_see_their_own_trail",
    ],
    "privilege escalation": [
        "tests.unit.organizations.test_membership_policy::test_role_changes_follow_the_spec",
        "tests.unit.organizations.test_invitations::test_invitation_policy",
        "tests.security.test_mass_assignment_sweep::test_every_body_rejects_undeclared_privileged_fields",
    ],
    "viewer write access": [
        "tests.unit.organizations.test_permissions::test_viewers_can_never_write",
        "tests.unit.organizations.test_membership_policy::test_viewer_cannot_change_roles_or_remove_anyone",
    ],
    "member role modification": [
        "tests.unit.organizations.test_membership_policy::test_member_cannot_change_roles_or_remove_admins"
    ],
    "admin owner-only operations": [
        "tests.unit.organizations.test_membership_policy::test_admin_cannot_do_owner_only_things"
    ],
    "final-owner removal": [
        "tests.unit.organizations.test_membership_policy::test_the_final_owner_can_neither_be_demoted_nor_removed_nor_leave",
        "tests.integration.api.test_members::test_the_last_owner_cannot_leave",
        "tests.integration.api.test_members::test_sole_owner_must_transfer_before_deleting_their_account",
        "tests.integration.database.test_membership_races::test_two_owners_leaving_at_once",
    ],
    "invitation email mismatch": [
        "tests.integration.api.test_invitations::test_only_the_invited_address_can_accept"
    ],
    "revoked invitation": ["tests.integration.api.test_invitations::test_revoked_invitation"],
    "expired invitation": ["tests.integration.api.test_invitations::test_expired_invitation"],
    "session revocation": [
        "tests.integration.api.test_session_management::test_revoke_another_of_my_sessions",
        "tests.security.test_authentication_sweep::test_every_protected_endpoint_refuses_a_token_whose_session_ended",
    ],
    "password reset invalidating sessions": [
        "tests.integration.api.test_passwords::test_reset_flow_end_to_end"
    ],
    "rate limit behavior": [
        "tests.integration.api.test_hardening::test_login_is_limited_per_email_even_with_the_right_password",
        "tests.integration.api.test_hardening::test_unauthenticated_endpoints_are_limited",
    ],
}

EDGE_CASES: dict[str, list[str]] = {
    "duplicate registration": [
        "tests.integration.api.test_register::test_duplicate_registration_is_indistinguishable"
    ],
    "email case differences": [
        "tests.integration.database.test_constraints::test_email_is_unique_regardless_of_case"
    ],
    "leading/trailing email whitespace": [
        "tests.unit.identity.test_value_objects::test_email_is_trimmed_and_lower_cased"
    ],
    "multiple verification requests": [
        "tests.integration.api.test_email_verification::test_resend_is_throttled_and_supersedes_the_old_link"
    ],
    "multiple password reset requests": [
        "tests.unit.identity.test_passwords_flows::test_repeated_requests_are_throttled_and_supersede_old_links"
    ],
    "multiple sessions": ["tests.integration.api.test_session_management::test_list_my_sessions"],
    "logout from already revoked session": [
        "tests.integration.api.test_session_management::test_logging_out_twice_succeeds"
    ],
    "expired session": ["tests.unit.identity.test_sessions::test_expired_session_cannot_refresh"],
    "organization with one member": [
        "tests.integration.api.test_members::test_deleting_an_account_removes_organizations_it_was_alone_in"
    ],
    "attempt to remove final owner": ["tests.integration.api.test_members::test_the_last_owner_cannot_leave"],
    "inviting existing member": [
        "tests.unit.organizations.test_invitations::test_existing_members_cannot_be_invited"
    ],
    "duplicate invitation": [
        "tests.integration.api.test_invitations::test_reinviting_replaces_the_pending_invitation"
    ],
    "accepting invitation twice": ["tests.unit.organizations.test_invitations::test_accepting_twice"],
    "concurrent invitation acceptance": [
        "tests.integration.database.test_invitation_race::test_concurrent_acceptance_creates_one_membership"
    ],
}


@pytest.mark.parametrize(
    ("requirement", "tests"),
    [*MATRIX.items(), *EDGE_CASES.items()],
    ids=[*MATRIX, *EDGE_CASES],
)
def test_requirement_is_proven_by_existing_tests(requirement: str, tests: list[str]) -> None:
    assert tests, requirement
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{requirement}: {reference} not found"


def test_every_listed_requirement_is_mapped() -> None:
    assert len(MATRIX) == 23
    assert len(EDGE_CASES) == 14
