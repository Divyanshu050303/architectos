"""Milestone 8 (deterministic cost engine): each acceptance criterion of phases 0-10 mapped to the
tests that prove it, plus structural guarantees (one architecture model, a generic pricing
framework, no shipped prices, no dynamic code, documentation of every topic). Fails if a mapped
test is renamed or removed, or a criterion is unmapped."""

import ast
import importlib
from pathlib import Path

import pytest

U = "tests.unit.cost"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
MONEY = f"{U}.test_money"
PRICING = f"{U}.test_pricing"
RESULTS = f"{U}.test_cost_results"
LOOKUP = f"{U}.test_pricing_lookup"
SNAPSHOTS = f"{U}.test_pricing_service"
CALCULATOR = f"{U}.test_cost_calculator"
MAPPING = f"{U}.test_cost_mapping"
CAPACITY = f"{U}.test_cost_capacity"
AGGREGATION = f"{U}.test_cost_aggregation"
PROJECTION = f"{U}.test_cost_projection"
SERVICE = f"{U}.test_cost_service"
HARDENING = f"{U}.test_cost_hardening"
API = f"{I}.api.test_cost"
PRICING_API = f"{I}.api.test_pricing"
HERE = f"{S}.test_traceability_cost_engine"

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "architecture" / "cost-engine.md"

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: repository audit and cost model design.
    "existing cost functionality has been inspected": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    "capacity engine integration is understood": [
        f"{CAPACITY}::test_the_basis_keeps_what_cost_needs_of_the_analysis",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    "pricing provenance requirements are documented": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{S}.test_documentation::test_the_decisions_are_recorded",
    ],
    "no duplicate architecture model is proposed": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{HERE}::test_the_cost_engine_reads_the_ir_and_defines_no_graph",
    ],
    "no code changed during the audit": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    "phase 1 scope is clear": [f"{HERE}::test_every_documentation_topic_is_covered"],
    # Phase 1: cost domain model and pricing contracts.
    "pricing and cost contracts are typed": [
        f"{PRICING}::test_records_round_trip_and_are_strict",
        f"{RESULTS}::test_results_are_ordered_fingerprinted_and_round_trip",
    ],
    "units and currencies are explicit": [
        f"{MONEY}::test_currencies_are_never_mixed",
        f"{PRICING}::test_invalid_records_are_refused",
        f"{RESULTS}::test_a_priced_line_is_in_its_prices_currency",
    ],
    "provenance is represented": [
        f"{CALCULATOR}::test_instance_hours_are_priced_exactly",
        f"{CAPACITY}::test_usage_keeps_the_capacity_provenance",
    ],
    "unknown cost is distinct from zero cost": [
        f"{RESULTS}::test_an_unknown_cost_is_never_zero",
        f"{HARDENING}::test_zero_replicas_cost_zero_because_declared_not_because_unknown",
    ],
    "tests cover valid and invalid records": [
        f"{PRICING}::test_invalid_records_are_refused",
        f"{PRICING}::test_negative_quantities_are_refused",
        f"{MONEY}::test_invalid_amounts_and_currencies_are_refused",
        f"{RESULTS}::test_invalid_requests_are_refused",
    ],
    # Phase 2: pricing catalog and snapshot management.
    "pricing lookup is explicit and deterministic": [
        f"{LOOKUP}::test_an_exact_match",
        f"{LOOKUP}::test_the_latest_effective_price_wins_and_ties_are_ambiguous",
        f"{LOOKUP}::test_lookups_are_deterministic_whatever_the_record_order",
        f"{HARDENING}::test_the_index_finds_what_a_full_scan_finds",
    ],
    "historical pricing snapshots remain stable": [
        f"{SNAPSHOTS}::test_a_snapshot_is_created_immutable_and_audited",
        f"{PRICING_API}::test_snapshots_are_append_only",
        f"{PRICING_API}::test_the_same_prices_have_the_same_hash",
    ],
    "missing and stale pricing are visible": [
        f"{LOOKUP}::test_missing_prices_name_what_is_missing",
        f"{LOOKUP}::test_freshness",
        f"{CALCULATOR}::test_a_stale_price_is_used_and_flagged",
    ],
    "no arbitrary pricing substitution occurs": [
        f"{LOOKUP}::test_another_region_is_reported_never_substituted",
        f"{LOOKUP}::test_another_currency_is_reported_never_converted",
        f"{MAPPING}::test_an_unmapped_component_never_borrows_a_price",
    ],
    # Phase 3: cost calculation framework.
    "calculation models are independently extensible": [
        f"{CALCULATOR}::test_duplicate_model_ids_are_refused",
        f"{CALCULATOR}::test_the_registry_lists_models_by_kind_and_versions_them",
        f"{HERE}::test_the_framework_is_generic",
    ],
    "monetary calculations use appropriate decimal precision": [
        f"{CALCULATOR}::test_decimal_precision_is_exact",
        f"{HARDENING}::test_small_fractional_quantities_and_prices_stay_exact",
    ],
    "unit mismatches are rejected or explicitly converted": [
        f"{LOOKUP}::test_the_unit_must_be_the_one_the_calculation_needs",
        f"{MAPPING}::test_a_mapping_to_a_price_of_another_unit_is_refused",
    ],
    "unknown prices remain unknown": [
        f"{CALCULATOR}::test_an_unknown_line_names_what_it_misses_and_is_never_zero",
        f"{CALCULATOR}::test_an_amount_beyond_what_money_holds_is_unknown",
    ],
    "tests verify arithmetic and rounding": [
        f"{CALCULATOR}::test_amounts_are_kept_exact_and_rounded_only_for_display",
        f"{MONEY}::test_amounts_are_exact_and_rounded_only_for_display",
        f"{PRICING}::test_tiers_are_graduated",
    ],
    # Phase 4: architecture resource cost mapping.
    "resource mapping is traceable": [
        f"{MAPPING}::test_a_fully_configured_database_is_priced_resource_by_resource",
        f"{MAPPING}::test_the_instance_class_is_matched_exactly_when_there_is_no_explicit_mapping",
        f"{MAPPING}::test_the_region_of_an_enclosing_boundary_is_used_and_traced",
    ],
    "unsupported components are visible": [
        f"{MAPPING}::test_on_premises_components_are_reported_unsupported",
        f"{CALCULATOR}::test_clients_are_not_billed_and_unmodelled_kinds_are_reported",
    ],
    "missing configuration is explicit": [
        f"{MAPPING}::test_missing_configuration_is_named_never_defaulted",
        f"{MAPPING}::test_missing_storage_leaves_the_instances_priced",
    ],
    "cost calculations use canonical architecture data": [
        f"{MAPPING}::test_an_invalid_mapping_is_refused_by_the_architecture",
        f"{HERE}::test_the_cost_engine_reads_the_ir_and_defines_no_graph",
    ],
    # Phase 5: workload and capacity integration.
    "the cost engine reuses capacity engine outputs": [
        f"{CAPACITY}::test_usage_is_priced_from_the_capacity_analysis",
        f"{CAPACITY}::test_required_replicas_are_billed_only_when_asked",
    ],
    "architecture revision consistency is enforced": [
        f"{CAPACITY}::test_an_analysis_of_another_revision_is_refused",
        f"{API}::test_a_capacity_analysis_of_another_revision_is_refused",
    ],
    "missing usage inputs do not become zero": [
        f"{CAPACITY}::test_without_an_average_rate_usage_is_unknown_and_fixed_costs_remain",
        f"{CAPACITY}::test_incomplete_demand_is_never_used_as_a_total",
    ],
    "fixed and calculable costs remain available in partial results": [
        f"{CAPACITY}::test_without_a_capacity_analysis_fixed_costs_stay_and_usage_is_unknown",
        f"{API}::test_a_partial_result_keeps_unknowns_apart",
    ],
    # Phase 6: aggregation and cost drivers.
    "aggregations are mathematically consistent": [
        f"{AGGREGATION}::test_multiple_components_add_up_to_the_known_total",
        f"{AGGREGATION}::test_shares_are_of_the_known_total",
    ],
    "currency and period mismatches are not silently combined": [
        f"{AGGREGATION}::test_another_currency_is_never_combined",
        f"{AGGREGATION}::test_billing_periods_are_derived_from_monthly_amounts_never_added_across",
    ],
    "unknown costs remain visible": [
        f"{AGGREGATION}::test_unknown_items_are_listed_apart_and_the_known_total_is_a_lower_bound",
        f"{AGGREGATION}::test_unsupported_components_make_the_total_a_lower_bound",
    ],
    "cost drivers are evidence-based": [
        f"{AGGREGATION}::test_drivers_are_explicit_calculations",
        f"{AGGREGATION}::test_drivers_never_judge_a_cost",
        f"{AGGREGATION}::test_lines_are_traceable_from_every_driver",
    ],
    # Phase 7: projections and scenarios.
    "projection assumptions are explicit": [
        f"{PROJECTION}::test_monthly_and_annual_projection_under_stated_conventions",
        f"{PROJECTION}::test_operating_hours_change_instance_hours_and_usage_but_not_stored_volume",
    ],
    "scenario calculations are reproducible": [
        f"{PROJECTION}::test_projections_are_reproducible",
        f"{PROJECTION}::test_the_capacity_run_must_be_of_the_same_scenario",
    ],
    "nonlinear pricing is not treated as linear": [
        f"{PROJECTION}::test_tiered_prices_are_not_treated_as_linear",
        f"{PROJECTION}::test_increased_workload_doubles_linear_usage_and_steps_replicas",
    ],
    "comparisons identify changed inputs": [
        f"{PROJECTION}::test_the_comparison_reports_totals_differences_and_changes",
        f"{PROJECTION}::test_resource_count_changes",
        f"{PROJECTION}::test_an_unknown_scenario_cost_is_never_a_difference",
        f"{PROJECTION}::test_a_zero_baseline_has_no_percentage",
    ],
    # Phase 8: API and persistence.
    "cost analysis is available through the API": [
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
        f"{API}::test_the_model_catalog",
    ],
    "results are persisted and retrievable": [
        f"{API}::test_analyses_are_append_only_and_reads_cost_the_same",
        f"{SERVICE}::test_reading_lists_analyses_and_line_items",
        f"{I}.database.test_migrations::test_downgrading_cost_leaves_pricing_intact",
    ],
    "authorization is enforced": [
        f"{API}::test_access_and_listing",
        f"{SERVICE}::test_access",
        f"{PRICING_API}::test_members_read_only_admins_create_strangers_see_nothing",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "OpenAPI documentation is updated": [
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
    "API and repository tests pass, including failures": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{API}::test_an_engine_failure_is_stored_as_failed",
        f"{SERVICE}::test_a_storage_failure_is_raised_not_swallowed",
        f"{SERVICE}::test_an_archive_during_the_calculation_refuses_the_store",
    ],
    # Phase 9: determinism, performance and security hardening.
    "determinism is demonstrated by tests": [
        f"{HARDENING}::test_the_engine_output_is_identical_on_repetition_and_reordering",
        f"{HARDENING}::test_the_context_fingerprint_follows_every_input",
        f"{CALCULATOR}::test_the_order_of_lines_and_the_result_are_deterministic",
    ],
    "monetary calculations are precise": [
        f"{HARDENING}::test_large_values_are_exact_up_to_the_limit_and_unknown_beyond",
        f"{HARDENING}::test_a_tier_boundary_is_charged_to_its_own_tier",
        f"{HARDENING}::test_a_price_per_block_divides_before_it_multiplies",
    ],
    "performance risks are documented": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{SERVICE}::test_the_engine_runs_off_the_event_loop",
        f"{API}::test_analyses_are_append_only_and_reads_cost_the_same",
    ],
    "security tests pass": [
        f"{S}.test_mass_assignment_sweep::test_every_body_rejects_undeclared_privileged_fields",
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
        f"{HERE}::test_the_engine_executes_no_dynamic_code",
    ],
    "existing functionality remains compatible": [
        f"{S}.test_traceability_capacity_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_architecture_crud::test_every_criterion_is_mapped",
    ],
    # Phase 10: documentation and final verification.
    "the engine is documented without overstating accuracy": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{HERE}::test_no_prices_are_shipped",
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
    assert len(ACCEPTANCE) == 47  # 6 + 5 + 4 + 5 + 4 + 4 + 4 + 4 + 5 + 5 + 1 (phases 0-10)


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


def test_the_cost_engine_reads_the_ir_and_defines_no_graph() -> None:
    """Components come from the IR (and its Topology index); no second graph or node model."""
    graph_names = {"Graph", "Node", "Edge", "Connection", "ArchitectureGraph", "Topology", "Component"}
    for package in ("engines/cost", "core/domain/cost"):
        for path, tree in _python(package):
            classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            assert not classes & graph_names, (path, classes & graph_names)
            assert not any(m in {"networkx", "igraph"} for m in _imports(tree)), path


def test_the_framework_is_generic() -> None:
    """calculator.py names no model, resource, provider or service: what to bill lives in the
    models, how to price it in the framework."""
    text = (ROOT / "engines" / "cost" / "calculator.py").read_text()
    for name in (
        "compute.instances",
        "instances",
        "storage",
        "requests",
        "data_transfer",
        "aws",
        "ec2",
        "rds",
    ):
        assert f'"{name}"' not in text, name


def test_the_engine_executes_no_dynamic_code() -> None:
    builtins = {"eval", "exec", "compile", "__import__"}
    for package in ("engines/cost", "core/domain/cost"):
        for path, tree in _python(package):
            calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
            assert not [f.id for f in calls if isinstance(f, ast.Name) and f.id in builtins], path
            assert not [
                f.attr for f in calls if isinstance(f, ast.Attribute) and f.attr == "import_module"
            ], path
            assert "importlib" not in _imports(tree), path


def test_no_prices_are_shipped() -> None:
    """Prices come only from an organization's snapshots: no price records are built in engine or
    persistence code, and the provider modules stay empty (no provider-specific rules)."""
    for package in ("engines", "persistence", "core/domain/capacity"):
        for path, tree in _python(package):
            calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
            assert not [f for f in calls if isinstance(f, ast.Name) and f.id in {"PricingRecord", "Tier"}], (
                path
            )
    for path in sorted((ROOT / "knowledge").rglob("*")):  # no price catalog in the knowledge base
        if path.is_file():
            assert not {"unit_price", "unitPrice", "price"} & set(path.read_text().split()), path
    for provider in ("aws", "azure", "gcp"):
        assert (ROOT / "engines" / "cost" / "providers" / f"{provider}.py").read_text() == ""


def test_every_documentation_topic_is_covered() -> None:
    """Phase 10's documentation list (19 topics), section by section, and every registered model."""
    text = DOC.read_text()
    for heading in (
        "## Responsibilities",
        "## Supported pricing models",
        "## Pricing catalog structure",
        "## Pricing snapshot semantics",
        "## Supported providers and services",
        "## Resource mapping",
        "## Workload and capacity integration",
        "## Calculation formulas",
        "## Currency and unit handling",
        "## Billing-period assumptions",
        "## Provenance and freshness",
        "## Unknown and unsupported costs",
        "## Scenario projections",
        "## API contracts",
        "## Persistence",
        "## Authorization",
        "## Known limitations",
        "## Adding a pricing model",
        "## Tests",
        "## Limits and performance",
    ):
        assert heading in text, heading
    assert "not invoices and not" in text  # the engine does not overstate its accuracy
    assert "no provider integration" in text  # implemented support apart from planned integrations
    assert "Planned, not implemented" in text
    from engines.cost.registry import default_registry  # noqa: PLC0415 - the shipped models

    for model in default_registry().models():
        assert f"`{model.meta.id}`" in text, model.meta.id


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known limitations"):
        assert heading in text, heading
