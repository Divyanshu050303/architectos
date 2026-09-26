"""The Requirements Engine's Definition of Done (spec section 65), each item mapped to the tests
that prove it. Fails if a mapped test is renamed or removed, or if an item is left unmapped."""

import importlib
from pathlib import Path

import pytest

U = "tests.unit"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
E = "tests.evaluation.requirements.test_requirements_regression"
ENG = f"{U}.engines"
DOC = Path(__file__).resolve().parents[2] / "docs" / "requirements-engine.md"

DONE: dict[str, list[str]] = {
    "repository audit completed": [],  # a document, not a test: checked below
    "canonical requirement model defined": [
        f"{U}.requirements.test_requirement_entity::test_create_normalizes_and_parses",
        f"{U}.requirements.test_requirement_rules::test_spec_examples_are_valid",
    ],
    "requirement taxonomy defined": [
        f"{U}.requirements.test_requirement_rules::test_every_type_has_known_categories",
        f"{U}.requirements.test_requirement_rules::test_every_measurable_category_has_a_metric",
    ],
    "deterministic extraction": [
        f"{ENG}.test_requirements_extraction::test_each_quantity_reads_its_own_clause",
        f"{ENG}.test_requirements_extraction::test_what_cannot_be_read_is_unresolved_not_guessed",
        f"{ENG}.test_requirements_service::test_the_analysis_is_deterministic",
    ],
    "classification": [
        f"{ENG}.test_requirements_extraction::test_spec_classification_cases",
        f"{ENG}.test_requirements_extraction::test_scope_only_where_the_clause_names_one",
    ],
    "normalization": [
        f"{ENG}.test_requirements_normalizer::test_canonical_forms_come_from_the_domain",
        f"{U}.requirements.test_requirement_normalization::test_normalization_is_idempotent",
    ],
    "ambiguity detection": [
        f"{ENG}.test_requirements_ambiguity::test_ten_million_users_is_ambiguous_with_every_reading_offered",
        f"{ENG}.test_requirements_ambiguity::test_no_value_is_ever_invented",
    ],
    "assumption detection": [
        f"{ENG}.test_requirements_assumptions::test_every_applied_interpretation_is_surfaced",
        f"{ENG}.test_requirements_assumptions::test_ten_million_users_is_never_assumed_to_mean_anything",
    ],
    "completeness analysis": [
        f"{ENG}.test_requirements_completeness::test_a_payment_system_needs_security_compliance_consistency_and_recovery",
        f"{ENG}.test_requirements_completeness::test_an_internal_tool_is_not_asked_for_traffic_figures",
    ],
    "conflict detection": [
        f"{ENG}.test_requirements_conflicts::test_contradictory_bounds_are_a_blocking_conflict",
        f"{ENG}.test_requirements_conflicts::test_a_candidate_contradicting_an_existing_requirement",
    ],
    "confidence model": [
        f"{U}.requirements.test_requirement_entity::test_confidence_is_not_priority",
        f"{U}.requirements.test_requirement_candidates::test_candidates_are_always_drafts_with_a_valid_confidence",
        f"{ENG}.test_requirements_ambiguity::test_low_confidence_interpretations_are_flagged",
    ],
    "requirement validation": [
        f"{ENG}.test_requirements_validation::test_impossible_requirements_are_blocking",
        f"{ENG}.test_requirements_validation::test_a_proposal_that_points_at_text_not_in_the_input_is_rejected",
    ],
    "candidate model": [
        f"{U}.requirements.test_requirement_candidates::test_keys_are_deterministic_and_identify_the_interpretation",
        f"{ENG}.test_requirements_service::test_a_tampered_candidate_is_refused",
    ],
    "canonical requirement model": [
        f"{U}.requirements.test_requirement_candidates::test_promotion_revalidates_like_any_creation",
        f"{I}.database.test_requirement_analysis_persistence::test_promotion_records_the_origin_and_never_duplicates",
    ],
    "RequirementSet": [
        f"{U}.requirements.test_requirement_set_service::test_later_changes_never_alter_a_set",
        f"{U}.requirements.test_requirement_set_service::test_conflicting_requirements_cannot_form_a_set",
    ],
    "requirement versioning": [
        f"{U}.requirements.test_requirement_entity::test_a_material_change_is_a_new_version_and_keeps_identity",
        f"{I}.database.test_requirement_set_persistence::test_a_pinned_version_must_exist",
    ],
    "architecture boundary contract": [
        f"{U}.requirements.test_planning_input::test_the_contract_shape",
        f"{U}.requirements.test_requirement_analyses::test_a_promoted_candidate_remembers_its_origin_and_the_planning_input_carries_it",
    ],
    "API integration": [
        f"{I}.api.test_requirement_analyses::test_a_well_specified_description",
        f"{I}.api.test_requirement_analyses::test_promotion_creates_drafts_with_provenance_and_is_idempotent",
    ],
    "LLM adapter if required": [
        f"{U}.ai.test_anthropic_provider::test_a_structured_answer",
        f"{ENG}.test_requirements_engine_factory::test_the_model_is_off_unless_configured",
    ],
    "LLM failure handling": [
        f"{ENG}.test_requirements_semantic::test_a_model_failure_leaves_the_deterministic_analysis_standing",
        f"{I}.api.test_requirement_analyses::test_a_failing_model_never_fails_the_analysis",
    ],
    "prompt injection protection": [
        f"{U}.ai.test_requirement_agent::test_user_text_is_data_never_instructions",
        f"{U}.ai.test_requirement_agent::test_a_poisoned_output_cannot_smuggle_anything_through",
        f"{S}.test_requirement_input_sweep::test_hostile_model_output_cannot_escape_validation",
    ],
    "input limits": [
        f"{U}.requirements.test_requirement_analyses::test_the_longest_accepted_input",
        f"{I}.api.test_requirement_analyses::test_a_very_large_body_is_refused_before_it_is_read",
        f"{ENG}.test_requirements_service::test_the_worst_case_is_bounded_in_time_and_size",
    ],
    "unit tests": [f"{ENG}.test_requirements_service::test_the_result_shape"],
    "integration tests": [
        f"{I}.database.test_requirement_analysis_persistence::test_analyses_are_append_only",
        f"{I}.database.test_requirement_analysis_persistence::test_an_origin_must_be_an_analysis_of_the_same_project",
    ],
    "API tests": [
        f"{I}.api.test_requirement_analyses::test_who_may_analyze_read_and_promote",
        f"{I}.api.test_requirement_analyses::test_invalid_promotions",
    ],
    "security tests": [
        f"{S}.test_requirement_input_sweep::test_hostile_text_is_only_data",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
    ],
    "evaluation dataset": [
        f"{E}::test_the_dataset_is_well_formed",
        f"{E}::test_the_dataset_covers_every_kind_of_case",
    ],
    "regression evaluation": [
        f"{E}::test_quality_does_not_regress",
        f"{U}.ai.test_evaluation::test_the_regression_gate",
    ],
    "observability": [
        f"{U}.requirements.test_requirement_analysis_service::test_analysis_emits_counts_never_text",
        f"{U}.requirements.test_requirement_analysis_service::test_model_failures_and_token_usage_are_counted",
        f"{U}.api.test_metrics::test_labels_that_are_not_short_identifiers_are_refused",
    ],
    "documentation": [f"{S}.test_documentation::test_the_decisions_are_recorded"],
}

SPEC = [
    "repository audit completed", "canonical requirement model defined", "requirement taxonomy defined",
    "deterministic extraction", "classification", "normalization", "ambiguity detection",
    "assumption detection", "completeness analysis", "conflict detection", "confidence model",
    "requirement validation", "candidate model", "canonical requirement model", "RequirementSet",
    "requirement versioning", "architecture boundary contract", "API integration", "LLM adapter if required",
    "LLM failure handling", "prompt injection protection", "input limits", "unit tests", "integration tests",
    "API tests", "security tests", "evaluation dataset", "regression evaluation", "observability",
    "documentation",
]  # fmt: skip

PROVEN = [(item, tests) for item, tests in DONE.items() if tests]


@pytest.mark.parametrize(("item", "tests"), PROVEN, ids=[item for item, _ in PROVEN])
def test_item_is_proven_by_existing_tests(item: str, tests: list[str]) -> None:
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{item}: {reference} not found"


def test_every_item_of_the_spec_is_mapped() -> None:
    assert list(DONE) == SPEC
    assert len(SPEC) == 30
    assert [item for item, tests in DONE.items() if not tests] == ["repository audit completed"]


def test_the_engine_is_documented_with_its_audit_and_review() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Definition of done", "## Senior review"):
        assert heading in text, heading
