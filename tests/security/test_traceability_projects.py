"""The Definition of Done for projects (spec section 56) and requirements (section 57), and the
security test matrix (section 50), each mapped to the tests that prove it. Fails if a mapped test
is renamed or removed, or if an item is left unmapped."""

import importlib

import pytest

U = "tests.unit"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"

PROJECTS_DONE: dict[str, list[str]] = {
    "database model": [f"{I}.database.test_project_constraints::test_defaults"],
    "migration": [f"{I}.database.test_migrations::test_upgrading_an_existing_auth_database_keeps_its_data"],
    "indexes": [
        f"{I}.database.test_query_plans::test_every_hot_query_uses_its_index",
        f"{I}.database.test_project_constraints::test_slug_is_unique_within_an_organization_only",
    ],
    "constraints": [
        f"{I}.database.test_project_constraints::test_field_rules",
        f"{I}.database.test_project_constraints::test_deletion_requires_the_archived_state",
        f"{I}.database.test_project_constraints::test_an_organization_owning_projects_cannot_be_hard_deleted",
    ],
    "domain entity": [
        f"{U}.projects.test_project_entity::test_creation_normalizes_and_derives_the_slug",
        f"{U}.projects.test_project_entity::test_organization_and_slug_cannot_be_changed_through_the_entity",
    ],
    "repository": [
        f"{I}.database.test_project_repository::test_save_persists_lifecycle_and_changes_but_not_ownership"
    ],
    "API schemas": [f"{I}.api.test_projects::test_ownership_slug_and_lifecycle_cannot_be_patched"],
    "API routes": [f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list"],
    "authentication": [f"{S}.test_project_access::test_signed_out_callers_are_refused_before_anything_else"],
    "organization authorization": [
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{S}.test_project_access::test_leaving_the_organization_removes_project_access_at_once",
    ],
    "project authorization": [
        f"{I}.api.test_projects::test_role_permissions",
        f"{I}.api.test_project_lifecycle::test_who_may_archive_restore_and_delete",
    ],
    "create": [f"{I}.api.test_projects::test_create_returns_the_project_with_derived_slug_and_role"],
    "list": [f"{I}.api.test_projects::test_listing_paginates_sorts_and_filters"],
    "get": [f"{I}.api.test_projects::test_get_and_update"],
    "update": [f"{U}.projects.test_project_service::test_update_changes_only_allowed_fields_and_is_audited"],
    "archive": [
        f"{U}.projects.test_project_lifecycle::test_archive_and_restore_are_idempotent_and_audited_once"
    ],
    "restore": [f"{U}.projects.test_project_lifecycle::test_archived_projects_reject_edits_until_restored"],
    "delete/lifecycle": [
        f"{I}.api.test_project_lifecycle::test_archive_restore_delete_journey",
        f"{I}.api.test_project_lifecycle::test_a_deleted_projects_slug_can_be_used_again",
    ],
    "pagination": [f"{U}.projects.test_project_service::test_listing_pages_through_every_project_once"],
    "search/filtering": [f"{I}.api.test_projects::test_search_is_literal_and_case_insensitive"],
    "audit logging": [f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text"],
    "unit tests": [f"{U}.projects.test_project_values::test_slugify_is_deterministic_and_url_safe"],
    "integration tests": [f"{I}.database.test_project_repository::test_round_trip_including_settings"],
    "API tests": [f"{I}.api.test_projects_requirements_journey::test_the_full_journey"],
    "security tests": [
        f"{S}.test_project_access::test_a_project_cannot_be_moved_or_planted_across_organizations"
    ],
    "migration tests": [
        f"{I}.database.test_migrations::test_downgrading_projects_leaves_the_auth_schema_intact"
    ],
    "documentation": [
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_documentation::test_the_decisions_are_recorded",
    ],
}

REQUIREMENTS_DONE: dict[str, list[str]] = {
    "database model": [
        f"{I}.database.test_requirement_constraints::test_a_requirement_with_its_first_version"
    ],
    "migration": [f"{I}.database.test_migrations::test_downgrading_requirements_leaves_projects_intact"],
    "indexes": [f"{I}.database.test_query_plans::test_every_hot_query_uses_its_index"],
    "constraints": [
        f"{I}.database.test_requirement_constraints::test_requirement_field_rules",
        f"{I}.database.test_requirement_constraints::test_the_current_version_must_exist",
    ],
    "domain entity": [
        f"{U}.requirements.test_requirement_entity::test_a_material_change_is_a_new_version_and_keeps_identity"
    ],
    "requirement taxonomy": [
        f"{U}.requirements.test_requirement_rules::test_every_type_has_known_categories",
        f"{U}.requirements.test_requirement_rules::test_every_metric_is_reachable_from_each_of_its_types",
    ],
    "structured representation": [
        f"{U}.requirements.test_requirement_values::test_a_quantity_constraint_round_trips"
    ],
    "validation": [
        f"{U}.requirements.test_requirement_rules::test_impossible_values_are_out_of_range",
        f"{I}.api.test_requirements::test_invalid_requirements",
    ],
    "normalization": [
        f"{U}.requirements.test_requirement_normalization::test_spec_examples_reach_their_canonical_values",
        f"{U}.requirements.test_requirement_normalization::test_ambiguous_or_malformed_quantities_are_refused",
    ],
    "CRUD API": [
        f"{I}.api.test_requirements::test_create_and_read",
        f"{I}.api.test_requirements::test_update_creates_versions_and_is_audited",
        f"{I}.api.test_requirements::test_delete_is_soft",
    ],
    "authorization": [f"{I}.api.test_requirements::test_roles"],
    "pagination": [f"{I}.api.test_requirements::test_list_paginates_and_filters"],
    "filtering": [f"{I}.database.test_requirement_repository::test_list_filters_search_and_keyset"],
    "status lifecycle": [
        f"{U}.requirements.test_requirement_rules::test_status_transitions",
        f"{I}.api.test_requirements::test_lifecycle_errors",
    ],
    "priority": [f"{U}.requirements.test_requirement_entity::test_confidence_is_not_priority"],
    "source": [
        f"{U}.requirements.test_requirement_entity::test_ai_requirements_are_drafts_with_a_confidence"
    ],
    "confidence": [
        f"{U}.requirements.test_requirement_values::test_invalid_confidence",
        f"{U}.requirements.test_requirement_entity::test_people_do_not_state_a_confidence",
    ],
    "version history": [
        f"{I}.api.test_requirement_history::test_every_change_is_kept_in_order_with_its_reason"
    ],
    "immutable history": [
        f"{I}.database.test_requirement_constraints::test_version_history_is_append_only",
        f"{I}.api.test_requirement_history::test_history_cannot_be_modified_through_the_api",
    ],
    "conflict detection": [
        f"{U}.requirements.test_requirement_analysis::test_the_specs_direct_conflict",
        f"{U}.requirements.test_requirement_analysis::test_tensions_are_not_conflicts",
    ],
    "completeness analysis": [
        f"{U}.requirements.test_requirement_analysis::test_completeness_names_covered_and_missing_concerns"
    ],
    "audit logging": [
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
        f"{I}.api.test_event_logs::test_committed_events_are_logged_with_their_context",
    ],
    "architecture boundary contract": [
        f"{U}.requirements.test_planning_input::test_the_contract_shape",
        f"{I}.api.test_requirement_sets::test_sets_are_reproducible_after_requirements_change",
    ],
    "unit tests": [
        f"{U}.requirements.test_requirement_service::test_update_appends_a_version_and_audits_what_changed"
    ],
    "integration tests": [
        f"{I}.database.test_requirement_repository::test_save_appends_a_version_and_moves_the_current_state"
    ],
    "API tests": [f"{I}.api.test_projects_requirements_journey::test_the_full_journey"],
    "security tests": [
        f"{S}.test_requirement_security_matrix::test_malicious_structured_data_is_data_and_is_refused"
    ],
    "migration tests": [
        f"{I}.database.test_migrations::test_downgrading_requirement_sets_leaves_requirements_intact"
    ],
    "documentation": [
        f"{S}.test_documentation::test_the_taxonomy_metrics_and_units_are_documented",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
}

SECURITY_MATRIX: dict[str, list[str]] = {
    "cross-organization project access rejected": [
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing"
    ],
    "cross-organization requirement access rejected": [
        f"{I}.api.test_requirement_history::test_history_is_tenant_and_project_scoped",
        f"{U}.requirements.test_requirement_service::test_strangers_cannot_tell_the_project_exists",
    ],
    "cross-project requirement access rejected": [
        f"{I}.api.test_requirements::test_a_requirement_is_only_reachable_through_its_project"
    ],
    "viewer cannot mutate project": [
        f"{U}.projects.test_project_service::test_viewers_cannot_create_or_update"
    ],
    "viewer cannot mutate requirement": [
        f"{U}.requirements.test_requirement_service::test_viewers_read_but_cannot_write"
    ],
    "member permissions enforced": [
        f"{U}.projects.test_project_lifecycle::test_members_cannot_archive_restore_or_delete"
    ],
    "admin permissions enforced": [f"{U}.organizations.test_permissions::test_role_permission_matrix"],
    "archived project mutation blocked where required": [
        f"{I}.api.test_requirements::test_archived_projects_are_read_only",
        f"{I}.api.test_projects::test_archived_projects_are_read_only",
    ],
    "project cannot change organization": [
        f"{S}.test_project_access::test_a_project_cannot_be_moved_or_planted_across_organizations"
    ],
    "requirement cannot change project": [
        f"{S}.test_requirement_security_matrix::test_a_requirement_cannot_be_moved_to_another_project",
        f"{I}.database.test_requirement_set_persistence::test_a_set_cannot_pin_another_projects_requirement",
    ],
    "invalid structured data rejected": [
        f"{U}.requirements.test_requirement_values::test_malformed_structured_data"
    ],
    "malicious JSON does not execute": [
        f"{S}.test_requirement_security_matrix::test_malicious_structured_data_is_data_and_is_refused",
        f"{S}.test_requirement_security_matrix::test_deeply_nested_json_is_refused_cleanly",
    ],
    "pagination cannot bypass authorization": [
        f"{S}.test_requirement_security_matrix::test_a_cursor_does_not_open_another_tenants_project",
        f"{S}.test_project_access::test_pagination_and_search_never_cross_organizations",
    ],
    "search cannot bypass authorization": [
        f"{S}.test_requirement_security_matrix::test_search_stays_inside_the_project"
    ],
    "deleted/archived resources handled safely": [
        f"{U}.projects.test_project_entity::test_deleted_projects_behave_as_missing",
        f"{I}.api.test_requirement_history::test_deleted_requirements_hide_their_history",
        f"{I}.api.test_requirement_sets::test_archived_projects_cannot_get_new_sets",
    ],
}


SPEC_PROJECTS = [
    "database model", "migration", "indexes", "constraints", "domain entity", "repository", "API schemas",
    "API routes", "authentication", "organization authorization", "project authorization", "create", "list",
    "get", "update", "archive", "restore", "delete/lifecycle", "pagination", "search/filtering",
    "audit logging", "unit tests", "integration tests", "API tests", "security tests", "migration tests",
    "documentation",
]  # fmt: skip
SPEC_REQUIREMENTS = [
    "database model", "migration", "indexes", "constraints", "domain entity", "requirement taxonomy",
    "structured representation", "validation", "normalization", "CRUD API", "authorization", "pagination",
    "filtering", "status lifecycle", "priority", "source", "confidence", "version history",
    "immutable history", "conflict detection", "completeness analysis", "audit logging",
    "architecture boundary contract", "unit tests", "integration tests", "API tests", "security tests",
    "migration tests", "documentation",
]  # fmt: skip

ALL = [
    *((f"projects: {k}", v) for k, v in PROJECTS_DONE.items()),
    *((f"requirements: {k}", v) for k, v in REQUIREMENTS_DONE.items()),
    *((f"matrix: {k}", v) for k, v in SECURITY_MATRIX.items()),
]


@pytest.mark.parametrize(("item", "tests"), ALL, ids=[item for item, _ in ALL])
def test_item_is_proven_by_existing_tests(item: str, tests: list[str]) -> None:
    assert tests, item
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{item}: {reference} not found"


def test_every_item_of_the_spec_is_mapped() -> None:
    assert set(PROJECTS_DONE) == set(SPEC_PROJECTS)
    assert set(REQUIREMENTS_DONE) == set(SPEC_REQUIREMENTS)
    assert len(SECURITY_MATRIX) == 15
    assert (len(SPEC_PROJECTS), len(SPEC_REQUIREMENTS)) == (27, 29)
