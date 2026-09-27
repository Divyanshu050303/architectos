"""Milestone 12 (simulation engine): each acceptance criterion of phases 0-12 mapped to the tests that
prove it, plus structural guarantees (one architecture model, a generic orchestrator, no dynamic code,
no score or likelihood, no randomness, documentation of every topic with the required examples, and
no claim that a projection is guaranteed). Fails if a mapped test is renamed or removed, or a
criterion is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

U = "tests.unit.simulation"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
DOMAIN = f"{U}.test_simulation_domain"
SCENARIOS = f"{U}.test_simulation_scenarios"
OVERLAY = f"{U}.test_simulation_overlay"
ENGINE = f"{U}.test_simulation_engine"
CAPACITY = f"{U}.test_simulation_capacity"
FAILURE = f"{U}.test_simulation_failure"
COST = f"{U}.test_simulation_cost"
COMPARISON = f"{U}.test_simulation_comparison"
LIMITS = f"{U}.test_simulation_limits"
SERVICE = f"{U}.test_simulation_service"
FIXTURES = f"{U}.test_simulation_fixtures"
API = f"{I}.api.test_simulations"
MIGRATIONS = f"{I}.database.test_migrations"
HERE = f"{S}.test_traceability_simulation_engine"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "simulation-engine.md"
PACKAGES = ("engines/simulation", "core/domain/simulations")

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: repository audit.
    "existing simulation files and their status were inspected": [
        f"{HERE}::test_the_audit_and_review_are_recorded"
    ],
    "existing engines and IR commands are reused, not duplicated": [
        f"{HERE}::test_the_engine_reads_the_ir_and_defines_no_graph",
        f"{CAPACITY}::test_outputs_are_traceable_to_the_capacity_engines_own_results",
    ],
    "no competing architecture model is created": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{HERE}::test_the_engine_reads_the_ir_and_defines_no_graph",
    ],
    # Phase 1: simulation domain contract.
    "typed, validated models": [
        f"{DOMAIN}::test_the_request_is_canonical_and_bounded",
        f"{DOMAIN}::test_a_scenario_is_canonical_and_refuses_conflicts",
        f"{DOMAIN}::test_configuration_changes_are_checked_against_the_ir_property",
    ],
    "stable identifiers": [
        f"{DOMAIN}::test_results_are_ordered_counted_fingerprinted_and_round_trip",
        f"{SERVICE}::test_the_same_inputs_give_the_same_result",
    ],
    "explicit units and scenario semantics": [
        f"{DOMAIN}::test_units_are_explicit_and_never_guessed",
        f"{DOMAIN}::test_deltas_have_units_and_never_invent_a_difference",
    ],
    "unknown and unsupported states preserved": [
        f"{DOMAIN}::test_impacts_and_runs_keep_unknown_and_unsupported_explicit",
        f"{DOMAIN}::test_the_status_follows_what_was_established",
    ],
    "results serializable deterministically": [
        f"{DOMAIN}::test_results_are_ordered_counted_fingerprinted_and_round_trip",
        f"{FIXTURES}::test_outputs_are_canonically_ordered_and_exact",
    ],
    # Phase 2: scenario contract and validation.
    "scenario validation is deterministic": [
        f"{SCENARIOS}::test_a_valid_scenario_is_typed_part_by_part",
        f"{SCENARIOS}::test_validation_changes_nothing",
    ],
    "errors identify invalid fields and references": [
        f"{SCENARIOS}::test_invalid_scenarios_are_refused_with_the_field_and_element",
        f"{SCENARIOS}::test_entries_must_exist",
        f"{FIXTURES}::test_fixture_10_a_scenario_referencing_a_nonexistent_component_is_refused",
    ],
    "scenario behavior is versioned and documented": [
        f"{SCENARIOS}::test_every_type_declares_its_contract",
        f"{SCENARIOS}::test_configuration_types_partition_the_simulated_properties",
        f"{SCENARIOS}::test_the_cost_properties_are_what_the_cost_engine_maps",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    "invalid scenarios do not trigger expensive execution": [
        f"{ENGINE}::test_an_invalid_scenario_is_refused_before_any_evaluator_runs",
        f"{LIMITS}::test_over_limit_scenarios_are_refused_before_anything_runs",
    ],
    # Phase 3: immutable scenario overlay.
    "the original architecture remains unchanged": [
        f"{OVERLAY}::test_changes_apply_to_a_copy_and_the_revision_is_never_modified",
        f"{FIXTURES}::test_the_revision_is_never_modified",
    ],
    "the scenario overlay is deterministic": [
        f"{OVERLAY}::test_the_overlay_is_deterministic_serializable_and_reconstructible",
        f"{FIXTURES}::test_identical_inputs_give_identical_outputs",
    ],
    "the overlay can be serialized and reconstructed": [
        f"{OVERLAY}::test_the_overlay_is_deterministic_serializable_and_reconstructible"
    ],
    "invalid references are rejected": [
        f"{OVERLAY}::test_a_change_that_breaks_an_invariant_is_refused",
        f"{SCENARIOS}::test_invalid_scenarios_are_refused_with_the_field_and_element",
    ],
    "baseline and scenario revisions are clearly distinguished": [
        f"{OVERLAY}::test_changed_values_are_explicit_and_carry_the_scenarios_provenance",
        f"{OVERLAY}::test_failures_mark_elements_unavailable_without_removing_them",
        f"{API}::test_an_exact_revision_is_simulated",
    ],
    # Phase 4: simulation orchestrator.
    "the execution flow is explicit and testable": [
        f"{ENGINE}::test_evaluators_see_the_earlier_evaluations_in_order",
        f"{ENGINE}::test_outcomes_are_collected_with_their_trace_and_versions",
        f"{HERE}::test_the_orchestrator_is_generic",
    ],
    "failures are isolated and reported": [
        f"{ENGINE}::test_failures_are_isolated_and_reported",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_simulation_without_internals",
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
    ],
    "identical inputs produce identical normalized results": [
        f"{ENGINE}::test_the_same_inputs_give_the_same_result_and_the_revision_is_untouched",
        f"{FIXTURES}::test_identical_inputs_give_identical_outputs",
    ],
    "the source architecture is never mutated": [
        f"{ENGINE}::test_the_same_inputs_give_the_same_result_and_the_revision_is_untouched",
        f"{SERVICE}::test_the_architecture_is_never_changed",
    ],
    # Phase 5: workload and capacity scenarios.
    "capacity outputs are traceable to the Capacity Engine": [
        f"{CAPACITY}::test_outputs_are_traceable_to_the_capacity_engines_own_results",
        f"{CAPACITY}::test_fixture_1_workload_increase_with_supported_inputs",
    ],
    "scenario and baseline use compatible model versions": [
        f"{CAPACITY}::test_outputs_are_traceable_to_the_capacity_engines_own_results",
        f"{COST}::test_capacity_and_cost_read_one_capacity_run",
    ],
    "unsupported capacity calculations are explicit": [
        f"{CAPACITY}::test_what_capacity_cannot_evaluate_is_reported",
        f"{CAPACITY}::test_unknown_demand_is_never_zero",
        f"{FIXTURES}::test_fixture_9_a_partial_result_names_the_components_that_were_not_calculated",
    ],
    "no duplicate capacity formulas are introduced": [
        f"{CAPACITY}::test_no_linear_scaling_where_the_model_does_not_state_it",
        f"{HERE}::test_no_formula_is_duplicated_from_the_engines",
    ],
    # Phase 6: failure and recovery scenarios.
    "failure scenarios are grounded in explicit architecture semantics": [
        f"{FAILURE}::test_fixture_4_a_component_failure_with_explicit_dependency_semantics",
        f"{FAILURE}::test_declared_automatic_alternatives_tolerate_a_failure",
        f"{FAILURE}::test_a_zone_failure_uses_declared_placement_and_unknown_stays_unknown",
    ],
    "reliability outputs are traceable to the Reliability Engine": [
        f"{FAILURE}::test_resilience_changes_are_evaluated_by_the_reliability_engine",
        f"{FAILURE}::test_a_connection_failure_follows_the_routes_that_remain",
    ],
    "unknown recovery behavior remains unknown": [
        f"{FAILURE}::test_fixture_5_a_failure_with_unknown_failover_behavior",
        f"{FAILURE}::test_manual_failover_or_too_few_alternatives_interrupt",
    ],
    "no real-world outage prediction is claimed": [
        f"{FAILURE}::test_nothing_is_modified_and_the_result_is_deterministic",
        f"{HERE}::test_no_score_likelihood_or_prediction_in_the_results",
        f"{HERE}::test_nothing_is_claimed_guaranteed",
    ],
    # Phase 7: cost and resource comparison.
    "comparisons use compatible inputs and model versions": [
        f"{COST}::test_a_workload_increase_is_priced_with_the_same_snapshot",
        f"{COST}::test_capacity_and_cost_read_one_capacity_run",
    ],
    "missing pricing is visible": [
        f"{COST}::test_a_missing_price_stays_visible",
        f"{COST}::test_cost_needs_pricing_inputs_and_a_currency",
        f"{COST}::test_without_a_workload_usage_is_unknown_and_the_total_is_not_compared",
    ],
    "cost differences are reproducible": [
        f"{COST}::test_a_cost_simulation_is_deterministic",
        f"{COST}::test_a_replica_change_is_priced",
    ],
    "results are labeled as estimates, not invoices": [
        f"{COST}::test_a_workload_increase_is_priced_with_the_same_snapshot",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    # Phase 8: baseline comparison and result explanation.
    "comparison is deterministic": [
        f"{COMPARISON}::test_the_comparison_is_deterministic_and_states_no_cause",
        f"{FIXTURES}::test_fixtures_7_and_8_compare_only_compatible_models",
    ],
    "incompatible metrics are marked non-comparable": [
        f"{COMPARISON}::test_fixture_8_incompatible_model_versions_are_not_compared",
        f"{COMPARISON}::test_a_different_engine_baseline_or_missing_run_is_not_compared",
        f"{COMPARISON}::test_a_unit_mismatch_is_never_compared",
        f"{API}::test_simulations_are_compared_on_a_common_baseline",
    ],
    "every difference has units and provenance": [
        f"{COMPARISON}::test_fixture_7_scenarios_on_one_baseline_with_compatible_models",
        f"{DOMAIN}::test_deltas_have_units_and_never_invent_a_difference",
    ],
    "no unsupported causal explanation is generated": [
        f"{COMPARISON}::test_the_comparison_is_deterministic_and_states_no_cause"
    ],
    # Phase 9: execution safety and resource limits.
    "resource limits are configurable and documented": [
        f"{LIMITS}::test_limits_are_configurable_within_hard_caps",
        f"{API}::test_the_catalog",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    "over-limit requests fail predictably": [
        f"{LIMITS}::test_over_limit_scenarios_are_refused_before_anything_runs",
        f"{LIMITS}::test_the_output_is_cut_in_canonical_order_and_the_cut_is_stated",
        f"{LIMITS}::test_malformed_or_adversarial_inputs_are_refused",
        f"{API}::test_execution_limits_are_enforced_over_the_api",
    ],
    "no unbounded loops or graph traversal": [
        f"{LIMITS}::test_the_largest_architecture_is_evaluated_in_bounded_time",
        f"{LIMITS}::test_an_extreme_but_valid_workload_is_handled_not_crashed",
    ],
    "no unnecessary infrastructure dependencies": [
        f"{FIXTURES}::test_the_engine_reaches_no_network_storage_or_randomness",
        f"{SERVICE}::test_the_engine_runs_off_the_event_loop",
    ],
    # Phase 10: API, persistence and authorization.
    "every operation enforces authorization": [
        f"{SERVICE}::test_access",
        f"{API}::test_access_and_listing",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
    ],
    "API contracts are typed and documented": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
    ],
    "persistence is transactional where required": [
        f"{SERVICE}::test_an_archive_during_the_calculation_refuses_the_store",
        f"{API}::test_simulations_are_append_only_and_reads_cost_the_same",
        f"{MIGRATIONS}::test_downgrading_simulations_leaves_observability_intact",
        f"{S}.test_audit_sweep::test_every_project_scoped_audit_action_is_exercised",
    ],
    "existing frontend and API contracts remain compatible": [
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{HERE}::test_the_frontend_contract_is_documented",
    ],
    # Phase 11: testing and quality.
    "the ten scenario fixtures": [
        f"{CAPACITY}::test_fixture_1_workload_increase_with_supported_inputs",
        f"{CAPACITY}::test_fixture_2_a_workload_increase_without_workload_parameters",
        f"{CAPACITY}::test_fixture_3_a_replica_change_follows_the_declared_model",
        f"{FAILURE}::test_fixture_4_a_component_failure_with_explicit_dependency_semantics",
        f"{FAILURE}::test_fixture_5_a_failure_with_unknown_failover_behavior",
        f"{CAPACITY}::test_fixture_6_a_resource_configuration_change",
        f"{COMPARISON}::test_fixture_7_scenarios_on_one_baseline_with_compatible_models",
        f"{COMPARISON}::test_fixture_8_incompatible_model_versions_are_not_compared",
        f"{FIXTURES}::test_fixture_9_a_partial_result_names_the_components_that_were_not_calculated",
        f"{FIXTURES}::test_fixture_10_a_scenario_referencing_a_nonexistent_component_is_refused",
        f"{FIXTURES}::test_each_fixture_gives_its_status_and_runs",
    ],
    "exact revisions run through the orchestrator, stored and read back": [
        f"{API}::test_an_exact_revision_is_simulated",
        f"{API}::test_a_simulation_is_run_stored_and_read_back",
        f"{SERVICE}::test_a_simulation_is_stored_with_its_inputs_and_audited",
    ],
    "capacity, reliability and cost integrations": [
        f"{API}::test_a_simulation_is_run_stored_and_read_back",
        f"{API}::test_a_failure_scenario",
        f"{API}::test_missing_inputs_are_unsupported_not_estimated",
    ],
    "tenant isolation, unauthorized access, invalid architecture or revision, safe errors": [
        f"{API}::test_access_and_listing",
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{API}::test_an_engine_failure_is_stored_as_failed",
        f"{SERVICE}::test_invalid_requests_store_nothing",
    ],
    "determinism: overlay, structure, ordering, numbers, comparison, traces": [
        f"{FIXTURES}::test_identical_inputs_give_identical_outputs",
        f"{FIXTURES}::test_outputs_are_canonically_ordered_and_exact",
        f"{FIXTURES}::test_fixtures_7_and_8_compare_only_compatible_models",
    ],
    "the architecture IR remains unchanged": [
        f"{FIXTURES}::test_the_revision_is_never_modified",
        f"{FIXTURES}::test_failures_mark_rather_than_remove",
    ],
    "the other engines' contracts remain compatible": [
        f"{S}.test_traceability_capacity_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_reliability_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_cost_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
    ],
    "no silent exception swallowing, no fabricated data, no unbounded execution": [
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{ENGINE}::test_missing_inputs_leave_the_analysis_unsupported_not_zero",
        f"{LIMITS}::test_the_largest_architecture_is_evaluated_in_bounded_time",
        f"{HERE}::test_the_engine_executes_no_dynamic_code",
    ],
    # Phase 12: documentation.
    "every documentation topic, with the four required examples": [
        f"{HERE}::test_every_documentation_topic_is_covered"
    ],
    "results are stated as model-based projections, never guarantees": [
        f"{HERE}::test_nothing_is_claimed_guaranteed"
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
    assert len(ACCEPTANCE) == 55  # 3 + 5 + 4 + 5 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 8 + 2 (phases 0-12)


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


def test_the_engine_reads_the_ir_and_defines_no_graph() -> None:
    graph_names = {"Graph", "Node", "Edge", "Connection", "ArchitectureGraph", "Topology", "Component"}
    for package in PACKAGES:
        for path, tree in _python(package):
            classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            assert not classes & graph_names, (path, classes & graph_names)
            assert not any(m in {"networkx", "igraph"} for m in _imports(tree)), path


def test_the_orchestrator_is_generic() -> None:
    """engine.py names no analysis, scenario type or IR property: what to evaluate lives in the
    evaluators and the catalog, how to run them in the orchestrator. ("workload" and "pricing" are the
    inputs an evaluator may require.)"""
    from core.domain.simulations.catalog import TYPES  # noqa: PLC0415 - the shipped types

    text = (ROOT / "engines" / "simulation" / "engine.py").read_text()
    for name in ("capacity", "reliability", "cost", "replicas", "availability_zones", "failover_mode"):
        assert f'"{name}"' not in text, name
    for scenario_type in TYPES:
        if scenario_type.id != "workload":
            assert f'"{scenario_type.id}"' not in text, scenario_type.id


def test_no_formula_is_duplicated_from_the_engines() -> None:
    """The evaluators call the engines' ports; they define no utilization, availability or price
    formula of their own (no division of a demand by a capacity, no product of availabilities)."""
    for name in ("capacity.py", "failure.py", "cost.py"):
        tree = ast.parse((ROOT / "engines" / "simulation" / name).read_text())
        imports = _imports(tree)
        assert any(m.startswith("core.domain.") for m in imports), name
        functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for forbidden in ("utilization", "availability", "path_availability", "price", "monthly_cost"):
            assert forbidden not in functions, (name, forbidden)


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


def test_no_score_likelihood_or_prediction_in_the_results() -> None:
    """No field of a simulation result is a score, a probability, a likelihood, an error rate, a
    duration or a timeline."""
    text = (ROOT / "core" / "domain" / "simulations" / "results.py").read_text()
    for word in ("score", "probability", "likelihood", "error_rate", "timeline", "duration", "risk"):
        assert not re.search(rf'"{word}"|\b{word}\s*:', text), word


def test_nothing_is_claimed_guaranteed() -> None:
    """The documents say results are model-based projections and never claim a guarantee."""
    engine = DOC.read_text()
    for statement in (
        "Simulation results are model-based projections.",
        "They do not guarantee real-world performance,\navailability, cost or failure behavior.",
        "No telemetry is read",
        "never zero, never assumed",
    ):
        assert statement in engine, statement
    for path in (
        DOC,
        DOCS / "api" / "simulations.md",
        DOCS / "adr" / "ADR-017-deterministic-simulation.md",
        DOCS / "frontend" / "simulation-contract.md",
    ):
        text = " ".join(path.read_text().split())
        assert "guarantee" in text, path.name
        for sentence in re.findall(
            r"[^.]*\b(?:proves?|guarantee[sd]?|verified|will fail)\b[^.]*", text, re.I
        ):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Phase 12's documentation list (14 topics) and the four required examples."""
    text = DOC.read_text()
    for heading in (
        "## Purpose and scope",
        "## Supported scenario types",
        "## Scenario input contracts and units",
        "## Overlay semantics and immutability",
        "## Engine integrations",
        "## Model versions and assumptions",
        "## Execution limits",
        "## Partial and unsupported results",
        "## Baseline comparison semantics",
        "## API contracts",
        "## Example scenario and result",
        "## Adding a new scenario type safely",
        "## Tests",
        "## Known limitations",
        "## Example: missing inputs",
        "## Example: an incompatible baseline cannot be compared",
        "## Example: the architecture is unchanged",
        "## Persistence",
        "## Authorization",
        "## Determinism",
    ):
        assert heading in text, heading

    def section(title: str) -> str:
        return text.split(title, 1)[1].split("\n## ", 1)[0]

    assert "resultFingerprint" in section("## Example scenario and result")
    assert "status: completed" in section("## Example scenario and result")
    assert "no_workload" in section("## Example: missing inputs")
    assert "partial" in section("## Example: missing inputs")
    assert "different_baseline" in section("## Example: an incompatible baseline cannot be compared")
    assert "content hash" in section("## Example: the architecture is unchanged")
    assert "not an invoice" in text
    from core.domain.simulations.catalog import TYPES  # noqa: PLC0415 - the shipped types
    from core.domain.simulations.limits import DEFAULT_LIMITS  # noqa: PLC0415

    for scenario_type in TYPES:
        assert f"`{scenario_type.id}`" in text, scenario_type.id
    for name in DEFAULT_LIMITS.to_dict():
        assert f"`{name}`" in text, name


def test_the_frontend_contract_is_documented() -> None:
    text = (DOCS / "frontend" / "simulation-contract.md").read_text()
    for field in ("durationMinutes", "errorRate", "timeline", "cascadingFailure", "impact", "deltas"):
        assert field in text, field


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known limitations"):
        assert heading in text, heading
    assert "Reused" in text
    assert "Still empty placeholders" in text
