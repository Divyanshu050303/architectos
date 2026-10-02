"""Discovery Engine: each phase's acceptance criteria and the quality requirements mapped to the tests
that prove them, plus structural guarantees — the domain and the engine free of storage, network and
execution, no live-scanning or connector endpoint, and the documentation covering every topic and
example without claiming a guarantee. Fails if a mapped test is renamed or removed, or a criterion is
unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

U = "tests.unit.discovery"
DOMAIN = f"{U}.test_discovery_domain"
SOURCES = f"{U}.test_discovery_sources"
NORMALIZE = f"{U}.test_discovery_normalize"
MAPPING = f"{U}.test_discovery_mapping"
RELATIONSHIPS = f"{U}.test_discovery_relationships"
PROPOSAL = f"{U}.test_discovery_proposal"
COMPARISON = f"{U}.test_discovery_comparison"
FIXTURES = f"{U}.test_discovery_fixtures"
API = "tests.integration.api.test_discovery"
MIGRATIONS = "tests.integration.database.test_migrations"
S = "tests.security"
SAFETY = f"{S}.test_discovery_safety"
HERE = f"{S}.test_traceability_discovery_engine"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "discovery-engine.md"
PACKAGES = ("core/domain/discovery", "engines/discovery")
ROUTES = ROOT / "apps" / "api" / "routes" / "discovery.py"

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 1: domain contract.
    "1.1 typed and validated domain contracts": [
        f"{DOMAIN}::test_a_finding_keeps_its_evidence_and_never_a_secret",
        f"{DOMAIN}::test_a_result_is_consistent",
        f"{DOMAIN}::test_requests_are_bounded",
    ],
    "1.2 stable finding and entity identifiers": [
        f"{DOMAIN}::test_entities_rest_on_evidence_and_their_keys_are_ir_ids",
        f"{FIXTURES}::test_14_repeated_identical_input_gives_equivalent_results",
    ],
    "1.3 explicit provenance and verification states": [
        f"{DOMAIN}::test_how_a_value_is_known_becomes_ir_provenance_never_verification",
        f"{NORMALIZE}::test_every_value_points_at_its_finding",
    ],
    "1.4 findings distinct from accepted architecture": [
        f"{PROPOSAL}::test_every_candidate_has_an_element_and_proposing_writes_nothing",
        f"{API}::test_review_and_explicit_acceptance_create_a_new_architecture",
    ],
    "1.5 unknown and unsupported states preserved": [
        f"{DOMAIN}::test_what_is_unresolved_or_unsupported_makes_warnings",
        f"{FIXTURES}::test_5_unsupported_resource_types_are_reported_not_fabricated",
    ],
    "1.6 no overall confidence score": [f"{HERE}::test_no_confidence_score_is_produced"],
    "1.7 no duplicate canonical architecture graph": [
        f"{PROPOSAL}::test_the_proposal_is_reproduced_from_the_stored_result",
        f"{HERE}::test_the_domain_and_engine_reach_no_storage_network_or_execution",
    ],
    # Phase 2: source adapters and parsers.
    "2.1 typed, testable parser contract": [
        f"{SOURCES}::test_kubernetes_manifests_are_read_as_their_desired_state",
        f"{SOURCES}::test_extraction_is_deterministic",
    ],
    "2.2 supported formats and versions explicit": [
        f"{SOURCES}::test_native_terraform_is_unsupported_not_guessed",
        f"{SOURCES}::test_unrecognized_content_is_unsupported",
        f"{FIXTURES}::test_a_stored_result_reads_back_exactly_and_tampering_is_refused",
    ],
    "2.3 invalid and unsupported input produce structured results": [
        f"{SOURCES}::test_hostile_or_malformed_input_is_refused_safely",
        f"{SOURCES}::test_unsupported_kinds_and_templates_are_reported",
        f"{FIXTURES}::test_7_malformed_syntax_fails_safely_and_spares_the_rest",
    ],
    "2.4 source references and provenance preserved": [
        f"{NORMALIZE}::test_every_value_points_at_its_finding",
        f"{RELATIONSHIPS}::test_every_relationship_cites_its_finding_and_is_deterministic",
    ],
    "2.5 nothing is executed": [
        f"{SOURCES}::test_terraform_json_configuration_is_never_evaluated",
        f"{SAFETY}::test_nothing_is_executed_or_fetched_while_discovering",
        f"{SAFETY}::test_discovery_code_cannot_execute_or_fetch",
    ],
    "2.6 processing limits": [
        f"{SOURCES}::test_deep_yaml_and_duplicate_keys_are_reported",
        f"{FIXTURES}::test_10_sources_beyond_the_limits_are_refused",
        f"{SAFETY}::test_hostile_content_fails_safely",
    ],
    # Phase 3: normalization.
    "3.1 deterministic normalized output": [f"{NORMALIZE}::test_normalization_is_deterministic"],
    "3.2 source and normalized values traceable": [
        f"{NORMALIZE}::test_every_value_points_at_its_finding",
        f"{MAPPING}::test_each_mapping_keeps_the_value_as_written_and_the_conversion",
    ],
    "3.3 unsupported properties visible": [
        f"{SOURCES}::test_uninterpreted_fields_are_named_never_dropped",
        f"{SOURCES}::test_terraform_tags_and_nested_blocks",
    ],
    "3.4 missing, empty, unknown and explicit values distinguishable": [
        f"{SOURCES}::test_kubernetes_probes_api_version_and_declared_empty_ports",
        f"{NORMALIZE}::test_kinds_are_set_only_where_the_source_establishes_them",
        f"{FIXTURES}::test_6_missing_configuration_stays_unknown",
    ],
    "3.5 no runtime claims from static declarations": [
        f"{FIXTURES}::test_15_declared_desired_state_is_never_reported_as_verified_runtime"
    ],
    # Phase 4: component catalog mapping.
    "4.1 the existing catalog is used": [
        f"{MAPPING}::test_terraform_types_map_by_table_and_engine",
        f"{MAPPING}::test_an_exported_architecture_maps_its_declared_components",
    ],
    "4.2 original source identity retained": [
        f"{FIXTURES}::test_1_a_valid_source_with_several_component_types"
    ],
    "4.3 ambiguous and unsupported mappings visible": [
        f"{MAPPING}::test_containers_mapping_to_different_components_are_ambiguous",
        f"{MAPPING}::test_an_uncovered_provider_is_unsupported_and_utilities_are_not_components",
        f"{FIXTURES}::test_4_an_ambiguous_mapping_waits_for_a_person",
    ],
    "4.4 configuration validated against specifications": [
        f"{MAPPING}::test_invalid_conflicting_or_inapplicable_values_are_kept_with_a_reason",
        f"{PROPOSAL}::test_the_proposal_is_validated_by_the_ir_and_the_catalog",
    ],
    "4.5 no fabricated configuration": [
        f"{MAPPING}::test_nothing_is_defaulted",
        f"{FIXTURES}::test_8_duplicate_and_conflicting_identifiers_are_reported_not_resolved",
    ],
    # Phase 5: relationships.
    "5.1 every confirmed relationship has evidence": [
        f"{RELATIONSHIPS}::test_every_relationship_cites_its_finding_and_is_deterministic",
        f"{FIXTURES}::test_2_explicit_references_become_relationships_with_evidence",
    ],
    "5.2 unresolved endpoints explicit": [
        f"{RELATIONSHIPS}::test_a_selector_matching_several_workloads_is_not_resolved",
        f"{FIXTURES}::test_3_unresolved_references_stay_explicit",
    ],
    "5.3 direction and semantics not fabricated": [
        f"{RELATIONSHIPS}::test_compose_dependencies_hosts_and_volumes",
        f"{PROPOSAL}::test_a_connection_kind_is_stated_by_a_reviewer_never_guessed",
        f"{RELATIONSHIPS}::test_references_are_never_resolved_across_formats",
    ],
    "5.4 candidates distinct from IR edges": [
        f"{PROPOSAL}::test_the_default_proposal_holds_only_what_the_sources_establish"
    ],
    "5.5 deterministic relationship extraction": [
        f"{RELATIONSHIPS}::test_every_relationship_cites_its_finding_and_is_deterministic"
    ],
    # Phase 6: proposed architecture, review and acceptance.
    "6.1 canonical Architecture IR": [f"{PROPOSAL}::test_the_proposal_is_reproduced_from_the_stored_result"],
    "6.2 structurally validated": [
        f"{PROPOSAL}::test_the_proposal_is_validated_by_the_ir_and_the_catalog",
        f"{FIXTURES}::test_11_invalid_architecture_references_are_refused_by_the_ir",
    ],
    "6.3 evidence and provenance traceable": [
        f"{PROPOSAL}::test_provenance_is_never_verified_and_inferences_are_marked",
        f"{PROPOSAL}::test_configuration_enters_the_ir_with_its_provenance",
    ],
    "6.4 the baseline is never mutated": [
        f"{FIXTURES}::test_13_a_proposal_leaves_the_canonical_architecture_unchanged",
        f"{API}::test_accepting_into_an_architecture_adds_a_revision_and_never_rewrites_one",
    ],
    "6.5 acceptance through authorization and versioning": [
        f"{PROPOSAL}::test_a_reviewer_states_what_the_source_does_not",
        f"{PROPOSAL}::test_review_never_overrides_the_source_and_resolves_ambiguity_among_candidates",
        f"{API}::test_review_and_explicit_acceptance_create_a_new_architecture",
    ],
    "6.6 historical revisions immutable": [
        f"{API}::test_accepting_into_an_architecture_adds_a_revision_and_never_rewrites_one"
    ],
    # Phase 7: comparison and re-runs.
    "7.1 prior runs retrievable within scope": [f"{API}::test_access_isolation_and_listing"],
    "7.2 result identity and parser versions preserved": [
        f"{PROPOSAL}::test_versions_of_every_extractor_and_rule_are_recorded",
        f"{COMPARISON}::test_identical_inputs_are_comparable_and_identical",
    ],
    "7.3 comparability limitations explicit": [
        f"{COMPARISON}::test_coverage_differences_narrow_the_comparison",
        f"{COMPARISON}::test_unread_artifacts_are_not_compared",
    ],
    "7.4 no unsupported drift claims": [
        f"{COMPARISON}::test_different_parser_versions_are_not_comparable",
        f"{COMPARISON}::test_a_baseline_element_the_sources_do_not_describe_is_unknown_not_removed",
        f"{COMPARISON}::test_a_baseline_without_common_ids_is_not_compared_by_name",
    ],
    "7.5 stable contract for drift detection": [
        f"{COMPARISON}::test_comparisons_are_deterministic_and_cite_both_sides",
        f"{API}::test_reruns_are_compared_on_what_both_read",
    ],
    # Phase 8: API, persistence, authorization.
    "8.1 every operation enforces authorization": [
        f"{API}::test_access_isolation_and_listing",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
    ],
    "8.2 API contracts typed and documented": [
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
    ],
    "8.3 persistence transactional where required": [
        f"{API}::test_an_acceptance_that_cannot_be_recorded_leaves_no_revision",
        f"{API}::test_a_runs_result_never_changes_and_an_unaccepted_run_can_be_deleted",
        f"{MIGRATIONS}::test_downgrading_discovery_leaves_migration_plans_intact",
    ],
    "8.4 tenant isolation tested": [
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{API}::test_access_isolation_and_listing",
    ],
    "8.5 existing contracts remain compatible": [
        f"{S}.test_audit_sweep::test_every_mutating_project_endpoint_is_classified",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    "8.6 no canonical mutation without explicit authorization": [
        f"{API}::test_accepting_into_an_architecture_adds_a_revision_and_never_rewrites_one",
        f"{HERE}::test_there_is_no_live_scanning_or_connector_endpoint",
    ],
    # Phase 9: quality requirements.
    "9.1 the fifteen fixtures": [
        f"{FIXTURES}::test_1_a_valid_source_with_several_component_types",
        f"{FIXTURES}::test_15_declared_desired_state_is_never_reported_as_verified_runtime",
    ],
    "9.2 no silent exception swallowing": [
        f"{API}::test_an_engine_failure_exposes_nothing_and_stores_nothing",
        f"{API}::test_invalid_requests_store_nothing_and_limits_store_a_failed_run",
    ],
    "9.3 no fabricated evidence or architecture": [
        f"{FIXTURES}::test_5_unsupported_resource_types_are_reported_not_fabricated",
        f"{MAPPING}::test_images_are_evidence_not_proof",
    ],
    "9.4 no unsafe execution": [
        f"{SAFETY}::test_discovery_code_cannot_execute_or_fetch",
        f"{SAFETY}::test_nothing_is_executed_or_fetched_while_discovering",
    ],
    "9.5 no unbounded parser work": [
        f"{SAFETY}::test_hostile_content_fails_safely",
        f"{PROPOSAL}::test_a_discovery_beyond_the_result_limits_is_refused",
    ],
    "9.6 secret redaction": [
        f"{SAFETY}::test_secret_values_are_never_kept_in_any_format",
        f"{FIXTURES}::test_9_sensitive_values_are_redacted_everywhere",
        f"{API}::test_a_run_is_stored_without_content_or_secrets_and_read_back",
    ],
    "9.7 path traversal refused": [
        f"{SAFETY}::test_paths_are_names_never_locations",
        f"{DOMAIN}::test_unsafe_artifact_paths_are_refused",
    ],
    "9.8 determinism": [
        f"{FIXTURES}::test_14_repeated_identical_input_gives_equivalent_results",
        f"{MAPPING}::test_mapping_is_deterministic",
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
    assert len(ACCEPTANCE) == 53  # 45 acceptance criteria (phases 1-8) and 8 quality requirements


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
                assert name != "urllib.request", path


def test_there_is_no_live_scanning_or_connector_endpoint() -> None:
    text = ROUTES.read_text()
    assert "@router.post(" in text
    for word in ("connector", "credential", "scan", "cluster", "account", "execute", "apply"):
        assert not re.search(rf'["/][a-z-]*{word}', text), word  # no path names live access


def test_no_confidence_score_is_produced() -> None:
    for package in PACKAGES:
        for path, tree in _python(package):
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert node.value not in {"confidence", "score", "overall_confidence"}, path


def test_nothing_is_claimed_guaranteed() -> None:
    for path in (
        DOC,
        DOCS / "api" / "discovery.md",
        DOCS / "adr" / "ADR-021-deterministic-discovery.md",
        DOCS / "frontend" / "discovery-contract.md",
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
    assert "does not establish live runtime health" in text
    for heading in (
        "## Purpose and scope",
        "## Supported source formats and versions",
        "## Input requirements and processing limits",
        "## Discovery workflow",
        "## Provenance and verification semantics",
        "## Component mapping behavior",
        "## Relationship discovery rules",
        "## Proposed Architecture IR generation",
        "## Review and acceptance workflow",
        "## API contracts",
        "## Persistence and authorization",
        "## Security considerations",
        "## Unsupported constructs and known limitations",
        "## How drift detection consumes discovery results",
        "## Adding a source adapter",
        "## Tests",
        "## Example: a supported source produces component findings",
        "## Example: an explicit reference produces a candidate relationship",
        "## Example: an ambiguous mapping requires review",
        "## Example: missing evidence remains unknown",
        "## Example: an unsupported construct is reported without fabricated output",
        "## Example: a discovery proposal is accepted through the architecture lifecycle",
        "## Example: the canonical architecture remains unchanged before approval",
        "## Repository audit",
        "## Final review",
    ):
        assert heading in text, heading
    assert (DOCS / "frontend" / "discovery-contract.md").exists()
    assert "## Decision" in (DOCS / "adr" / "ADR-021-deterministic-discovery.md").read_text()
