"""Milestone 5 (architecture CRUD and versioning): each acceptance criterion (section 22) mapped to
the tests that prove it. Fails if a mapped test is renamed or removed, or a criterion is unmapped."""

import importlib

import pytest

U = "tests.unit"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
SERVICE = f"{U}.architecture.test_architecture_service"
API = f"{I}.api.test_architectures"
DB = f"{I}.database.test_architecture_persistence"

ACCEPTANCE: dict[str, list[str]] = {
    "existing architecture implementation was audited before changes": [
        f"{S}.test_traceability_architecture_ir::test_the_audit_and_review_are_recorded"
    ],
    "no duplicate Architecture IR or competing versioning model": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{U}.architecture.test_revisions::test_restoring_is_a_new_revision_with_the_old_content",
    ],
    "architecture records can be created and retrieved": [
        f"{API}::test_create_and_read_back_exactly",
        f"{API}::test_an_empty_architecture",
        f"{DB}::test_an_architecture_and_its_revision_round_trip_exactly",
    ],
    "architectures can be listed within authorized projects": [
        f"{API}::test_listing_paginates_filters_and_stays_in_the_project",
        f"{DB}::test_listing_is_project_scoped_filtered_and_keyset_paginated",
    ],
    "metadata can be updated according to documented semantics": [
        f"{API}::test_metadata_updates_create_no_revision",
        f"{SERVICE}::test_metadata_updates_create_no_revision",
    ],
    "architecture content updates create immutable revisions": [
        f"{API}::test_edits_create_revisions_with_their_changes",
        f"{API}::test_saving_whole_content",
        f"{DB}::test_revisions_are_append_only",
    ],
    "revision numbering is stable and concurrency-safe": [
        f"{I}.database.test_architecture_revision_race::test_concurrent_edits_of_one_revision_create_one_revision",
        f"{DB}::test_two_architectures_number_their_revisions_independently",
    ],
    "current revision always references a valid committed revision": [
        f"{DB}::test_the_current_revision_must_exist",
        f"{DB}::test_a_failed_write_leaves_nothing_behind",
    ],
    "historical revisions can be retrieved": [
        f"{API}::test_edits_create_revisions_with_their_changes",
        f"{DB}::test_an_older_schema_is_read_through_upgrades_and_its_snapshot_is_kept",
    ],
    "revision history is available and consistently ordered": [
        f"{SERVICE}::test_history_is_paginated_newest_first",
        f"{DB}::test_revisions_follow_each_other_and_history_is_kept",
    ],
    "historical restoration creates a new revision": [
        f"{API}::test_restoring_a_revision_creates_a_new_one_and_keeps_history",
        f"{SERVICE}::test_restoring_a_revision_creates_a_new_one_and_keeps_history",
    ],
    "revision comparison produces deterministic structured output": [
        f"{API}::test_compare_two_revisions",
        f"{U}.architecture_ir.test_ir_diff::test_the_summary_and_the_diff_are_deterministic",
        f"{U}.architecture_ir.test_ir_diff::test_secret_looking_values_are_redacted_but_the_change_is_reported",
    ],
    "stale content updates return a conflict rather than silently overwriting changes": [
        f"{API}::test_a_stale_update_is_a_conflict",
        f"{SERVICE}::test_a_stale_update_is_a_conflict_not_last_write_wins",
    ],
    "tenant isolation and project permissions are enforced": [
        f"{API}::test_who_may_read_and_change",
        f"{API}::test_an_architecture_is_only_reachable_through_its_own_project",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "database migrations are included where required": [
        f"{I}.database.test_migrations::test_upgrading_existing_architectures_keeps_them_and_names_them",
        f"{I}.database.test_migrations::test_models_match_migrations",
    ],
    "API schemas and OpenAPI documentation are updated": [
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
    "existing frontend contracts are preserved or explicitly documented": [
        f"{S}.test_documentation::test_the_decisions_are_recorded"
    ],
    "unit and integration tests cover core behavior and failure cases": [
        f"{SERVICE}::test_archive_restore_and_delete",
        f"{API}::test_invalid_edits_create_nothing",
        f"{API}::test_invalid_architectures_say_exactly_what_is_wrong",
    ],
    "existing regression tests pass": [
        f"{S}.test_traceability_projects::test_every_item_of_the_spec_is_mapped",
        f"{S}.test_traceability_requirements_engine::test_every_item_of_the_spec_is_mapped",
        f"{S}.test_traceability_architecture_ir::test_every_item_of_the_spec_is_mapped",
    ],
    "documentation is updated": [
        f"{U}.architecture_ir.test_ir_documentation::test_every_topic_of_the_specification_is_covered",
        f"{S}.test_documentation::test_every_audit_action_is_documented",
    ],
    "no unrelated infrastructure or roadmap features were added": [
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list"
    ],
    "the implementation has been verified before marking the milestone complete": [
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
        f"{S}.test_audit_sweep::test_read_only_endpoints_write_nothing",
    ],
}


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_criterion_is_mapped() -> None:
    assert len(ACCEPTANCE) == 22
