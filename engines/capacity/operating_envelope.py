"""Scenarios and scaling: the same models on an explicitly changed workload or configuration, and
what a model says would bring a constrained resource within its target.

**Applying a scenario.** On in-memory copies (nothing is stored, the architecture is not edited):

- the workload's rates are multiplied by the scenario's multiplier (``growth``, or
  ``(1 + growth_rate) ^ periods``), or set to its ``target_rate`` (the average rate keeps its ratio
  to the peak); a batch's interval is divided by the multiplier (a target rate does not apply to a
  batch). The target utilization may be overridden;
- each configuration change sets one capacity or traffic property of a named node or connection;
  the changed architecture must still be a valid IR.

Demand propagation is linear in the workload rate (every multiplier is a fixed ratio), so projected
demand is the baseline demand times the multiplier; capacities change only where the scenario
changes the configuration a model reads. Nothing else scales implicitly.

**Scaling options.** Only where a model defines how capacity scales:

- ``work_rate`` limited by ``replica-throughput`` (linear in replicas, the model's stated
  assumption): replicas needed = ceil(demand / (throughput per replica * goal));
- ``cpu`` from ``cpu-demand``: cores per replica needed = demand / (replicas * goal) (vertical), or
  replicas needed = ceil(demand / (cores per replica * goal)) (horizontal, linear);

where ``goal`` is the target utilization, else 1. A resource limited by a declared total
(``declared-throughput``) or by connections, storage or bandwidth has no scaling option: it is
reported as ``scaling_unsupported`` (change its configuration in a scenario to see the effect).
"""

import dataclasses
import math
from decimal import Decimal

from core.architecture_ir.configuration import CONNECTION_PROPERTIES as IR_CONNECTION_PROPERTIES
from core.architecture_ir.configuration import NODE_PROPERTIES as IR_NODE_PROPERTIES
from core.architecture_ir.configuration import Configuration, ValueType
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.capacity.errors import InvalidScenario, InvalidWorkload
from core.domain.capacity.results import CapacityResult, Evidence, Unsupported, Utilization
from core.domain.capacity.scenarios import (
    CONNECTION_PROPERTIES,
    NODE_PROPERTIES,
    Comparison,
    ConfigurationChange,
    ScalingKind,
    ScalingOption,
    Scenario,
    ScenarioResult,
)
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType

from .context import CapacityContext
from .engine import Propagator, Registry, analyze
from .estimates import declared, number


def _invalid(field: str, reason: str) -> InvalidScenario:
    return InvalidScenario(details={"field": field, "reason": reason})


def scenario_workload(workload: WorkloadProfile, scenario: Scenario) -> WorkloadProfile:
    multiplier = scenario.multiplier
    if scenario.target_rate is not None:
        if workload.type is WorkloadType.BATCH or workload.peak_rate is None:
            raise _invalid("target_rate", "not_applicable_to_batch")
        if scenario.target_rate.dimension is not workload.rate_dimension:
            raise _invalid("target_rate", "wrong_dimension")
        multiplier = scenario.target_rate.canonical / workload.peak_rate.canonical
    changes: dict[str, object] = {}
    if multiplier is not None:
        if workload.type is WorkloadType.BATCH and workload.batch_interval is not None:
            interval = workload.batch_interval.canonical / multiplier
            changes["batch_interval"] = _rate(interval, "ms", "growth")
        else:
            for name in ("peak_rate", "average_rate"):
                rate: Quantity | None = getattr(workload, name)
                if rate is not None:
                    changes[name] = _rate(rate.value * multiplier, rate.unit, "growth")
    if scenario.target_utilization is not None:
        changes["target_utilization"] = scenario.target_utilization
    try:
        return dataclasses.replace(workload, **changes)  # type: ignore[arg-type]
    except InvalidWorkload as error:
        raise _invalid(error.details["field"], error.details["reason"]) from None


def _rate(value: Decimal, unit: str, field: str) -> Quantity:
    rounded = Quantity.rounded(value, unit) if value < Decimal(10) ** 15 else None
    if rounded is None or rounded.value == 0:
        raise _invalid(field, "out_of_range")
    return rounded


def apply_changes(ir: ArchitectureIR, changes: tuple[ConfigurationChange, ...]) -> ArchitectureIR:
    """``ir`` with the scenario's configuration changes, still a valid IR."""
    if not changes:
        return ir
    nodes = {n.id: n for n in ir.nodes}
    connections = {c.id: c for c in ir.connections}
    for change in changes:
        if change.element_id in nodes:
            allowed, specs = NODE_PROPERTIES, IR_NODE_PROPERTIES
        elif change.element_id in connections:
            allowed, specs = CONNECTION_PROPERTIES, IR_CONNECTION_PROPERTIES
        else:
            raise _invalid("changes.element_id", "not_found")
        if change.property not in allowed:
            raise _invalid("changes.property", "not_changeable_here")
        value: int | Decimal = change.value
        if specs[change.property].type is ValueType.INTEGER:
            if change.value != change.value.to_integral_value():
                raise _invalid("changes.value", "not_a_whole_number")
            value = int(change.value)
        element = nodes.get(change.element_id) or connections[change.element_id]
        config = element.configuration
        updated = Configuration(
            {**config.values, change.property: value}, config.unknown - {change.property}, config.extra
        )
        try:
            if change.element_id in nodes:
                nodes[change.element_id] = dataclasses.replace(
                    nodes[change.element_id], configuration=updated
                )
            else:
                connections[change.element_id] = dataclasses.replace(
                    connections[change.element_id], configuration=updated
                )
        except InvalidArchitecture as error:
            raise _invalid("changes.value", error.violations[0].rule) from None
    try:
        return dataclasses.replace(ir, nodes=tuple(nodes.values()), connections=tuple(connections.values()))
    except InvalidArchitecture as error:
        raise _invalid("changes", error.violations[0].rule) from None


def _goal(u: Utilization) -> Decimal:
    return u.target if u.target is not None else Decimal(1)


def scaling_options(
    result: CapacityResult, context: CapacityContext
) -> tuple[tuple[ScalingOption, ...], tuple[Unsupported, ...]]:
    """For every resource above its goal, what a model says would bring it within it."""
    options: list[ScalingOption] = []
    unsupported: list[Unsupported] = []
    for component in result.components:
        node = context.topology.node(component.node_id)
        if node is None:
            continue
        for u in component.utilization:
            if u.demand is None or u.capacity is None or u.ratio is None or u.ratio <= _goal(u):
                continue
            goal = _goal(u)
            demand = u.demand.canonical
            if u.resource == "work_rate":
                binding = min(
                    (e for e in component.limits if e.resource == "work_rate" and e.quantity is not None),
                    key=lambda e: e.quantity.canonical,  # type: ignore[union-attr]
                )
                per_replica, replicas = (
                    declared(node, "throughput_per_replica_per_second"),
                    declared(node, "replicas"),
                )
                if binding.model_id == "replica-throughput" and per_replica and replicas is not None:
                    needed = math.ceil(demand / (per_replica * goal))
                    options.append(
                        _replicas(component.node_id, u.resource, replicas, needed, per_replica, goal)
                    )
                    continue
            elif u.resource == "cpu":
                cpu = _cpu(node, demand, goal)
                if cpu:
                    options += cpu
                    continue
            unsupported.append(
                Unsupported(
                    component.node_id,
                    "scaling_unsupported",
                    f"No model states how {u.resource} of {component.node_id} scales; change its "
                    "configuration in a scenario to see the effect.",
                )
            )
    return tuple(options), tuple(unsupported)


def _replicas(
    node_id: str, resource: str, current: Decimal, needed: int, per_replica: Decimal, goal: Decimal
) -> ScalingOption:
    return ScalingOption(
        node_id,
        resource,
        ScalingKind.HORIZONTAL,
        Quantity.rounded(current, "replicas"),
        Quantity.rounded(Decimal(needed), "replicas"),
        "ceil(demand / (throughput_per_replica_per_second * goal)), linear in replicas (stated)",
        "replica-throughput",
        (
            Evidence("throughput_per_replica_per_second", number(per_replica)),
            Evidence("goal_utilization", number(goal)),
        ),
    )


def _cpu(node: Node, demand: Decimal, goal: Decimal) -> list[ScalingOption]:
    node_id = node.id
    replicas, cores = declared(node, "replicas"), declared(node, "cpu_limit_cores")
    if not replicas or not cores:
        return []
    per_replica = demand / (replicas * goal)
    evidence = (Evidence("cpu_demand_cores", number(demand)), Evidence("goal_utilization", number(goal)))
    return [
        ScalingOption(
            node_id,
            "cpu",
            ScalingKind.VERTICAL,
            Quantity.rounded(cores, "cores"),
            Quantity.rounded(per_replica, "cores"),
            "cpu demand / (replicas * goal): cores per replica",
            "cpu-demand",
            evidence,
        ),
        ScalingOption(
            node_id,
            "cpu",
            ScalingKind.HORIZONTAL,
            Quantity.rounded(replicas, "replicas"),
            Quantity.rounded(Decimal(math.ceil(demand / (cores * goal))), "replicas"),
            "ceil(cpu demand / (cpu_limit_cores * goal)): replicas, at a constant CPU cost per unit of work",
            "cpu-demand",
            evidence,
        ),
    ]


def run_scenario(
    baseline: CapacityContext,
    baseline_result: CapacityResult,
    scenario: Scenario,
    registry: Registry,
    propagate: Propagator,
) -> ScenarioResult:
    """The scenario's result, its scaling options, and its comparison with the baseline."""
    workload = scenario_workload(baseline.workload, scenario)
    ir = apply_changes(baseline.ir, scenario.changes)
    request = dataclasses.replace(baseline.request, workload=workload)
    context = CapacityContext(ir, baseline.revision, request)
    result = analyze(context, registry, propagate)
    scaling, unsupported = scaling_options(result, context)
    return ScenarioResult(
        scenario, result, scaling, Comparison.between(baseline_result, result, scenario), unsupported
    )
