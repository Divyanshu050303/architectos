"""Milestone 9 (deterministic reliability engine): each acceptance criterion of phases 0-10 mapped to
the tests that prove it, plus structural guarantees (one architecture model, a generic
orchestrator, no shipped reliability figures, no dynamic code, documentation of every topic).
Fails if a mapped test is renamed or removed, or a criterion is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

from engines.reliability.engine import Registry

U = "tests.unit.reliability"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
DOMAIN = f"{U}.test_reliability_domain"
ENGINE = f"{U}.test_reliability_engine"
TOPOLOGY = f"{U}.test_reliability_topology"
MODELS = f"{U}.test_reliability_models"
PATHS = f"{U}.test_reliability_paths"
FINDINGS = f"{U}.test_reliability_findings"
OBJECTIVES = f"{U}.test_reliability_objectives"
SERVICE = f"{U}.test_reliability_service"
HARDENING = f"{U}.test_reliability_hardening"
API = f"{I}.api.test_reliability"
HERE = f"{S}.test_traceability_reliability_engine"

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "architecture" / "reliability-engine.md"
PACKAGES = ("engines/reliability", "core/domain/reliability")

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: repository audit.
    "existing reliability functionality has been inspected": [
        f"{HERE}::test_the_audit_and_review_are_recorded"
    ],
    "reusable conventions are identified and reused": [
        f"{HERE}::test_the_audit_and_review_are_recorded",
        f"{HERE}::test_the_engine_reads_the_ir_and_defines_no_graph",
    ],
    "no duplicate architecture model is proposed": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{HERE}::test_the_engine_reads_the_ir_and_defines_no_graph",
    ],
    "no code changed during the audit": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    # Phase 1: reliability domain contract.
    "domain models are typed and validated": [
        f"{DOMAIN}::test_invalid_reliability_properties_are_refused_by_the_architecture",
        f"{DOMAIN}::test_invalid_objectives_are_refused",
        f"{DOMAIN}::test_the_request_is_canonical_and_bounded",
    ],
    "units are explicit": [
        f"{DOMAIN}::test_availability_is_an_exact_fraction_kept_to_nine_places",
        f"{DOMAIN}::test_durations_have_explicit_units",
        f"{DOMAIN}::test_objectives_are_typed_and_unit_aware",
    ],
    "unknown values remain distinguishable from zero": [
        f"{DOMAIN}::test_missing_evidence_is_never_success",
        f"{ENGINE}::test_an_unknown_value_is_as_missing_as_an_absent_one",
        f"{MODELS}::test_components_nothing_is_declared_for_say_what_is_missing",
    ],
    "result serialization is deterministic": [
        f"{DOMAIN}::test_results_are_ordered_deduplicated_fingerprinted_and_round_trip",
        f"{DOMAIN}::test_finding_ids_are_stable_and_ordering_is_by_severity",
    ],
    "no API or persistence changes beyond the established architecture": [
        f"{DOMAIN}::test_component_facts_keep_their_source_and_provenance",
        f"{DOMAIN}::test_reliability_properties_apply_only_where_they_mean_something",
    ],
    # Phase 2: model contract and orchestrator.
    "model selection is explicit and testable": [
        f"{ENGINE}::test_the_registry_refuses_duplicates_and_unknown_resources_and_keeps_its_order",
        f"{ENGINE}::test_the_first_model_to_establish_a_value_takes_precedence",
        f"{HERE}::test_the_orchestrator_is_generic",
    ],
    "unsupported inputs produce actionable results": [
        f"{ENGINE}::test_missing_inputs_are_named_only_for_what_stays_unknown",
        f"{ENGINE}::test_components_no_model_supports_are_reported_and_clients_are_out_of_scope",
    ],
    "the same inputs produce the same normalized output": [
        f"{ENGINE}::test_the_same_inputs_give_the_same_result_whatever_the_node_order",
        f"{HARDENING}::test_repeated_and_reordered_runs_are_identical",
    ],
    "calculation failures are isolated and reported safely": [
        f"{ENGINE}::test_failing_and_malformed_models_are_isolated",
        f"{ENGINE}::test_failing_and_overreaching_steps_are_isolated",
    ],
    # Phase 3: topology and dependency analysis.
    "findings reference stable IR node and connection ids": [
        f"{TOPOLOGY}::test_a_critical_dependency_on_a_single_point",
        f"{HARDENING}::test_a_node_id_with_dots_is_named_whole",
        f"{FINDINGS}::test_every_finding_is_complete_and_actionable",
    ],
    "graph traversal is deterministic": [
        f"{TOPOLOGY}::test_analysis_is_deterministic_and_leaves_the_architecture_unchanged",
        f"{PATHS}::test_paths_are_deterministic",
    ],
    "cycles are handled safely": [
        f"{TOPOLOGY}::test_cycles_are_safe_and_reported",
        f"{HARDENING}::test_a_cycle_through_every_node_completes_and_is_named_briefly",
    ],
    "unsupported topology semantics are reported": [
        f"{TOPOLOGY}::test_an_unstated_interaction_is_treated_as_waiting_and_reported",
        f"{TOPOLOGY}::test_only_explicitly_required_connections_are_followed",
        f"{FINDINGS}::test_alternatives_that_cannot_be_composed_are_named",
    ],
    "no architecture structure is modified by analysis": [
        f"{TOPOLOGY}::test_analysis_is_deterministic_and_leaves_the_architecture_unchanged"
    ],
    # Phase 4: component reliability calculations.
    "each formula has focused unit tests": [
        f"{MODELS}::test_availability_from_mtbf_and_mttr",
        f"{MODELS}::test_k_of_n_with_independent_failures_and_automatic_failover",
        f"{MODELS}::test_recovery_time",
        f"{MODELS}::test_data_loss_window",
        f"{HARDENING}::test_k_of_n_is_exact",
    ],
    "invalid units and values are rejected or marked unsupported": [
        f"{MODELS}::test_zero_and_undefined_denominators",
        f"{DOMAIN}::test_invalid_reliability_properties_are_refused_by_the_architecture",
        f"{HARDENING}::test_beyond_a_thousand_replicas_is_not_calculated",
    ],
    "formula inputs and outputs are recorded in the trace": [
        f"{MODELS}::test_the_shipped_models_feed_the_paths",
        f"{TOPOLOGY}::test_series_dependencies_multiply_and_say_what_they_assume",
    ],
    "no hardcoded reliability benchmarks or assumed provider SLAs": [
        f"{HERE}::test_no_reliability_figures_are_shipped",
        f"{MODELS}::test_redundancy_is_not_combined_without_its_assumptions",
    ],
    # Phase 5: end-to-end availability.
    "the exact analyzed path is identified": [
        f"{TOPOLOGY}::test_a_single_dependency",
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
    ],
    "assumptions are visible in the result": [
        f"{TOPOLOGY}::test_series_dependencies_multiply_and_say_what_they_assume",
        f"{PATHS}::test_declared_alternatives_with_their_own_dependencies_are_parallel",
    ],
    "partial coverage is explicit": [
        f"{TOPOLOGY}::test_a_path_is_unknown_while_any_required_component_is",
        f"{API}::test_a_partial_result_when_inputs_are_missing",
    ],
    "no unsupported global availability is presented as authoritative": [
        f"{PATHS}::test_each_entry_has_its_own_path_and_there_is_no_architecture_wide_figure",
        f"{PATHS}::test_alternatives_that_overlap_in_part_are_not_composed",
    ],
    # Phase 6: single points of failure and findings.
    "findings are reproducible": [
        f"{FINDINGS}::test_findings_are_reproducible",
        f"{HARDENING}::test_repeated_and_reordered_runs_are_identical",
    ],
    "findings include stable evidence references": [
        f"{FINDINGS}::test_a_single_point_lists_everything_its_failure_can_reach",
        f"{DOMAIN}::test_finding_ids_are_stable_and_ordering_is_by_severity",
    ],
    "severity follows existing conventions": [
        f"{TOPOLOGY}::test_how_sure_a_single_point_of_failure_is",
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
    ],
    "no unsupported certainty or risk score is introduced": [
        f"{FINDINGS}::test_findings_carry_no_score",
        f"{TOPOLOGY}::test_declared_facts_make_a_finding_modeled",
    ],
    # Phase 7: objectives and requirement traceability.
    "requirement links are stable": [
        f"{OBJECTIVES}::test_machine_checkable_requirements_become_objectives_with_their_ids",
        f"{SERVICE}::test_in_force_requirements_become_objectives_and_are_recorded",
    ],
    "missing evidence results in cannot be evaluated, not success": [
        f"{OBJECTIVES}::test_missing_evidence_is_never_success",
        f"{OBJECTIVES}::test_availability_on_paths_satisfied_violated_or_unknown",
    ],
    "no natural-language interpretation is presented as proof": [
        f"{OBJECTIVES}::test_requirements_that_cannot_be_checked_are_never_passed"
    ],
    # Phase 8: API, persistence and authorization.
    "API schemas are typed and documented": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
    "OpenAPI remains accurate": [
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{API}::test_the_model_catalog",
    ],
    "authorization is checked on every operation": [
        f"{API}::test_access_and_listing",
        f"{SERVICE}::test_access",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "persistence is transactional where required": [
        f"{SERVICE}::test_an_archive_during_the_calculation_refuses_the_store",
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{API}::test_analyses_are_append_only_and_reads_cost_the_same",
        f"{I}.database.test_migrations::test_downgrading_reliability_leaves_cost_intact",
    ],
    "existing frontend and API contracts are not broken": [
        f"{S}.test_traceability_cost_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_capacity_engine::test_every_criterion_is_mapped",
        f"{S}.test_documentation::test_the_decisions_are_recorded",
    ],
    # Phase 9: testing and quality.
    "unit tests cover formulas, invalid values, missing inputs and traces": [
        f"{MODELS}::test_zero_and_undefined_denominators",
        f"{HARDENING}::test_availability_at_the_edges",
        f"{HARDENING}::test_perfect_replicas_combine_to_one",
    ],
    "topology tests cover series, alternatives, redundancy, domains, cycles and partial analysis": [
        f"{PATHS}::test_both_alternatives_needed_is_their_product",
        f"{PATHS}::test_every_member_must_declare_independence_automatic_failover_and_one_minimum",
        f"{TOPOLOGY}::test_redundancy_groups",
        f"{TOPOLOGY}::test_disconnected_components_are_on_no_path",
    ],
    "integration tests cover revisions, isolation, errors and schemas": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{API}::test_access_and_listing",
        f"{API}::test_an_engine_failure_is_stored_as_failed",
    ],
    "determinism covers ordering, identifiers, traces and precision": [
        f"{HARDENING}::test_repeated_and_reordered_runs_are_identical",
        f"{OBJECTIVES}::test_objectives_are_deterministic",
        f"{SERVICE}::test_the_same_inputs_give_the_same_result",
    ],
    "regressions: the IR and other engines are unchanged": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_architecture_crud::test_every_criterion_is_mapped",
    ],
    "no silent exception swallowing": [
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_analysis_without_internals",
    ],
    "no fabricated data": [
        f"{HERE}::test_no_reliability_figures_are_shipped",
        f"{ENGINE}::test_an_unknown_value_is_as_missing_as_an_absent_one",
    ],
    "no unbounded traversal or uncontrolled computation": [
        f"{HARDENING}::test_a_chain_through_every_node_completes",
        f"{HARDENING}::test_many_entries_reaching_one_large_group_compose_it_once",
        f"{SERVICE}::test_the_engine_runs_off_the_event_loop",
        f"{HERE}::test_the_engine_executes_no_dynamic_code",
    ],
    # Phase 10: documentation and final verification.
    "the engine is documented topic by topic with a partial example": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{S}.test_documentation::test_the_decisions_are_recorded",
    ],
    "no estimate is described as guaranteed uptime": [f"{HERE}::test_no_estimate_is_called_guaranteed"],
}


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_criterion_is_mapped() -> None:
    assert len(ACCEPTANCE) == 48  # 4 + 5 + 4 + 5 + 4 + 4 + 4 + 3 + 5 + 8 + 2 (phases 0-10)


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


def _shipped_ids(registry: Registry) -> list[str]:
    return [m.meta.id for m in registry.models()] + [s.meta.id for s in registry.steps()]


def test_the_engine_reads_the_ir_and_defines_no_graph() -> None:
    """Components and connections come from the IR (and its Topology index); no second graph."""
    graph_names = {"Graph", "Node", "Edge", "Connection", "ArchitectureGraph", "Topology", "Component"}
    for package in PACKAGES:
        for path, tree in _python(package):
            classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            assert not classes & graph_names, (path, classes & graph_names)
            assert not any(m in {"networkx", "igraph"} for m in _imports(tree)), path


def test_the_orchestrator_is_generic() -> None:
    """engine.py names no model, step or reliability property: what to estimate lives in the
    models and steps, how to run them in the orchestrator."""
    from engines.reliability.registry import default_registry  # noqa: PLC0415 - the shipped models

    text = (ROOT / "engines" / "reliability" / "engine.py").read_text()
    for shipped in _shipped_ids(default_registry()):
        if shipped != "objectives":  # also the name of what a step produces
            assert f'"{shipped}"' not in text, shipped
    for name in ("mtbf_seconds", "mttr_seconds", "replicas", "redundancy_group", "failover_mode"):
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


def test_no_reliability_figures_are_shipped() -> None:
    """Every availability, MTBF, MTTR and failover figure is declared on the architecture: none is
    written into the engine, the domain or the knowledge base, and the reliability placeholders
    elsewhere stay empty."""
    figure = re.compile(r"""Decimal\(\s*["']0\.9""")
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            assert not figure.search(path.read_text()), path
    for path in sorted((ROOT / "knowledge").rglob("*")):
        if path.is_file():
            words = set(re.split(r"[\s:]+", path.read_text().lower()))
            assert not {"mtbf", "mttr", "mtbf_seconds", "mttr_seconds", "sla"} & words, path
    for placeholder in (
        "engines/validation/rules/reliability.py",
        "engines/validation/rules/availability.py",
        "ai/agents/reliability_agent.py",
    ):
        assert (ROOT / placeholder).read_text() == "", placeholder


def test_every_documentation_topic_is_covered() -> None:
    """Phase 10's documentation list (10 topics), section by section, a partial example, and every
    shipped model and step."""
    text = DOC.read_text()
    for heading in (
        "## Purpose and scope",
        "## Supported models and formulas",
        "## Input fields and units",
        "## Assumptions and limitations",
        "## Unknown and unsupported behavior",
        "## API contracts",
        "## Example analysis",
        "## Example: a partial result",
        "## Interpreting findings",
        "## Adding a model",
        "## Tests",
        "## Limits and performance",
        "## Persistence",
        "## Authorization",
    ):
        assert heading in text, heading
    partial = text.split("## Example: a partial result", 1)[1].split("\n## ", 1)[0]
    assert '"status": "partial"' in partial
    assert '"quantity": null' in partial
    assert "not_verifiable" in partial
    from engines.reliability.registry import default_registry  # noqa: PLC0415 - the shipped models

    for shipped in _shipped_ids(default_registry()):
        assert f"`{shipped}`" in text, shipped


def test_no_estimate_is_called_guaranteed() -> None:
    """The documents say estimates are not guaranteed uptime, and never call one guaranteed."""
    docs = ROOT / "docs"
    for path in (
        DOC,
        docs / "api" / "reliability.md",
        docs / "frontend" / "reliability-contract.md",
        docs / "adr" / "ADR-014-deterministic-reliability.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(r"[^.]*guaranteed[^.]*", text, flags=re.IGNORECASE):
            assert re.search(r"\b(not|never|no)\b", sentence, flags=re.IGNORECASE), (path, sentence)
    assert "not guaranteed uptime" in DOC.read_text()


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Assumptions and limitations"):
        assert heading in text, heading
    assert "Reused:" in text
