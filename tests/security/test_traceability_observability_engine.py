"""Milestone 11 (architecture-level observability engine): each acceptance criterion of phases 0-11
mapped to the tests that prove it, plus structural guarantees (one architecture model, a generic
orchestrator, no dynamic code, no score or percentage, no telemetry read, documentation of every topic,
no claim that instrumentation works or an objective is met). Fails if a mapped test is renamed or
removed, or a criterion is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

from engines.observability.engine import INPUTS, Registry

U = "tests.unit.observability"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
DOMAIN = f"{U}.test_observability_domain"
ENGINE = f"{U}.test_observability_engine"
LOGS = f"{U}.test_observability_logs"
METRICS = f"{U}.test_observability_metrics"
TRACES = f"{U}.test_observability_traces"
HEALTH = f"{U}.test_observability_health_alerts"
CHECKS = f"{U}.test_observability_policy_requirements"
SUMMARY = f"{U}.test_observability_summary"
FIXTURES = f"{U}.test_observability_fixtures"
SERVICE = f"{U}.test_observability_service"
API = f"{I}.api.test_observability"
POLICY = "tests.unit.projects.test_architecture_policy"
MIGRATIONS = f"{I}.database.test_migrations"
HERE = f"{S}.test_traceability_observability_engine"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "observability-engine.md"
PACKAGES = ("engines/observability", "core/domain/observability")

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: repository audit.
    "existing observability files and their status were inspected": [
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
    # Phase 1: observability domain contract.
    "models are typed and validated": [
        f"{DOMAIN}::test_invalid_observability_properties_are_refused",
        f"{DOMAIN}::test_invalid_findings_are_refused",
        f"{DOMAIN}::test_the_request_is_canonical_and_bounded",
    ],
    "identifiers are stable and ordering deterministic": [
        f"{DOMAIN}::test_finding_ids_are_stable_and_include_dimension_and_check",
        f"{DOMAIN}::test_results_are_ordered_deduplicated_fingerprinted_and_round_trip",
    ],
    "unknown and unsupported states remain explicit": [
        f"{DOMAIN}::test_facts_keep_provenance_and_absence_is_not_a_fact",
        f"{DOMAIN}::test_a_component_has_one_explicit_state_per_dimension",
        f"{DOMAIN}::test_the_status_says_what_was_declared",
    ],
    "no unsupported operational guarantees": [
        f"{DOMAIN}::test_findings_round_trip_and_carry_no_score",
        f"{HERE}::test_nothing_is_claimed_verified_or_attained",
    ],
    # Phase 2: orchestrator.
    "analyzer selection is explicit": [
        f"{ENGINE}::test_selection_is_explicit_and_requests_name_only_what_exists",
        f"{HERE}::test_the_orchestrator_is_generic",
    ],
    "identical inputs produce identical normalized results": [
        f"{ENGINE}::test_the_same_inputs_give_the_same_result_whatever_the_order",
        f"{FIXTURES}::test_the_whole_registry_is_deterministic_whatever_the_order",
    ],
    "unsupported analyzers do not erase unrelated results": [
        f"{ENGINE}::test_a_failing_analyzer_is_isolated_and_logged_by_type",
        f"{ENGINE}::test_a_malformed_output_is_refused_and_the_rest_counts",
    ],
    "failures are handled safely and observable through logging": [
        f"{ENGINE}::test_a_failing_analyzer_is_isolated_and_logged_by_type",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_analysis_without_internals",
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
    ],
    # Phase 3: logging.
    "logging findings identify the component and its modeled evidence": [
        f"{LOGS}::test_a_critical_service_with_missing_logging_configuration",
        f"{FIXTURES}::test_1_critical_service_with_explicitly_modeled_logs_and_metrics",
    ],
    "unknown configuration is not a confirmed absence": [
        f"{FIXTURES}::test_2_critical_service_with_missing_logging_configuration",
        f"{LOGS}::test_a_standard_or_unclassified_component_without_logging_is_not_a_finding",
        f"{API}::test_missing_configuration_is_unknown_not_configured",
    ],
    "sensitive values are never emitted": [
        f"{LOGS}::test_proposed_facts_make_a_candidate_and_no_secret_is_shown",
        f"{DOMAIN}::test_a_finding_or_check_that_would_show_a_secret_is_refused",
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
    ],
    # Phase 4: metrics.
    "metric requirements are traceable to policies or objectives": [
        f"{CHECKS}::test_required_metric_kinds",
        f"{CHECKS}::test_latency_and_throughput_objectives_map_to_their_kinds",
    ],
    "metrics findings are deterministic": [f"{METRICS}::test_the_analysis_is_deterministic"],
    "missing runtime telemetry is distinct from missing configuration": [
        f"{METRICS}::test_no_metric_value_is_ever_computed",
        f"{FIXTURES}::test_3_metrics_configured_without_a_modeled_collection_path",
    ],
    # Phase 5: tracing and propagation.
    "tracing findings reference the exact modeled path or elements": [
        f"{TRACES}::test_a_path_with_incomplete_propagation_evidence",
        f"{FIXTURES}::test_5_multi_service_path_with_incomplete_propagation_evidence",
    ],
    "propagation checks are deterministic": [
        f"{TRACES}::test_no_completeness_or_sampling_is_claimed_and_the_analysis_is_deterministic",
        f"{FIXTURES}::test_4_multi_service_request_path_with_propagation_represented",
    ],
    "no claim of end-to-end trace completeness": [
        f"{TRACES}::test_no_completeness_or_sampling_is_claimed_and_the_analysis_is_deterministic"
    ],
    # Phase 6: health checks and alerting.
    "findings reference the modeled health-check or alerting configuration": [
        f"{HEALTH}::test_a_health_check_with_no_modeled_consumer",
        f"{HEALTH}::test_an_alert_rule_on_a_signal_that_is_not_emitted",
        f"{FIXTURES}::test_6_health_check_with_an_explicit_consumer",
        f"{FIXTURES}::test_7_health_check_with_no_modeled_consumer",
        f"{FIXTURES}::test_8_alert_rule_with_a_modeled_signal_source",
    ],
    "missing thresholds and routing remain explicit": [
        f"{HEALTH}::test_alerts_without_a_modeled_delivery_path",
        f"{HEALTH}::test_every_reached_backend_counts_for_delivery",
    ],
    "no runtime behavior is asserted without telemetry": [
        f"{HEALTH}::test_no_threshold_or_firing_is_claimed_and_the_analysis_is_deterministic"
    ],
    # Phase 7: SLO and requirement traceability.
    "requirement traceability is stable": [
        f"{CHECKS}::test_monitoring_words_map_by_the_documented_table",
        f"{CHECKS}::test_checks_and_findings_are_deterministic",
        f"{SERVICE}::test_requirement_ids_select_what_is_evaluated",
    ],
    "missing evidence never defaults to success": [
        f"{CHECKS}::test_an_undeclared_criticality_is_never_a_pass",
        f"{CHECKS}::test_an_uncollected_indicator_or_undelivered_alert_is_not_verifiable",
        f"{CHECKS}::test_an_objective_no_metric_kind_measures_is_unsupported",
        f"{FIXTURES}::test_9_slo_objective_without_a_measurable_indicator",
    ],
    "configuration is distinguished from observed performance": [
        f"{CHECKS}::test_a_measured_and_alerted_objective_is_traceable_but_never_met",
        f"{API}::test_requirements_are_traced_never_attained",
    ],
    # Phase 8: coverage summary and prioritization.
    "the summary is reproducible from the detailed result": [
        f"{SUMMARY}::test_the_summary_is_reproducible_from_the_detailed_result",
        f"{DOMAIN}::test_the_summary_is_reproducible_from_the_components",
    ],
    "aggregation rules are documented and tested": [
        f"{SUMMARY}::test_the_scope_counts_unsupported_components_apart",
        f"{SUMMARY}::test_coverage_counts_each_component_once_per_dimension",
        f"{SUMMARY}::test_collection_paths_per_signal",
        f"{SUMMARY}::test_priority_order_is_severity_then_basis_then_type",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    "no misleading global percentage or maturity score": [
        f"{SUMMARY}::test_no_percentage_score_or_maturity",
        f"{SUMMARY}::test_severity_is_kept_apart_from_coverage",
        f"{HERE}::test_no_score_or_percentage_anywhere_in_the_results",
    ],
    # Phase 9: API, persistence and authorization.
    "every operation enforces authorization": [
        f"{SERVICE}::test_access",
        f"{API}::test_access_and_listing",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
    ],
    "API schemas are typed and documented": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
    ],
    "persistence is transactional and append-only": [
        f"{SERVICE}::test_an_archive_during_the_calculation_refuses_the_store",
        f"{API}::test_analyses_are_append_only_and_reads_cost_the_same",
        f"{MIGRATIONS}::test_downgrading_observability_leaves_security_intact",
        f"{S}.test_audit_sweep::test_every_project_scoped_audit_action_is_exercised",
    ],
    "existing frontend and API contracts remain compatible": [
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{HERE}::test_the_frontend_contract_is_documented",
    ],
    # Phase 10: testing and quality.
    "the ten architecture fixtures": [
        f"{FIXTURES}::test_1_critical_service_with_explicitly_modeled_logs_and_metrics",
        f"{FIXTURES}::test_2_critical_service_with_missing_logging_configuration",
        f"{FIXTURES}::test_3_metrics_configured_without_a_modeled_collection_path",
        f"{FIXTURES}::test_4_multi_service_request_path_with_propagation_represented",
        f"{FIXTURES}::test_5_multi_service_path_with_incomplete_propagation_evidence",
        f"{FIXTURES}::test_6_health_check_with_an_explicit_consumer",
        f"{FIXTURES}::test_7_health_check_with_no_modeled_consumer",
        f"{FIXTURES}::test_8_alert_rule_with_a_modeled_signal_source",
        f"{FIXTURES}::test_9_slo_objective_without_a_measurable_indicator",
        f"{FIXTURES}::test_10_an_incomplete_architecture_yields_partial_results_never_configured",
    ],
    "analysis against an exact revision, stored and read back": [
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
        f"{SERVICE}::test_an_analysis_is_stored_with_its_inputs_and_audited",
    ],
    "invalid architecture or revision and safe errors": [
        f"{SERVICE}::test_invalid_requests_store_nothing",
        f"{API}::test_an_engine_failure_is_stored_as_failed",
    ],
    "determinism: ids, order, summary, evidence, normalized result": [
        f"{FIXTURES}::test_the_whole_registry_is_deterministic_whatever_the_order",
        f"{SERVICE}::test_the_same_inputs_give_the_same_result",
    ],
    "the architecture IR remains unchanged": [
        f"{FIXTURES}::test_the_architecture_is_never_modified",
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
    ],
    "the other engines' contracts remain compatible": [
        f"{S}.test_traceability_security_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_reliability_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_capacity_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{POLICY}::test_names_are_normalized_and_the_dict_is_canonical",
    ],
    "no fabricated telemetry and no silent exception swallowing": [
        f"{FIXTURES}::test_the_engine_reads_no_telemetry_and_stays_apart_from_the_platform",
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
    ],
    "no uncontrolled graph traversal": [
        f"{FIXTURES}::test_the_cost_is_bounded_on_a_large_graph",
        f"{ENGINE}::test_collection_follows_modeled_telemetry_paths_through_collectors",
    ],
    "no dynamic code": [f"{HERE}::test_the_engine_executes_no_dynamic_code"],
    # Phase 11: documentation.
    "every documentation topic is covered, with a modeled-but-unverified example": [
        f"{HERE}::test_every_documentation_topic_is_covered"
    ],
    "the documents state what the engine does not do": [
        f"{HERE}::test_nothing_is_claimed_verified_or_attained"
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
    assert len(ACCEPTANCE) == 45  # 4 + 4 + 4 + 3 + 3 + 3 + 3 + 3 + 3 + 4 + 9 + 2 (phases 0-11)


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
    """engine.py names no analyzer, finding type or observability property: what to look for lives in
    the analyzers, how to run them in the orchestrator."""
    from engines.observability.registry import default_registry  # noqa: PLC0415 - the shipped analyzers

    text = (ROOT / "engines" / "observability" / "engine.py").read_text()
    # Some ids are also names the orchestrator knows for another reason: the inputs an analyzer may
    # declare ("policy", "requirements") and the declared criticality each component result reports
    # (part of its contract, as the security engine's exposure).
    shared = INPUTS | {"criticality"}
    for analyzer_id in _shipped(default_registry()):
        if analyzer_id not in shared:
            assert f'"{analyzer_id}"' not in text, analyzer_id
    for name in ("logs", "metrics", "traces", "health_check", "alerts", "trace_propagation", "telemetry"):
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


def test_no_score_or_percentage_anywhere_in_the_results() -> None:
    """No field of any result, finding, check or summary is a score, a percentage, a maturity level,
    an attainment or an error budget."""
    text = (ROOT / "core" / "domain" / "observability" / "results.py").read_text()
    for word in ("score", "percentage", "percent", "maturity", "attainment", "error_budget", "rating"):
        assert not re.search(rf'"{word}"|\b{word}\s*:', text), word


def test_nothing_is_claimed_verified_or_attained() -> None:
    """The documents state what the analysis does not do, and never claim that instrumentation works
    or that an objective is met."""
    engine = DOC.read_text()
    for statement in (
        "analyzes architecture-level observability configuration",
        "does not collect or query live telemetry",
        "does not prove instrumentation is functioning",
        "does not calculate SLO attainment",
        "Findings require engineering review",
    ):
        assert statement in engine, statement
    for path in (
        DOC,
        DOCS / "api" / "observability.md",
        DOCS / "adr" / "ADR-016-deterministic-observability.md",
        DOCS / "frontend" / "observability-contract.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(
            r"[^.]*\b(?:proves?|guarantee[sd]?|verified|attain(?:s|ed)?|is met)\b[^.]*", text, re.I
        ):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Phase 11's documentation list (12 topics), the modeled-but-unverified example, and every
    analyzer."""
    text = DOC.read_text()
    for heading in (
        "## Purpose and scope",
        "## Supported analyzers and coverage categories",
        "## Required architecture properties",
        "## Coverage aggregation rules",
        "## Evidence and provenance",
        "## Unknown and unsupported behavior",
        "## Requirement and SLO traceability",
        "## API contracts",
        "## Example analysis",
        "## Example: unknown is not configured",
        "## Adding a deterministic analyzer",
        "## Tests",
        "## Known limitations",
        "## Persistence",
        "## Authorization",
        "## Limits and performance",
    ):
        assert heading in text, heading
    example = text.split("## Example analysis", 1)[1].split("\n## ", 1)[0]
    assert "runtime observability unverified" in example
    assert "configuration_only" in example
    unknown = text.split("## Example: unknown is not configured", 1)[1].split("\n## ", 1)[0]
    assert "not_verifiable" in unknown
    assert "not_evaluable" in unknown
    from engines.observability.registry import default_registry  # noqa: PLC0415 - the shipped analyzers

    for analyzer_id in _shipped(default_registry()):
        assert f"`{analyzer_id}`" in text, analyzer_id


def test_the_frontend_contract_is_documented() -> None:
    text = (DOCS / "frontend" / "observability-contract.md").read_text()
    for field in ("score", "errorBudgetRemaining", "coverage", "slos", "gaps"):
        assert field in text, field


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known limitations"):
        assert heading in text, heading
    assert "Reused:" in text
