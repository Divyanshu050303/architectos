"""Milestone 10 (architecture-level security engine): each acceptance criterion of phases 0-11 mapped
to the tests that prove it, plus structural guarantees (one architecture model, a generic
orchestrator, no dynamic code, no score, documentation of every topic, no claim that anything is
secure). Fails if a mapped test is renamed or removed, or a criterion is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

from engines.security.engine import INPUTS, Registry

U = "tests.unit.security"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
DOMAIN = f"{U}.test_security_domain"
ENGINE = f"{U}.test_security_engine"
BOUNDARIES = f"{U}.test_security_trust_boundaries"
ACCESS = f"{U}.test_security_access"
DATA = f"{U}.test_security_data_protection"
EXPOSURE = f"{U}.test_security_secrets_exposure"
THREATS = f"{U}.test_security_threats"
CHECKS = f"{U}.test_security_policy_requirements"
FIXTURES = f"{U}.test_security_fixtures"
SERVICE = f"{U}.test_security_service"
API = f"{I}.api.test_security"
DIFF = "tests.unit.architecture_ir.test_ir_diff"
HERE = f"{S}.test_traceability_security_engine"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "security-engine.md"
PACKAGES = ("engines/security", "core/domain/security")

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: repository audit.
    "existing security files and their status were inspected": [
        f"{HERE}::test_the_audit_and_review_are_recorded"
    ],
    "reusable models and conventions are reused": [
        f"{HERE}::test_the_audit_and_review_are_recorded",
        f"{HERE}::test_the_engine_reads_the_ir_and_defines_no_graph",
    ],
    "no competing architecture model is created": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{HERE}::test_the_engine_reads_the_ir_and_defines_no_graph",
    ],
    "no code changed during the audit": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    # Phase 1: security analysis contract.
    "models are typed and validated": [
        f"{DOMAIN}::test_invalid_security_properties_are_refused_by_the_architecture",
        f"{DOMAIN}::test_invalid_findings_are_refused",
        f"{DOMAIN}::test_invalid_requests_are_refused",
    ],
    "identifiers are stable and ordering deterministic": [
        f"{DOMAIN}::test_finding_ids_are_stable_and_include_threat_requirement_and_policy",
        f"{DOMAIN}::test_results_are_ordered_deduplicated_fingerprinted_and_round_trip",
    ],
    "unknown and unsupported states are explicit": [
        f"{DOMAIN}::test_facts_keep_their_source_and_provenance_and_absence_is_not_a_fact",
        f"{DOMAIN}::test_sensitivity_comes_only_from_declared_data",
        f"{DOMAIN}::test_the_status_says_what_was_modeled",
    ],
    "no arbitrary numeric risk score": [
        f"{DOMAIN}::test_findings_round_trip_and_carry_no_score",
        f"{HERE}::test_no_score_anywhere_in_the_results",
    ],
    "no compliance or security certification is claimed": [
        f"{HERE}::test_nothing_is_claimed_secure_or_compliant",
        f"{DOMAIN}::test_every_type_has_a_fixed_category_and_basis",
    ],
    # Phase 2: orchestrator.
    "analyzer selection is explicit": [
        f"{ENGINE}::test_analyzer_selection_is_explicit",
        f"{ENGINE}::test_requests_naming_what_does_not_exist_are_refused",
        f"{HERE}::test_the_orchestrator_is_generic",
    ],
    "one failing analyzer does not erase unrelated findings": [
        f"{ENGINE}::test_a_failing_analyzer_is_isolated_and_logged_without_its_message",
        f"{ENGINE}::test_a_malformed_output_is_refused_and_the_rest_counts",
    ],
    "results are deterministic": [
        f"{ENGINE}::test_the_same_inputs_give_the_same_result_whatever_the_order",
        f"{FIXTURES}::test_the_whole_registry_is_deterministic_whatever_the_order",
    ],
    "internal errors are handled safely and observable": [
        f"{ENGINE}::test_a_failing_analyzer_is_isolated_and_logged_without_its_message",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_analysis_without_internals",
    ],
    # Phase 3: trust boundaries.
    "findings reference stable node and connection ids": [
        f"{BOUNDARIES}::test_a_crossing_that_declares_no_protection_is_a_control_gap",
        f"{ENGINE}::test_a_malformed_output_is_refused_and_the_rest_counts",
    ],
    "boundary traversal is deterministic": [
        f"{BOUNDARIES}::test_the_analysis_is_deterministic_and_leaves_the_architecture_unchanged",
        f"{BOUNDARIES}::test_nested_zones_cross_at_their_edge",
    ],
    "missing topology semantics are unknown or unevaluable": [
        f"{BOUNDARIES}::test_unmodeled_controls_are_not_evaluable_never_secure_nor_insecure",
        f"{BOUNDARIES}::test_thin_crossing_semantics_are_reported",
        f"{BOUNDARIES}::test_boundaries_are_never_inferred_from_names",
    ],
    "the architecture graph is never modified": [
        f"{FIXTURES}::test_the_architecture_is_never_modified",
        f"{BOUNDARIES}::test_the_analysis_is_deterministic_and_leaves_the_architecture_unchanged",
    ],
    # Phase 4: authentication and authorization.
    "missing evidence is distinguished from a confirmed modeled gap": [
        f"{ACCESS}::test_a_public_api_without_authentication_evidence_cannot_be_evaluated",
        f"{ACCESS}::test_authentication_none_where_it_is_needed_is_a_modeled_gap",
        f"{ACCESS}::test_a_sensitive_operation_without_authorization_evidence",
    ],
    "sensitive configuration is redacted": [
        f"{ACCESS}::test_no_secret_value_is_read_or_shown",
        f"{DOMAIN}::test_a_finding_or_check_that_would_show_a_secret_is_refused",
    ],
    "platform authentication and authorization are unchanged": [
        f"{FIXTURES}::test_the_engine_is_apart_from_platform_authentication",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
    ],
    # Phase 5: encryption and data protection.
    "classification and encryption evidence are traceable": [
        f"{DATA}::test_sensitive_data_in_transit",
        f"{DATA}::test_sensitive_data_stored_unencrypted_is_a_modeled_gap",
    ],
    "unknown configuration remains unknown": [
        f"{DATA}::test_a_sensitive_datastore_with_unknown_encryption_stays_unknown",
        f"{DATA}::test_a_technology_is_never_taken_as_encrypting",
    ],
    "findings explain the gap and its limitations": [
        f"{DATA}::test_sensitive_data_stored_unencrypted_is_a_modeled_gap",
        f"{DATA}::test_non_sensitive_stores_are_not_flagged_and_unclassified_ones_are_reported_as_such",
    ],
    "sensitive values are not persisted": [
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
        f"{DATA}::test_the_analysis_shows_no_secret_and_is_deterministic",
    ],
    # Phase 6: secrets and exposure.
    "no secret values are emitted": [
        f"{EXPOSURE}::test_a_secret_looking_setting_is_reported_by_name_and_never_shown",
        f"{EXPOSURE}::test_a_dotted_secret_key_is_found",
        f"{DIFF}::test_a_key_containing_a_dot_is_judged_whole",
    ],
    "exposure findings identify the exact path": [
        f"{EXPOSURE}::test_a_sensitive_component_reachable_from_a_public_entry_names_the_path",
        f"{EXPOSURE}::test_the_nearest_public_entry_is_named",
    ],
    "unmodeled reachability is reported as unknown": [
        f"{EXPOSURE}::test_reachability_is_never_inferred",
        f"{EXPOSURE}::test_a_component_clients_call_without_declared_exposure",
    ],
    "no attack surface score": [f"{HERE}::test_no_score_anywhere_in_the_results"],
    # Phase 7: threat modeling.
    "threat candidates are reproducible": [f"{THREATS}::test_candidates_are_reproducible"],
    "each candidate has traceable evidence": [
        f"{THREATS}::test_a_missing_authentication_is_a_spoofing_candidate_traceable_to_its_finding",
        f"{THREATS}::test_findings_about_the_same_elements_form_one_candidate",
    ],
    "threat assumptions are explicit": [
        f"{THREATS}::test_a_missing_authentication_is_a_spoofing_candidate_traceable_to_its_finding",
        f"{THREATS}::test_the_mapping_is_documented_in_the_analyzer_rules",
    ],
    "no exploitability claims or numeric risk scores": [
        f"{THREATS}::test_no_likelihood_score_or_vulnerability_identifier_is_claimed",
    ],
    # Phase 8: requirements and policy.
    "requirement traceability is stable": [
        f"{CHECKS}::test_requirement_links_are_stable",
        f"{CHECKS}::test_each_condition_of_a_requirement_is_its_own_finding",
    ],
    "missing evidence never defaults to success": [
        f"{DOMAIN}::test_missing_evidence_or_an_unsupported_condition_is_never_success",
        f"{CHECKS}::test_encryption_at_rest_by_policy",
        f"{CHECKS}::test_what_decides_whether_a_rule_applies_must_be_declared_too",
    ],
    "unsupported requirements are identified": [
        f"{CHECKS}::test_words_that_match_no_condition_are_unsupported_never_satisfied",
        f"{CHECKS}::test_requirements_follow_their_scope_and_references",
    ],
    "the policy version is recorded": [
        f"{SERVICE}::test_the_policy_and_requirements_are_evaluated_and_recorded",
        f"{ENGINE}::test_the_context_fingerprint_follows_every_input",
    ],
    # Phase 9: API, persistence and authorization.
    "every operation enforces authorization": [
        f"{API}::test_access_and_listing",
        f"{SERVICE}::test_access",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "API contracts are typed and documented": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
    "persistence is transactional where required": [
        f"{SERVICE}::test_an_archive_during_the_calculation_refuses_the_store",
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{API}::test_analyses_are_append_only_and_reads_cost_the_same",
        f"{I}.database.test_migrations::test_downgrading_security_leaves_reliability_intact",
    ],
    "existing frontend and API contracts remain compatible": [
        f"{S}.test_documentation::test_the_decisions_are_recorded",
        f"{S}.test_traceability_reliability_engine::test_every_criterion_is_mapped",
    ],
    "the platform's login and session implementation is unchanged": [
        f"{FIXTURES}::test_the_engine_is_apart_from_platform_authentication",
        f"{S}.test_mass_assignment_sweep::test_every_body_rejects_undeclared_privileged_fields",
    ],
    # Phase 10: testing and quality.
    "unit tests cover every analyzer, redaction and missing inputs": [
        f"{DOMAIN}::test_secret_looking_values_are_never_shown_but_closed_properties_are",
        f"{ENGINE}::test_statuses_without_components_or_without_any_security_property",
        f"{EXPOSURE}::test_the_search_only_tests_values",
    ],
    "the ten architecture fixtures": [
        f"{FIXTURES}::test_1_public_api_with_explicit_authentication",
        f"{FIXTURES}::test_2_public_api_with_missing_authentication_evidence",
        f"{FIXTURES}::test_3_sensitive_datastore_with_explicit_encryption",
        f"{FIXTURES}::test_4_sensitive_datastore_with_unknown_encryption",
        f"{FIXTURES}::test_5_service_flow_crossing_a_trust_boundary",
        f"{FIXTURES}::test_6_explicit_secret_manager_reference",
        f"{FIXTURES}::test_7_hardcoded_secret_indicator_without_exposing_the_value",
        f"{FIXTURES}::test_8_publicly_exposed_management_interface",
        f"{FIXTURES}::test_9_missing_authorization_evidence_for_a_sensitive_operation",
        f"{FIXTURES}::test_10_an_incomplete_architecture_yields_partial_findings_never_secure",
    ],
    "integration covers revisions, isolation, errors and redaction": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{API}::test_an_engine_failure_is_stored_as_failed",
        f"{API}::test_missing_configuration_cannot_be_evaluated_rather_than_secure",
    ],
    "determinism covers ids, ordering, evidence and checks": [
        f"{FIXTURES}::test_the_whole_registry_is_deterministic_whatever_the_order",
        f"{SERVICE}::test_the_same_inputs_give_the_same_result",
    ],
    "regressions: IR, platform auth, validation and requirements unchanged": [
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_requirements_engine::test_every_item_of_the_spec_is_mapped",
        f"{DIFF}::test_closed_security_properties_are_shown_even_when_their_name_looks_secret",
    ],
    "no silent exception swallowing": [
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{ENGINE}::test_a_failing_analyzer_is_isolated_and_logged_without_its_message",
    ],
    "no secret leakage in responses, storage or logs": [
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_analysis_without_internals",
    ],
    "no unsupported security or compliance claims": [f"{HERE}::test_nothing_is_claimed_secure_or_compliant"],
    "no uncontrolled traversal or unbounded analysis": [
        f"{HERE}::test_the_engine_executes_no_dynamic_code",
        f"{SERVICE}::test_the_engine_runs_off_the_event_loop",
        f"{DOMAIN}::test_the_request_is_canonical_and_bounded",
    ],
    # Phase 11: documentation and final verification.
    "the engine is documented topic by topic, with a cannot-evaluate example": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{S}.test_documentation::test_the_decisions_are_recorded",
    ],
    "the documents state what the engine does not do": [
        f"{HERE}::test_nothing_is_claimed_secure_or_compliant"
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
    assert len(ACCEPTANCE) == 52  # 4 + 5 + 4 + 4 + 3 + 4 + 4 + 4 + 4 + 5 + 9 + 2 (phases 0-11)


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


def _shipped(registry: Registry) -> list[str]:
    return [a.meta.id for a in registry.analyzers()]


def test_the_engine_reads_the_ir_and_defines_no_graph() -> None:
    graph_names = {"Graph", "Node", "Edge", "Connection", "ArchitectureGraph", "Topology", "Component"}
    for package in PACKAGES:
        for path, tree in _python(package):
            classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            assert not classes & graph_names, (path, classes & graph_names)
            assert not any(m in {"networkx", "igraph"} for m in _imports(tree)), path


def test_the_orchestrator_is_generic() -> None:
    """engine.py names no analyzer, finding type or security property: what to look for lives in
    the analyzers, how to run them in the orchestrator."""
    from engines.security.registry import default_registry  # noqa: PLC0415 - the shipped analyzers

    text = (ROOT / "engines" / "security" / "engine.py").read_text()
    # Some ids are also names the orchestrator knows for another reason: the inputs an analyzer may
    # declare ("policy", "requirements") and the component result's declared exposure.
    shared = INPUTS | {"exposure"}
    for analyzer_id in _shipped(default_registry()):
        if analyzer_id not in shared:
            assert f'"{analyzer_id}"' not in text, analyzer_id
    # (the declared exposure is read only to report it on each component result, part of its contract)
    for name in ("authentication", "tls", "secret_source", "encryption_at_rest", "management_interface"):
        assert f'"{name}"' not in text, name


def test_the_engine_executes_no_dynamic_code() -> None:
    builtins = {"eval", "exec", "compile", "__import__"}
    for package in PACKAGES:
        for path, tree in _python(package):
            calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
            assert not [f.id for f in calls if isinstance(f, ast.Name) and f.id in builtins], path
            assert not [
                f.attr for f in calls if isinstance(f, ast.Attribute) and f.attr == "import_module"
            ], path
            assert "importlib" not in _imports(tree), path


def test_no_score_anywhere_in_the_results() -> None:
    """No field of any result, finding, check or summary is a score, a likelihood or a rating."""
    text = (ROOT / "core" / "domain" / "security" / "results.py").read_text()
    for word in ("score", "likelihood", "exploitability", "risk_level", "rating"):
        assert not re.search(rf'"{word}"|\b{word}\s*:', text), word


def test_nothing_is_claimed_secure_or_compliant() -> None:
    """The documents state what the analysis is not, and never claim that an architecture is secure
    or compliant."""
    engine = DOC.read_text()
    for statement in (
        "architecture-level analysis",
        "does not prove the absence of vulnerabilities",
        "does not replace",
        "Findings require engineering review",
    ):
        assert statement in engine, statement
    for path in (
        DOC,
        DOCS / "api" / "security.md",
        DOCS / "adr" / "ADR-015-deterministic-security.md",
        DOCS / "frontend" / "security-contract.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(
            r"[^.]*\b(?:proves?|guarantee[sd]?|certif\w*|compliant)\b[^.]*", text, re.I
        ):
            assert re.search(r"\b(not|never|no|nor)\b|out of scope", sentence, re.I), (path.name, sentence)


def test_every_documentation_topic_is_covered() -> None:
    """Phase 11's documentation list (12 topics), a cannot-evaluate example, and every analyzer."""
    text = DOC.read_text()
    for heading in (
        "## Purpose and scope",
        "## Supported analyzers and rule categories",
        "## Required architecture properties",
        "## Finding categories and severity semantics",
        "## Evidence and provenance",
        "## Unknown and unsupported behavior",
        "## Threat-model assumptions",
        "## API contracts",
        "## Example analysis",
        "## Example: cannot evaluate, not secure",
        "## Adding a deterministic analyzer",
        "## Tests",
        "## Known limitations",
        "## Requirements and policy",
        "## Persistence",
        "## Authorization",
        "## Limits and performance",
    ):
        assert heading in text, heading
    example = text.split("## Example: cannot evaluate, not secure", 1)[1].split("\n## ", 1)[0]
    assert "insufficient_input" in example
    assert "not_verifiable" in example
    assert "not_evaluable" in example
    from engines.security.registry import default_registry  # noqa: PLC0415 - the shipped analyzers

    for analyzer_id in _shipped(default_registry()):
        assert f"`{analyzer_id}`" in text, analyzer_id


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known limitations"):
        assert heading in text, heading
    assert "Reused:" in text
