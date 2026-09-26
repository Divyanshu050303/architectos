"""Scenario projection: the baseline cost analysis re-priced under a cost scenario.

The scenario's architecture is the baseline revision with the scenario's configuration changes
(the capacity engine's ``apply_changes``: the same rules, still a valid IR); its usage comes from
the capacity engine's run of the same scenario (``ScenarioResult``), never extrapolated from the
baseline; it is priced with the baseline's pricing snapshot, currency and pricing date, by the same
registry. A workload change needs a capacity analysis (without one, usage is unknown on both sides
and the change could not show).
"""

import dataclasses

from core.domain.capacity.scenarios import ScenarioResult
from core.domain.cost.capacity import CapacityBasis
from core.domain.cost.errors import InvalidCostRequest
from core.domain.cost.projection import CostComparison, CostScenario, ScenarioProjection, assumptions
from core.domain.cost.results import CostResult
from core.domain.engine_results import Evidence
from core.domain.requirements.value_objects import decimal_to_str
from engines.capacity.operating_envelope import apply_changes, scenario_workload

from .calculator import Registry, analyze
from .context import CostContext


def _invalid(field: str, reason: str) -> InvalidCostRequest:
    return InvalidCostRequest(details={"field": field, "reason": reason})


def workload(basis: CapacityBasis | None) -> Evidence | None:
    """The workload behind the usage, stated."""
    if basis is None:
        return None
    load = basis.workload
    average = (
        f", average {decimal_to_str(load.average_rate.value)} {load.average_rate.unit}"
        if load.average_rate
        else ""
    )
    return Evidence(
        "workload", f"{load.name} ({load.type.value}): design {decimal_to_str(load.design_rate)}/s{average}"
    )


def baseline_assumptions(context: CostContext, result: CostResult) -> tuple[Evidence, ...]:
    return assumptions(context.request, result, workload(context.capacity))


def _basis(
    baseline: CostContext, scenario: CostScenario, capacity: ScenarioResult | None
) -> CapacityBasis | None:
    if baseline.capacity is None:
        if scenario.changes_workload:
            raise _invalid("scenarios.workload", "needs_capacity_analysis")
        if scenario.replicas_from_capacity:
            raise _invalid("scenarios.replicas_from_capacity", "needs_capacity_analysis")
        if capacity is not None:
            raise _invalid("scenarios", "capacity_without_analysis")
        return None
    if capacity is None:
        raise _invalid("scenarios", "capacity_run_required")
    if capacity.scenario != scenario.scenario:
        raise _invalid("scenarios", "capacity_scenario_mismatch")
    load = scenario_workload(baseline.capacity.workload, scenario.scenario)
    return baseline.capacity.for_scenario(load, capacity.result, capacity.scaling)


def project(
    baseline: CostContext,
    baseline_result: CostResult,
    scenario: CostScenario,
    registry: Registry,
    capacity: ScenarioResult | None = None,
) -> ScenarioProjection:
    """``scenario`` priced and compared with ``baseline_result``; deterministic for equal inputs."""
    basis = _basis(baseline, scenario, capacity)
    request = dataclasses.replace(
        baseline.request,
        operating_hours_per_month=scenario.operating_hours_per_month
        or baseline.request.operating_hours_per_month,
        replicas_from_capacity=baseline.request.replicas_from_capacity
        if scenario.replicas_from_capacity is None
        else scenario.replicas_from_capacity,
    )
    ir = apply_changes(baseline.ir, scenario.scenario.changes)
    context = CostContext(ir, baseline.revision, request, baseline.snapshot, baseline.provider, basis)
    result = analyze(context, registry)
    comparison = CostComparison.between(
        baseline_result, result, scenario.changed_assumptions(baseline.request)
    )
    return ScenarioProjection(scenario, result, comparison, assumptions(request, result, workload(basis)))
