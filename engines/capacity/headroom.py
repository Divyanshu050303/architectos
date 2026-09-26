"""Utilization, headroom and bottleneck candidates: demand against capacity, per node and
resource, in one unit.

**Pairing.** For each node:

- ``work_rate``: the work arriving (the sum of its propagated demand, in the node's work unit)
  against its throughput limits; when several are known, the **lowest binds** (the evidence names
  it). Demand is unknown when the propagation could not complete it.
- every other resource a model reports (``cpu``, ``connections``, ``bandwidth``, ``storage``): the
  required amount against the limit of the same name.

The utilization contract (``core/domain/capacity/results.py``) fixes the arithmetic: exact ratios,
never clamped, no division by zero, unknown when a side is unknown.

**Bottlenecks.** A resource is reported when:

- demand exceeds capacity (``exceeds_capacity``), equals it (``at_capacity``), exceeds the
  workload's target utilization (``above_target``), or capacity is zero (``no_capacity``): these
  are ``modeled``, since demand and capacity are both known;
- the throughput capacity is unknown on a node the workload reaches over a connection its source
  waits for (synchronous and not declared non-critical): ``unknown_capacity``, a ``candidate``.

No single component is named the system's bottleneck: the list is ordered (modeled first, worst
condition and utilization first), and the summary's ``saturation_multiple`` is complete only when
every component the workload reaches has a known throughput utilization.
"""

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal

from core.architecture_ir.dependency import Interaction
from core.domain.capacity.results import (
    Bottleneck,
    BottleneckCondition,
    Certainty,
    ComponentResult,
    Estimate,
    Evidence,
    Utilization,
    UtilizationState,
)

from .context import CapacityContext
from .estimates import number, quantity, work, work_unit

REMEDIATION = {
    "work_rate": "Raise the component's throughput (more or larger replicas, if its model supports "
    "that), reduce the traffic reaching it (caching, batching), or confirm the declared limit.",
    "cpu": "Add CPU (cores per replica or replicas) or reduce the CPU cost per unit of work.",
    "connections": "Reduce pool sizes or replicas of the sources, add a connection pooler, or raise "
    "max_connections.",
    "bandwidth": "Reduce payload sizes (compression, pagination) or provision more bandwidth.",
    "storage": "Shorten the retention, reduce payload sizes, or provision more storage.",
}
UNKNOWN_REMEDIATION = (
    "Declare the component's throughput (throughput_limit_per_second, or "
    "throughput_per_replica_per_second with replicas) so its capacity can be compared with its demand."
)


def _binding(limits: Iterable[Estimate]) -> Estimate | None:
    known = [e for e in limits if e.quantity is not None]
    return min(known, key=lambda e: (e.quantity.canonical, e.model_id or "")) if known else None  # type: ignore[union-attr]


def utilization(
    node_id: str, component: ComponentResult, context: CapacityContext, *, complete: bool
) -> tuple[Utilization, ...]:
    node = context.topology.node(node_id)
    target = context.workload.target_utilization
    found: list[Utilization] = []
    throughput = [e for e in component.limits if e.resource == "work_rate"]
    if component.demand or throughput:
        unit = work_unit(node, component.demand) if node is not None else "requests/second"
        demand = quantity(work(component.demand), unit) if complete else None  # None beyond 10^15
        limit = _binding(throughput)
        found.append(Utilization("work_rate", demand, limit.quantity if limit else None, target))
    for resource in sorted({e.resource for e in component.resources} - {"storage_growth", "time_to_full"}):
        required = next((e for e in component.resources if e.resource == resource), None)
        limit = _binding(e for e in component.limits if e.resource == resource)
        if limit is None and not any(e.resource == resource for e in component.limits):
            continue  # nothing to compare against: the amount stays a resource estimate
        amount = required.quantity if required is not None else None
        capacity = limit.quantity if limit is not None else None
        if amount is not None and capacity is not None and amount.dimension is not capacity.dimension:
            continue  # never compared across dimensions
        found.append(Utilization(resource, amount, capacity, target))
    return tuple(found)


def _on_waited_path(node_id: str, component: ComponentResult, context: CapacityContext) -> bool:
    """Whether demand reaches the node over a connection its source waits for."""
    for demand in component.demand:
        if len(demand.path) < 3:
            return True
        connection = context.topology.connection(demand.path[1])
        if (
            connection is not None
            and connection.critical is not False
            and connection.interaction is not Interaction.ASYNCHRONOUS
        ):
            return True
    return False


def _evidence(u: Utilization, component: ComponentResult) -> tuple[Evidence, ...]:
    items = []
    if u.demand is not None:
        items.append(Evidence("demand", str(u.demand)))
    if u.capacity is not None:
        items.append(Evidence("capacity", str(u.capacity)))
        binding = _binding(e for e in component.limits if e.resource == u.resource)
        if binding is not None:
            items.append(Evidence("binding_limit", f"{binding.model_id}: {binding.basis}"))
    if u.ratio is not None:
        items.append(Evidence("utilization", number(u.ratio)))
    if u.demand is not None and u.capacity is not None and u.demand.canonical > 0:
        items.append(
            Evidence("saturates_at_workload_multiple", number(u.capacity.canonical / u.demand.canonical))
        )
    return tuple(items)


def bottlenecks(component: ComponentResult, context: CapacityContext) -> list[Bottleneck]:
    assumptions = tuple(sorted(context.assumptions))
    found: list[Bottleneck] = []
    for u in component.utilization:
        condition, explanation = _condition(u)
        if condition is None:
            if not (
                u.resource == "work_rate"
                and u.state is UtilizationState.UNKNOWN
                and u.demand is not None
                and u.demand.canonical > 0
                and _on_waited_path(component.node_id, component, context)
            ):
                continue
            condition = BottleneckCondition.UNKNOWN_CAPACITY
            explanation = (
                f"{component.node_id} receives {u.demand} on a path its callers wait for, but its "
                "throughput capacity is not known: it may be a bottleneck."
            )
        certainty = (
            Certainty.MODELED if u.demand is not None and u.capacity is not None else Certainty.CANDIDATE
        )
        remediation = (
            UNKNOWN_REMEDIATION
            if condition is BottleneckCondition.UNKNOWN_CAPACITY
            else REMEDIATION.get(u.resource, "Increase the capacity or reduce the demand.")
        )
        found.append(
            Bottleneck(
                component.node_id,
                u.resource,
                condition,
                certainty,
                u,
                explanation,
                remediation,
                _evidence(u, component),
                assumptions,
            )
        )
    return found


def _condition(u: Utilization) -> tuple[BottleneckCondition | None, str]:
    state, ratio = u.state, u.ratio
    match state:
        case UtilizationState.ABOVE:
            ratio_text = number(ratio or Decimal(0))
            return BottleneckCondition.EXCEEDS_CAPACITY, (
                f"Demand ({u.demand}) exceeds the modeled capacity ({u.capacity}): utilization {ratio_text}."
            )
        case UtilizationState.AT:
            return (
                BottleneckCondition.AT_CAPACITY,
                f"Demand ({u.demand}) equals the modeled capacity: no headroom.",
            )
        case UtilizationState.NO_CAPACITY:
            return BottleneckCondition.NO_CAPACITY, f"The capacity is zero while demand is {u.demand}."
        case UtilizationState.BELOW if u.target is not None and ratio is not None and ratio > u.target:
            return BottleneckCondition.ABOVE_TARGET, (
                f"Utilization {number(ratio)} is above the target {number(u.target)}."
            )
        case _:
            return None, ""


def assess(
    components: Iterable[ComponentResult], context: CapacityContext, incomplete: frozenset[str]
) -> tuple[list[ComponentResult], list[Bottleneck]]:
    """Components with their utilization, and the bottleneck candidates, deterministically."""
    assessed: list[ComponentResult] = []
    found: list[Bottleneck] = []
    for component in components:
        pairs = utilization(
            component.node_id, component, context, complete=component.node_id not in incomplete
        )
        updated = replace(component, utilization=pairs)
        assessed.append(updated)
        found += bottlenecks(updated, context)
    return assessed, found
