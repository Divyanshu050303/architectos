"""Milestone 6 (deterministic validation engine): each acceptance criterion of phases 0-8 mapped to
the tests that prove it, plus the structural guarantees the specification asks for (one validation
framework, a domain independent of the engine, no dynamic code, documentation of every topic).
Fails if a mapped test is renamed or removed, or a criterion is unmapped."""

import ast
import importlib
from pathlib import Path

import pytest

U = "tests.unit.validation"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
RESULTS = f"{U}.test_validation_results"
ENGINE = f"{U}.test_validation_engine"
STRUCTURE = f"{U}.test_structure_rules"
CONFIG = f"{U}.test_configuration_and_policy_rules"
REQUIREMENTS = f"{U}.test_requirement_rules"
SERVICE = f"{U}.test_validation_service"
LIMITS = f"{U}.test_validation_limits"
API = f"{I}.api.test_validations"
POLICY_API = f"{I}.api.test_architecture_policy"
HERE = f"{S}.test_traceability_validation_engine"

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "architecture" / "validation-engine.md"

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: audit and plan.
    "the existing implementation has been inspected": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    "the plan is based on actual repository contents": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    "no duplicate architecture model is proposed": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{HERE}::test_there_is_one_validation_framework",
    ],
    # Phase 1: result contract.
    "typed validation run and finding contracts exist": [
        f"{RESULTS}::test_a_valid_finding_and_its_stable_id",
        f"{RESULTS}::test_invalid_findings_are_refused",
        f"{RESULTS}::test_the_run_lifecycle",
    ],
    "summary calculations are consistent": [
        f"{RESULTS}::test_the_summary_is_derived_from_the_findings",
        f"{CONFIG}::test_blocking_findings_are_counted",
    ],
    "findings reference architecture entities and fields": [
        f"{STRUCTURE}::test_a_synchronous_cycle_is_reported_once_with_its_members",
        f"{STRUCTURE}::test_unknown_values_are_reported_per_element",
    ],
    "deterministic comparison behavior is defined and tested": [
        f"{RESULTS}::test_results_are_ordered_and_fingerprinted_deterministically",
        f"{RESULTS}::test_findings_and_verdicts_round_trip",
    ],
    # Phase 2: registry and execution.
    "rules can be added through the rule contract": [
        f"{ENGINE}::test_a_rule_finds_what_it_is_made_to_find",
        f"{STRUCTURE}::test_the_registry_ships_every_rule_once_in_both_profiles",
    ],
    "registry behavior is deterministic": [
        f"{ENGINE}::test_registration_lookup_and_listing_are_ordered",
        f"{ENGINE}::test_results_are_deterministic_whatever_the_registration_order",
        f"{ENGINE}::test_selection_by_profile_and_rule_ids",
    ],
    "rule failures are visible and not misreported": [
        f"{ENGINE}::test_a_crashing_rule_is_a_failure_not_a_finding",
        f"{ENGINE}::test_a_rule_cannot_report_under_another_rules_name",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_run_without_internals",
    ],
    "rule execution is independent of API and persistence": [
        f"{HERE}::test_the_domain_and_engine_depend_only_inward",
        f"{ENGINE}::test_the_context_is_read_only",
    ],
    # Phase 3: structural rules.
    "structural rules are deterministic": [
        f"{STRUCTURE}::test_the_shipped_rules_are_deterministic",
        f"{STRUCTURE}::test_strongly_connected_components_are_deterministic",
    ],
    "findings identify exact affected entities": [
        f"{STRUCTURE}::test_a_component_without_connections_is_reported",
        f"{STRUCTURE}::test_depending_on_a_deprecated_component_is_reported",
    ],
    "valid architectures are not rejected by assumptions outside the IR contract": [
        f"{STRUCTURE}::test_well_formed_examples_raise_no_structural_findings",
        f"{STRUCTURE}::test_a_lone_component_and_boundaries_are_not_disconnected",
        f"{STRUCTURE}::test_messaging_and_data_access_are_not_request_cycles",
    ],
    "existing IR validation remains compatible": [
        f"{S}.test_traceability_architecture_ir::test_every_item_of_the_spec_is_mapped",
        f"{STRUCTURE}::test_a_revision_stored_in_an_older_schema_is_reported",
    ],
    "structural tests cover normal and malformed inputs": [
        f"{STRUCTURE}::test_a_large_cycle_is_capped_and_does_not_exhaust_the_stack",
        f"{ENGINE}::test_invalid_configuration_is_refused_before_anything_runs",
    ],
    # Phase 4: component, connection and constraint validation.
    "component and connection checks use canonical specification data": [
        f"{CONFIG}::test_replicas_outside_the_autoscaling_range",
        f"{CONFIG}::test_retries_need_a_timeout_on_connections_the_caller_waits_on",
        f"{CONFIG}::test_every_result_states_its_limitations",
    ],
    "constraint evaluation is deterministic": [
        f"{CONFIG}::test_the_policy_is_part_of_the_context_fingerprint",
        f"{STRUCTURE}::test_the_shipped_rules_are_deterministic",
    ],
    "unsupported or missing information is not treated as verified": [
        f"{CONFIG}::test_an_unstated_technology_cannot_be_shown_to_comply",
        f"{CONFIG}::test_tls_is_required_on_every_communicating_connection",
        f"{CONFIG}::test_multi_az_and_zones_must_agree",
    ],
    "findings include traceable evidence": [
        f"{CONFIG}::test_the_component_count_is_capped_boundaries_excluded",
        f"{STRUCTURE}::test_a_synchronous_cycle_is_reported_once_with_its_members",
    ],
    "tests cover both passing and failing configuration cases": [
        f"{CONFIG}::test_backups_and_retention_must_agree",
        f"{CONFIG}::test_dead_letter_belongs_to_consumers",
        f"{CONFIG}::test_the_examples_have_consistent_configuration",
    ],
    # Phase 5: requirements and policy.
    "requirement evaluation uses existing requirement contracts": [
        f"{REQUIREMENTS}::test_only_requirements_in_force_get_verdicts",
        f"{SERVICE}::test_requirements_are_given_to_the_engine_and_recorded",
    ],
    "requirement evidence is traceable": [
        f"{REQUIREMENTS}::test_regions_satisfied_violated_and_not_verifiable",
        f"{REQUIREMENTS}::test_storage_is_compared_with_the_provisioned_total",
        f"{REQUIREMENTS}::test_references_to_unknown_retired_and_older_requirements",
    ],
    "unknown is distinct from pass": [
        f"{REQUIREMENTS}::test_other_requirements_are_not_verifiable_with_a_reason",
        f"{REQUIREMENTS}::test_retention_is_checked_where_it_is_stated",
        f"{REQUIREMENTS}::test_a_scope_outside_the_concerned_components_is_never_widened",
    ],
    "policy results are reproducible": [
        f"{CONFIG}::test_policy_rules_are_mandatory",
        f"{SERVICE}::test_a_run_is_stored_completed_with_its_inputs_and_audited",
        f"{POLICY_API}::test_owners_replace_the_policy_normalized_stored_and_audited",
    ],
    "no unsupported compliance claims are made": [
        f"{REQUIREMENTS}::test_encryption_in_transit",
        f"{REQUIREMENTS}::test_data_residency_concerns_only_data_holders",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    # Phase 6: API and persistence.
    "validation can be triggered and retrieved through the API": [
        f"{API}::test_a_validation_is_run_stored_and_read_back",
        f"{API}::test_runs_are_listed_newest_first",
        f"{API}::test_the_rule_catalog",
    ],
    "results persist correctly": [
        f"{API}::test_findings_are_paged_filtered_and_stable_across_runs",
        f"{API}::test_runs_and_findings_are_append_only",
        f"{I}.database.test_migrations::test_downgrading_validation_leaves_architectures_intact",
    ],
    "authorization is enforced": [
        f"{API}::test_viewers_read_members_validate_strangers_get_404",
        f"{SERVICE}::test_runs_are_only_found_through_their_own_architecture",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "existing API conventions are preserved": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{S}.test_mass_assignment_sweep::test_every_body_rejects_undeclared_privileged_fields",
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
    ],
    "OpenAPI documentation is updated": [
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
    "API and persistence tests pass, including failure behavior": [
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{SERVICE}::test_an_invalid_configuration_stores_nothing",
        f"{API}::test_archived_architectures_are_not_validated",
    ],
    # Phase 7: hardening.
    "determinism tests pass": [
        f"{SERVICE}::test_the_same_revision_gives_the_same_result",
        f"{LIMITS}::test_the_largest_architecture_validates_completely_and_deterministically",
    ],
    "the regression suite passes": [
        f"{S}.test_traceability_architecture_crud::test_every_criterion_is_mapped",
        f"{S}.test_traceability_projects::test_every_item_of_the_spec_is_mapped",
        f"{S}.test_traceability_requirements_engine::test_every_item_of_the_spec_is_mapped",
    ],
    "performance risks are documented": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{API}::test_reads_cost_the_same_whatever_the_number_of_runs_and_findings",
    ],
    "security boundaries are tested": [
        f"{S}.test_authentication_sweep::test_every_protected_endpoint_refuses_bad_credentials",
        f"{S}.test_audit_sweep::test_read_only_endpoints_write_nothing",
        f"{HERE}::test_rules_execute_no_dynamic_code",
    ],
    "sensitive values are not exposed": [
        f"{LIMITS}::test_secret_values_never_reach_findings_or_verdicts",
        f"{SERVICE}::test_a_run_is_stored_completed_with_its_inputs_and_audited",
    ],
    "no known critical validation correctness issue remains unresolved": [
        f"{HERE}::test_the_audit_and_review_are_recorded",
        f"{REQUIREMENTS}::test_a_scope_outside_the_concerned_components_is_never_widened",
    ],
    # Phase 8: documentation.
    "the engine and its limits are documented": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{S}.test_documentation::test_the_decisions_are_recorded",
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
    assert len(ACCEPTANCE) == 39  # 3 + 4 + 4 + 5 + 5 + 5 + 6 + 6 + 1 (phases 0-8)


def _python(package: str) -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text() or "")) for p in sorted((ROOT / package).rglob("*.py"))]


def _imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_there_is_one_validation_framework() -> None:
    """Rules, registries and results are defined once: in engines/validation and
    core/domain/validation."""
    names = {"RuleMeta", "Registry", "Finding", "ValidationResult", "ValidationRun"}
    found = [
        f"{path.relative_to(ROOT)}: {node.name}"
        for package in ("core", "engines", "apps/api", "persistence")
        for path, tree in _python(package)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name in names
    ]
    allowed = {
        "engines/validation/engine.py: RuleMeta",
        "engines/validation/engine.py: Registry",
        "core/domain/validation/results.py: Finding",
        "core/domain/validation/results.py: ValidationResult",
        "core/domain/validation/runs.py: ValidationRun",
        # Earlier, different questions (not architecture validation): whether a document is a
        # well-formed IR (Milestone 4), and what the Requirements Engine says about requirement text.
        "core/architecture_ir/validation.py: ValidationResult",
        "engines/requirements/findings.py: Finding",
    }
    assert set(found) - allowed == set()


def test_the_domain_and_engine_depend_only_inward() -> None:
    for path, tree in _python("core"):
        assert not any(m.startswith(("engines", "apps", "persistence")) for m in _imports(tree)), path
    for path, tree in _python("engines/validation"):
        assert not any(
            m.startswith(("apps", "persistence", "sqlalchemy", "fastapi")) for m in _imports(tree)
        ), path


def test_rules_execute_no_dynamic_code() -> None:
    builtins = {"eval", "exec", "compile", "__import__"}  # re.compile is fine; the builtin is not
    for path, tree in _python("engines/validation"):
        calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert not [f.id for f in calls if isinstance(f, ast.Name) and f.id in builtins], path
        assert not [f.attr for f in calls if isinstance(f, ast.Attribute) and f.attr == "import_module"], path
        assert "importlib" not in _imports(tree), path


def test_every_documentation_topic_is_covered() -> None:
    """Phase 8's documentation list, section by section."""
    text = DOC.read_text()
    for heading in (
        "## Layout",  # validation architecture
        "## Execution",  # lifecycle
        "## Result contract",  # result schema
        "### Severity",  # severity semantics
        "## Determinism",
        "## Rules",  # supported rules, categories
        "### Which requirements are verifiable",
        "## Policy",
        "## Persistence",
        "## Authorization",
        "## Limits",  # resource limits, performance
        "## Security",
        "## Known limitations",
        "## Adding a rule",  # rule interface and registration
        "## Tests",  # how to run tests
    ):
        assert heading in text, heading
    from engines.validation.registry import default_registry  # noqa: PLC0415 - the shipped rules

    for rule in default_registry().rules():
        assert f"`{rule.meta.id}`" in text, rule.meta.id
    assert (ROOT / "docs" / "api" / "validation.md").exists()


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known limitations"):
        assert heading in text, heading
