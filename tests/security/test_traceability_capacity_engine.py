"""Milestone 7 (deterministic capacity engine): each acceptance criterion of phases 0-9 mapped to
the tests that prove it, plus structural guarantees (one architecture model, a generic
orchestrator, no dynamic code, no simulation machinery, documentation of every topic). Fails if a
mapped test is renamed or removed, or a criterion is unmapped."""

import ast
import importlib
from pathlib import Path

import pytest

U = "tests.unit.capacity"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
UNITS = f"{U}.test_capacity_units"
WORKLOAD = f"{U}.test_workload_profile"
RESULTS = f"{U}.test_capacity_results"
ENGINE = f"{U}.test_capacity_engine"
TRAFFIC = f"{U}.test_demand_propagation"
MODELS = f"{U}.test_capacity_models"
HEADROOM = f"{U}.test_headroom"
SCENARIOS = f"{U}.test_scenarios"
SERVICE = f"{U}.test_capacity_service"
NUMERICS = f"{U}.test_capacity_numerics"
API = f"{I}.api.test_capacity"
HERE = f"{S}.test_traceability_capacity_engine"

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "architecture" / "capacity-engine.md"

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: audit and capacity model design.
    "existing capacity code has been inspected": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    "existing component and IR contracts are understood": [
        f"{S}.test_traceability_architecture_ir::test_every_item_of_the_spec_is_mapped",
        f"{HERE}::test_the_capacity_engine_reads_the_ir_and_defines_no_graph",
    ],
    "no duplicate architecture or component model is proposed": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{HERE}::test_the_capacity_engine_reads_the_ir_and_defines_no_graph",
    ],
    "no code changed during the audit": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    "phase 1 scope is clear": [f"{HERE}::test_every_documentation_topic_is_covered"],
    # Phase 1: domain model and workload profile.
    "workload inputs are typed and unit-aware": [
        f"{WORKLOAD}::test_a_request_response_profile",
        f"{WORKLOAD}::test_rates_in_any_unit_of_the_dimension",
        f"{UNITS}::test_canonical_values_are_exact",
    ],
    "analysis results have explicit status and provenance": [
        f"{RESULTS}::test_the_status_says_what_was_established",
        f"{RESULTS}::test_the_lifecycle_ends_in_what_the_result_established",
        f"{MODELS}::test_a_declared_total_throughput",
    ],
    "invalid numeric inputs are rejected": [
        f"{UNITS}::test_invalid_quantities_are_refused",
        f"{WORKLOAD}::test_invalid_profiles_are_refused",
        f"{NUMERICS}::test_invalid_numbers_are_refused",
    ],
    "missing data is represented honestly": [
        f"{RESULTS}::test_an_unknown_value_has_no_number_and_names_what_is_missing",
        f"{RESULTS}::test_components_state_why_they_have_no_estimate",
    ],
    "tests cover valid and invalid requests": [
        f"{RESULTS}::test_a_request_names_the_exact_revision_and_its_inputs",
        f"{WORKLOAD}::test_from_dict_is_strict",
        f"{RESULTS}::test_results_round_trip_exactly",
    ],
    # Phase 2: model registry and calculation framework.
    "new capacity models can be added independently": [
        f"{ENGINE}::test_registration_resolution_and_duplicates",
        f"{ENGINE}::test_models_run_on_the_nodes_they_support_with_their_parameters",
    ],
    "the orchestrator contains no component-specific calculation logic": [
        f"{HERE}::test_the_orchestrator_is_generic",
        f"{ENGINE}::test_demand_from_the_propagation_step_reaches_the_models",
    ],
    "unsupported cases are explicit": [
        f"{ENGINE}::test_a_node_without_a_model_is_unsupported_not_zero",
        f"{ENGINE}::test_missing_workload_fields_and_assumptions_are_named",
        f"{ENGINE}::test_a_failing_model_is_recorded_and_the_others_still_count",
        f"{ENGINE}::test_an_empty_architecture_says_so",
    ],
    "framework results are deterministic for identical inputs": [
        f"{ENGINE}::test_results_are_deterministic_whatever_the_registration_order",
        f"{ENGINE}::test_the_context_fingerprint_follows_the_inputs",
    ],
    # Phase 3: demand propagation.
    "demand propagation follows explicit architecture semantics": [
        f"{TRAFFIC}::test_a_linear_path_carries_declared_shares",
        f"{TRAFFIC}::test_branching_distributes_by_declared_ratios",
        f"{TRAFFIC}::test_event_streams_flow_from_broker_to_consumers",
        f"{TRAFFIC}::test_dependencies_and_replication_carry_no_workload",
    ],
    "traffic multipliers are traceable": [
        f"{TRAFFIC}::test_fan_out_and_aggregation_add_up",
        f"{TRAFFIC}::test_read_write_split_and_cache_hits",
    ],
    "unsupported topology is not silently guessed": [
        f"{TRAFFIC}::test_missing_routing_is_reported_not_assumed",
        f"{TRAFFIC}::test_cycles_are_unsupported_not_guessed",
        f"{TRAFFIC}::test_traffic_from_outside_the_workload_makes_downstream_incomplete",
    ],
    "propagation is deterministic and unit-consistent": [
        f"{TRAFFIC}::test_propagation_is_deterministic",
        f"{TRAFFIC}::test_units_follow_the_connection_kind",
        f"{TRAFFIC}::test_work_in_different_units_is_never_summed",
    ],
    # Phase 4: component capacity and resource estimation.
    "component capacity estimates are model-backed": [
        f"{MODELS}::test_per_replica_throughput_scales_linearly_and_says_so",
        f"{MODELS}::test_models_are_versioned_and_deterministic",
    ],
    "resource assumptions are explicit": [
        f"{MODELS}::test_cpu_demand_from_a_declared_cost_per_request",
        f"{MODELS}::test_connection_pools_against_the_declared_maximum",
        f"{MODELS}::test_storage_growth_and_fill_time_from_empty",
    ],
    "unknown values remain unknown": [
        f"{MODELS}::test_without_declared_throughput_there_is_no_throughput_limit",
        f"{MODELS}::test_cpu_limit_is_unknown_without_declared_cores",
        f"{MODELS}::test_incomplete_demand_never_becomes_a_number",
        f"{MODELS}::test_components_without_declared_inputs_are_insufficient_and_nothing_is_zero",
    ],
    "unit conversions are tested": [
        f"{UNITS}::test_conversions_round_trip_and_never_cross_dimensions",
        f"{MODELS}::test_the_work_unit_follows_the_demand",
        f"{MODELS}::test_retention_bounds_what_a_queue_keeps",
    ],
    "no unsupported performance claims are generated": [
        f"{MODELS}::test_storage_needs_reads_and_writes_told_apart",
        f"{MODELS}::test_bandwidth_needs_response_sizes_for_requests",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    # Phase 5: bottlenecks, utilization and headroom.
    "utilization and headroom are mathematically correct": [
        f"{RESULTS}::test_utilization_and_headroom",
        f"{HEADROOM}::test_demand_against_declared_capacity",
        f"{HEADROOM}::test_a_target_utilization_flags_what_is_above_it",
    ],
    "unknown values are not misrepresented": [
        f"{HEADROOM}::test_incomplete_demand_is_never_compared",
        f"{HEADROOM}::test_the_saturation_multiple_is_incomplete_when_a_capacity_is_unknown",
    ],
    "bottleneck findings are evidence-backed": [
        f"{HEADROOM}::test_the_lowest_known_limit_binds",
        f"{HEADROOM}::test_other_resources_are_paired_by_name_and_unit",
    ],
    "definite limits are distinguished from uncertain candidates": [
        f"{RESULTS}::test_a_bottleneck_is_modeled_only_with_both_sides_known",
        f"{HEADROOM}::test_unknown_capacity_on_a_waited_path_is_a_candidate_not_a_verdict",
        f"{HEADROOM}::test_multiple_candidates_are_ordered_modeled_first_then_worst",
    ],
    # Phase 6: scenarios.
    "scenario assumptions are explicit": [
        f"{SCENARIOS}::test_growth_scales_demand_and_finds_what_breaks",
        f"{SCENARIOS}::test_a_resource_increase_is_an_explicit_change",
        f"{SCENARIOS}::test_invalid_scenarios_are_refused",
    ],
    "unsupported scaling behavior is not guessed": [
        f"{SCENARIOS}::test_a_declared_total_does_not_scale_by_itself",
        f"{NUMERICS}::test_a_scaling_option_too_large_to_state_is_unsupported",
    ],
    "baseline and scenario results are comparable": [
        f"{SCENARIOS}::test_a_neutral_scenario_equals_the_baseline",
        f"{SCENARIOS}::test_reduced_workload_resolves_bottlenecks",
    ],
    "scenarios do not become a general-purpose simulation engine": [
        f"{HERE}::test_scenarios_are_recalculations_not_simulations",
        f"{SCENARIOS}::test_scenarios_are_deterministic_and_leave_the_baseline_untouched",
    ],
    # Phase 7: API and persistence.
    "capacity analysis is accessible through the API": [
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
        f"{API}::test_components_bottlenecks_and_scenarios",
        f"{API}::test_the_model_catalog",
    ],
    "results are persisted and retrievable": [
        f"{API}::test_analyses_are_append_only_and_reads_cost_the_same",
        f"{SERVICE}::test_reading_lists_components_and_bottlenecks",
        f"{I}.database.test_migrations::test_downgrading_capacity_leaves_validation_intact",
    ],
    "authorization is enforced": [
        f"{API}::test_access_and_listing",
        f"{SERVICE}::test_access",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "OpenAPI documentation is updated": [
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
    "API and repository tests pass, including failures": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_analysis_without_internals",
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{SERVICE}::test_an_archive_during_the_calculation_refuses_the_store",
    ],
    # Phase 8: determinism, performance and security hardening.
    "determinism is demonstrated by tests": [
        f"{SERVICE}::test_the_same_inputs_give_the_same_result",
        f"{NUMERICS}::test_the_largest_architecture_is_analyzed_completely_and_deterministically",
    ],
    "numerical edge cases are covered": [
        f"{NUMERICS}::test_tiny_and_huge_rates_keep_exact_ratios",
        f"{NUMERICS}::test_a_ratio_is_never_clamped_even_when_enormous",
        f"{NUMERICS}::test_zero_capacity_and_zero_demand",
        f"{NUMERICS}::test_values_beyond_what_a_quantity_holds_become_unknown_not_errors",
        f"{NUMERICS}::test_rounding_is_half_even_at_nine_places_and_only_for_presentation_of_quantities",
        f"{TRAFFIC}::test_demand_beyond_a_quantity_is_reported_not_raised",
    ],
    "performance characteristics and limits are documented": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{API}::test_analyses_are_append_only_and_reads_cost_the_same",
    ],
    "security tests pass": [
        f"{S}.test_mass_assignment_sweep::test_every_body_rejects_undeclared_privileged_fields",
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
        f"{HERE}::test_models_execute_no_dynamic_code",
    ],
    "existing functionality remains compatible": [
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_architecture_crud::test_every_criterion_is_mapped",
        f"{S}.test_traceability_requirements_engine::test_every_item_of_the_spec_is_mapped",
    ],
    # Phase 9: documentation.
    "the engine is documented without overstating accuracy": [
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
    assert len(ACCEPTANCE) == 42  # 5 + 5 + 4 + 4 + 5 + 4 + 4 + 5 + 5 + 1 (phases 0-9)


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


def test_the_capacity_engine_reads_the_ir_and_defines_no_graph() -> None:
    """Topology comes from the IR (its Topology index); no second graph or node model."""
    graph_names = {"Graph", "Node", "Edge", "Connection", "ArchitectureGraph", "Topology"}
    for package in ("engines/capacity", "core/domain/capacity"):
        for path, tree in _python(package):
            classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            assert not classes & graph_names, (path, classes & graph_names)
            assert not any(m in {"networkx", "igraph"} for m in _imports(tree)), path


def test_the_orchestrator_is_generic() -> None:
    """engine.py names no resource and no model: component logic lives in the models."""
    text = (ROOT / "engines" / "capacity" / "engine.py").read_text()
    names = (
        "work_rate",
        "cpu",
        "connections",
        "storage",
        "bandwidth",
        "declared-throughput",
        "replica-throughput",
    )
    for name in names:
        assert f'"{name}"' not in text, name


def test_models_execute_no_dynamic_code() -> None:
    builtins = {"eval", "exec", "compile", "__import__"}
    for path, tree in _python("engines/capacity"):
        calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert not [f.id for f in calls if isinstance(f, ast.Name) and f.id in builtins], path
        assert not [f.attr for f in calls if isinstance(f, ast.Attribute) and f.attr == "import_module"], path
        assert "importlib" not in _imports(tree), path


def test_scenarios_are_recalculations_not_simulations() -> None:
    """No clock, randomness or event loop in the capacity engine: scenarios rerun the models."""
    for path, tree in _python("engines/capacity"):
        assert not _imports(tree) & {"random", "time", "datetime", "asyncio", "simpy"}, path


def test_every_documentation_topic_is_covered() -> None:
    """Phase 9's documentation list (17 topics), section by section, and every registered model."""
    text = DOC.read_text()
    for heading in (
        "## Responsibilities",
        "## Workload profile",
        "## Supported units",
        "## Capacity model interface",
        "## Registered models",
        "## Input assumptions",  # with the formulas in the models table
        "## Demand propagation",
        "## Evidence, provenance and unknowns",
        "## Bottleneck analysis",
        "## Scenarios",
        "## API contracts",
        "## Persistence",
        "## Authorization",
        "## Limits and performance",
        "## Known limitations",
        "## Adding a model",
        "## Tests",
    ):
        assert heading in text, heading
    assert "not measurements" in text  # the engine does not overstate its accuracy
    assert "Planned, not implemented" in text  # implemented models apart from planned ones
    from engines.capacity.registry import default_registry  # noqa: PLC0415 - the shipped models

    for model in default_registry().models():
        assert f"`{model.meta.id}`" in text, model.meta.id


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known limitations"):
        assert heading in text, heading
