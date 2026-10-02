"""Drift Detection Engine: each phase's acceptance criteria and the quality requirements mapped to the
tests that prove them, plus structural guarantees — the domain and the engine free of storage, network
and execution, no remediation, scanning or scheduling endpoint, and the documentation covering every
topic and example without claiming a guarantee. Fails if a mapped test is renamed or removed, or a
criterion is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

U = "tests.unit.drift"
DOMAIN = f"{U}.test_drift_domain"
COMPATIBILITY = f"{U}.test_drift_compatibility"
MATCHING = f"{U}.test_drift_matching"
COMPARISON = f"{U}.test_drift_comparison"
CLASSIFICATION = f"{U}.test_drift_classification"
IMPACT = f"{U}.test_drift_impact"
REVIEW = f"{U}.test_drift_review"
FIXTURES = f"{U}.test_drift_fixtures"
API = "tests.integration.api.test_drift"
DISCOVERY_API = "tests.integration.api.test_discovery"
MIGRATIONS = "tests.integration.database.test_migrations"
IR_DIFF = "tests.unit.architecture_ir.test_ir_diff"
S = "tests.security"
SAFETY = f"{S}.test_drift_safety"
HERE = f"{S}.test_traceability_drift_engine"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "drift-engine.md"
PACKAGES = ("core/domain/drift", "engines/drift")
ROUTES = ROOT / "apps" / "api" / "routes" / "drift.py"

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 1: drift domain contract.
    "1.1 typed and validated contracts": [
        f"{DOMAIN}::test_a_request_names_exact_references_and_no_topology",
        f"{FIXTURES}::test_requests_are_validated_and_the_exact_revision_and_run_resolved",
    ],
    "1.2 stable finding ids and deterministic ordering": [
        f"{DOMAIN}::test_finding_ids_are_stable_and_correlate_repeated_differences",
        f"{DOMAIN}::test_a_result_is_canonical_and_says_no_difference_only_of_its_coverage",
        f"{FIXTURES}::test_identical_inputs_give_identical_normalized_results",
    ],
    "1.3 exact baseline and discovery references": [
        f"{DOMAIN}::test_a_request_names_exact_references_and_no_topology",
        f"{API}::test_an_analysis_is_stored_read_back_and_changes_nothing",
    ],
    "1.4 explicit compatibility and coverage states": [
        f"{DOMAIN}::test_compatibility_is_the_least_comparable_dimension_and_blocks_comparison",
        f"{DOMAIN}::test_the_analysis_lifecycle_reflects_compatibility_coverage_and_certainty",
    ],
    "1.5 technical result separated from review lifecycle": [
        f"{DOMAIN}::test_review_is_separate_from_the_comparison_and_auditable",
    ],
    "1.6 unknown and unresolved states preserved": [
        f"{CLASSIFICATION}::test_ambiguous_identity_is_unknown_with_its_candidates",
        f"{FIXTURES}::test_04_a_component_missing_under_incomplete_coverage_is_never_removed",
    ],
    "1.7 no arbitrary overall drift score": [
        f"{SAFETY}::test_there_is_no_drift_score",
        f"{HERE}::test_no_drift_score_or_severity_is_produced",
    ],
    # Phase 2: comparison input compatibility.
    "2.1 compatibility checks are deterministic": [
        f"{COMPATIBILITY}::test_compatibility_is_deterministic_and_reads_recorded_provenance",
    ],
    "2.2 incompatible inputs produce actionable explanations": [
        f"{COMPATIBILITY}::test_a_changed_shared_rule_makes_the_inputs_incompatible",
        f"{COMPATIBILITY}::test_nothing_read_or_no_shared_source_type_is_incompatible",
        f"{COMPATIBILITY}::test_a_hand_written_baseline_needs_confirmed_identity",
    ],
    "2.3 partial comparability is preserved": [
        f"{COMPATIBILITY}::test_partial_and_missing_coverage_stay_partial",
        f"{COMPATIBILITY}::test_a_changed_extractor_makes_its_source_type_not_comparable",
    ],
    "2.4 parser or schema changes are not architecture changes": [
        f"{CLASSIFICATION}::test_incompatible_inputs_and_changed_parsers_are_not_compared",
        f"{COMPATIBILITY}::test_schema_and_freshness",
        f"{FIXTURES}::test_10_a_parser_version_change_is_not_compared",
    ],
    "2.5 missing coverage is not removal": [
        f"{CLASSIFICATION}::test_missing_coverage_is_never_a_removal",
        f"{FIXTURES}::test_04_a_component_missing_under_incomplete_coverage_is_never_removed",
    ],
    # Phase 3: identity and matching.
    "3.1 matching is deterministic and independently testable": [
        f"{MATCHING}::test_matching_is_deterministic",
    ],
    "3.2 identity evidence is preserved": [
        f"{MATCHING}::test_a_rename_matches_only_through_a_confirmed_identity",
        f"{MATCHING}::test_the_latest_confirmed_mapping_is_in_force_and_can_be_retracted",
        f"{API}::test_identity_mappings_are_confirmed_by_people",
    ],
    "3.3 ambiguous matches remain unresolved": [
        f"{MATCHING}::test_conflicting_claims_and_missing_mappings_stay_unresolved",
        f"{MATCHING}::test_an_entity_where_a_node_was_discovered_is_a_candidate_not_a_match",
        f"{MATCHING}::test_parallel_connections_need_a_stated_kind",
        f"{FIXTURES}::test_09_an_ambiguous_identity_stays_unresolved_with_its_candidates",
    ],
    "3.4 no unsupported merging or continuity claims": [
        f"{MATCHING}::test_similar_names_are_never_merged",
        f"{FIXTURES}::test_08_similar_names_without_identity_evidence_are_never_merged",
    ],
    "3.5 stable identifiers reused": [
        f"{MATCHING}::test_a_stable_source_identifier_matches",
        f"{MATCHING}::test_the_same_relationship_id_matches_and_exclusions_are_left_out",
        f"{FIXTURES}::test_07_a_renamed_resource_with_a_confirmed_identity_is_the_same_component",
    ],
    # Phase 4: structural and configuration differences.
    "4.1 structural comparisons are deterministic": [f"{COMPARISON}::test_differences_are_deterministic"],
    "4.2 every finding names the compared elements and properties": [
        f"{COMPARISON}::test_a_configuration_change_names_its_property_values_and_evidence",
        f"{FIXTURES}::test_05_a_component_configuration_change",
        f"{FIXTURES}::test_06_a_connection_addition_and_removal",
    ],
    "4.3 secret values are not exposed": [
        f"{COMPARISON}::test_secret_values_are_never_kept",
        f"{FIXTURES}::test_13_secret_bearing_configuration_is_never_kept_or_shown",
        f"{API}::test_secrets_are_never_stored_or_returned",
    ],
    "4.4 unsupported properties remain unresolved": [
        f"{COMPARISON}::test_an_unreadable_declared_value_is_unresolved",
        f"{COMPARISON}::test_values_are_compared_typed_and_only_where_the_source_can_declare_them",
        f"{COMPARISON}::test_a_value_no_longer_declared_is_not_a_default",
    ],
    "4.5 absence is removal only when coverage supports it": [
        f"{CLASSIFICATION}::test_a_confirmed_removal_where_the_scope_was_inspected",
        f"{FIXTURES}::test_03_one_confirmed_removal_where_its_source_was_read_completely",
        f"{FIXTURES}::test_11_an_unsupported_source_is_unread_never_absent",
    ],
    "4.6 existing architecture diff behavior compatible": [
        f"{IR_DIFF}::test_configuration_technology_and_resource_changes_are_categorized",
        f"{IR_DIFF}::test_decimal_changes_are_exact",
        f"{COMPARISON}::test_kinds_and_components_are_compared_only_as_far_as_the_source_states_them",
    ],
    # Phase 5: coverage-aware classification.
    "5.1 coverage limitations visible in results": [
        f"{CLASSIFICATION}::test_no_difference_is_stated_only_of_the_inspected_scope",
        f"{FIXTURES}::test_12_a_partial_run_completes_with_warnings_and_only_potential_removals",
    ],
    "5.2 confirmed and potential differences distinct": [
        f"{CLASSIFICATION}::test_a_confirmed_addition",
        f"{CLASSIFICATION}::test_inferences_and_hand_written_baselines_stay_uncertain",
        f"{CLASSIFICATION}::test_a_configuration_change_is_confirmed_with_source_evidence",
    ],
    "5.3 missing information is not removal": [
        f"{CLASSIFICATION}::test_missing_coverage_is_never_a_removal",
        f"{FIXTURES}::test_04_a_component_missing_under_incomplete_coverage_is_never_removed",
    ],
    "5.4 partial discovery gives no system-wide conclusion": [
        f"{FIXTURES}::test_12_a_partial_run_completes_with_warnings_and_only_potential_removals",
        f"{FIXTURES}::test_01_identical_inputs_with_complete_coverage_report_no_difference_within_it",
    ],
    "5.5 classification deterministic and documented": [
        f"{CLASSIFICATION}::test_classification_is_deterministic",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    # Phase 6: impact context.
    "6.1 impact links exact downstream analysis references": [
        f"{IMPACT}::test_stored_analyses_of_the_baseline_are_linked_current_or_stale",
    ],
    "6.2 existing engines reused, not recalculated": [
        f"{IMPACT}::test_a_property_names_the_engines_its_component_specification_lists",
        f"{IMPACT}::test_structure_and_connections_name_the_engines_that_read_them",
    ],
    "6.3 stale analyses identified": [
        f"{IMPACT}::test_stored_analyses_of_the_baseline_are_linked_current_or_stale",
        f"{FIXTURES}::test_14_a_stale_baseline_or_an_older_run_is_compared_with_a_warning",
    ],
    "6.4 unsupported impact claims remain unknown": [
        f"{IMPACT}::test_a_property_no_specification_lists_claims_nothing_specific",
        f"{IMPACT}::test_requirements_and_decisions_the_element_references_are_named_not_judged",
        f"{FIXTURES}::test_15_impact_with_missing_downstream_analyses_claims_nothing",
    ],
    "6.5 usable without optional downstream analyses": [
        f"{IMPACT}::test_drift_works_without_downstream_analyses_and_impact_is_not_identity",
    ],
    # Phase 7: review and resolution.
    "7.1 review status distinct from classification": [
        f"{DOMAIN}::test_review_is_separate_from_the_comparison_and_auditable",
        f"{REVIEW}::test_an_analysis_opens_items_for_reviewable_differences_only",
    ],
    "7.2 review actions auditable": [
        f"{API}::test_items_follow_differences_and_their_review_never_changes_the_architecture",
        f"{FIXTURES}::test_18_review_actions_leave_the_canonical_architecture_unchanged",
    ],
    "7.3 historical findings retrievable": [
        f"{API}::test_an_analysis_is_stored_read_back_and_changes_nothing",
        f"{DOMAIN}::test_a_resolved_difference_detected_again_reopens_and_history_is_kept",
    ],
    "7.4 repeated findings correlated without losing history": [
        f"{REVIEW}::test_repeated_findings_correlate_without_losing_history",
        f"{FIXTURES}::test_16_a_repeated_difference_is_one_item_across_analyses",
        f"{FIXTURES}::test_17_a_resolved_difference_detected_again_reopens",
    ],
    "7.5 no review action mutates the IR": [
        f"{REVIEW}::test_review_never_changes_the_baseline",
        f"{FIXTURES}::test_18_review_actions_leave_the_canonical_architecture_unchanged",
    ],
    # Phase 8: API, persistence, authorization.
    "8.1 every operation enforces authorization": [
        f"{API}::test_authorization_and_tenant_isolation",
        f"{FIXTURES}::test_authorization_and_isolation",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
    ],
    "8.2 API contracts typed and documented": [
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
        f"{API}::test_invalid_requests_store_nothing",
    ],
    "8.3 persistence transactional where required": [
        f"{API}::test_stored_records_are_append_only_and_a_compared_run_is_kept",
        f"{SAFETY}::test_failures_expose_nothing_and_store_nothing",
        f"{MIGRATIONS}::test_downgrading_drift_leaves_discovery_runs_intact",
    ],
    "8.4 tenant isolation tested": [
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{SAFETY}::test_another_organization_learns_nothing",
    ],
    "8.5 existing contracts remain compatible": [
        f"{S}.test_audit_sweep::test_every_mutating_project_endpoint_is_classified",
        f"{DISCOVERY_API}::test_accepting_into_an_architecture_adds_a_revision_and_never_rewrites_one",
    ],
    "8.6 drift never mutates canonical architecture": [
        f"{SAFETY}::test_drift_code_cannot_execute_fetch_or_change_an_architecture",
        f"{API}::test_an_analysis_is_stored_read_back_and_changes_nothing",
        f"{HERE}::test_there_is_no_remediation_scanning_or_scheduling_endpoint",
    ],
    # Phase 9: fixtures and quality requirements.
    "9.1 the eighteen fixtures": [
        f"{FIXTURES}::test_01_identical_inputs_with_complete_coverage_report_no_difference_within_it",
        f"{FIXTURES}::test_18_review_actions_leave_the_canonical_architecture_unchanged",
        f"{HERE}::test_the_eighteen_fixtures_exist",
    ],
    "9.2 no silent exception swallowing": [
        f"{FIXTURES}::test_an_engine_refusal_is_stored_as_a_failed_analysis",
        f"{SAFETY}::test_failures_expose_nothing_and_store_nothing",
    ],
    "9.3 no fabricated evidence or impact": [
        f"{FIXTURES}::test_15_impact_with_missing_downstream_analyses_claims_nothing",
        f"{DOMAIN}::test_a_finding_rests_on_evidence_and_never_keeps_a_secret",
    ],
    "9.4 no unbounded comparison work": [
        f"{SAFETY}::test_requests_are_size_and_rate_limited",
        f"{HERE}::test_the_domain_and_engine_reach_no_storage_network_or_execution",
    ],
    "9.5 no unsupported removal": [
        f"{FIXTURES}::test_11_an_unsupported_source_is_unread_never_absent",
        f"{CLASSIFICATION}::test_inferences_and_hand_written_baselines_stay_uncertain",
    ],
    "9.6 no remediation or architecture mutation": [
        f"{SAFETY}::test_drift_code_cannot_execute_fetch_or_change_an_architecture",
        f"{HERE}::test_there_is_no_remediation_scanning_or_scheduling_endpoint",
    ],
    "9.7 no arbitrary drift score": [f"{SAFETY}::test_there_is_no_drift_score"],
}


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_criterion_is_mapped() -> None:
    assert len(ACCEPTANCE) == 51  # 44 acceptance criteria (phases 1-8) and 7 quality requirements


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


def test_the_domain_and_engine_reach_no_storage_network_or_execution() -> None:
    forbidden = (
        "persistence", "apps", "sqlalchemy", "httpx", "requests", "socket", "subprocess", "random",
        "boto3", "kubernetes", "docker",
    )  # fmt: skip
    for package in PACKAGES:
        for path, tree in _python(package):
            for name in _imports(tree):
                assert name.split(".", 1)[0] not in forbidden, (path, name)


def test_there_is_no_remediation_scanning_or_scheduling_endpoint() -> None:
    text = ROUTES.read_text()
    assert "@router.post(" in text
    for verb in ("put", "patch", "delete"):
        assert f"@router.{verb}(" not in text
    for word in ("remediat", "apply", "rollback", "scan", "schedule", "connector", "fix", "sync"):
        assert not re.search(rf'["/][a-z-]*{word}', text), word


def test_no_drift_score_or_severity_is_produced() -> None:
    for package in PACKAGES:
        for path, tree in _python(package):
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert node.value not in {"score", "drift_score", "severity", "risk_score"}, path


def test_the_eighteen_fixtures_exist() -> None:
    module = importlib.import_module(FIXTURES)
    numbered = {name[5:7] for name in dir(module) if re.match(r"test_\d\d_", name)}
    assert numbered == {f"{n:02}" for n in range(1, 19)}


def test_nothing_is_claimed_guaranteed() -> None:
    for path in (
        DOC,
        DOCS / "api" / "drift.md",
        DOCS / "adr" / "ADR-022-deterministic-drift-detection.md",
        DOCS / "frontend" / "drift-contract.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(r"[^.]*\b(?:guarantee[sd]?|proves?|certif\w*)\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Phase 10's sixteen topics and seven examples, the audit and the final review."""
    text = DOC.read_text()
    flat = " ".join(text.replace("\n>", "\n").split())
    assert "within supported comparison coverage" in flat
    assert "does not establish that a change is unauthorized, harmful, insecure or operationally" in flat
    for heading in (
        "## Purpose and scope",
        "## Baseline and discovery input requirements",
        "## Compatibility and coverage semantics",
        "## Identity matching rules",
        "## Supported structural and configuration comparisons",
        "## Finding classification semantics",
        "## Removal classification requirements",
        "## Finding lifecycle and review actions",
        "## Evidence and provenance references",
        "## Downstream engine integrations",
        "## API contracts",
        "## Persistence and authorization",
        "## Security considerations",
        "## Known limitations",
        "## Adding a comparison rule",
        "## Tests",
        "## Example: a confirmed component addition is detected",
        "## Example: a configuration change is detected with source evidence",
        "## Example: incomplete discovery coverage prevents a removal claim",
        "## Example: an incompatible parser version prevents reliable comparison",
        "## Example: a finding is acknowledged without changing the architecture",
        "## Example: a finding is resolved by a later discovery run",
        "## Example: the baseline architecture remains unchanged throughout analysis",
        "## Repository audit",
        "## Final review",
    ):
        assert heading in text, heading
    assert (DOCS / "frontend" / "drift-contract.md").exists()
    assert "## Decision" in (DOCS / "adr" / "ADR-022-deterministic-drift-detection.md").read_text()
