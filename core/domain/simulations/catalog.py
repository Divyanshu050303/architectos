"""The supported scenario types: what each may change, in which units, on which elements, how it is
applied, which engines evaluate it, what it cannot evaluate, and its limits. Versioned: a change to
what a type means is a new version, recorded with every simulation that used it.

Every part of a scenario belongs to exactly one type:

- the workload change → ``workload``;
- a configuration change → the type whose properties include it (``replicas``, ``traffic``,
  ``resilience``, ``resources``, in that precedence); a property no simulated engine reads is refused
  (``not_simulated``);
- a failure → ``component_failure``, ``connection_failure``, ``zone_failure`` or ``region_failure``.

Which analyses a configuration change concerns follows the engines that read the property:
``capacity`` for the properties the Capacity Engine's scenarios may change, ``reliability`` for the
Reliability Engine's inputs, ``cost`` for what the Cost Engine maps and prices. Nothing else is
assumed to depend on a property.
"""

from dataclasses import dataclass
from typing import Any

from core.domain.capacity.scenarios import CONNECTION_PROPERTIES as CAPACITY_CONNECTION_PROPERTIES
from core.domain.capacity.scenarios import NODE_PROPERTIES as CAPACITY_NODE_PROPERTIES
from core.domain.reliability.inputs import PROPERTIES as RELIABILITY_INPUTS

from .scenarios import MAX_CHANGES, MAX_FAILURES, ConfigurationChange, Failure
from .values import AnalysisKind, FailureKind

A = AnalysisKind
CAPACITY_PROPERTIES = CAPACITY_NODE_PROPERTIES | CAPACITY_CONNECTION_PROPERTIES
RELIABILITY_PROPERTIES = frozenset(RELIABILITY_INPUTS)
# What the Cost Engine maps and prices (engines/cost/mapping.py and usage.py; kept in step by a test).
COST_PROPERTIES = frozenset(
    {
        "pricing_service",
        "pricing_sku",
        "pricing_storage_sku",
        "pricing_conditions",
        "instance_class",
        "deployment_model",
        "region",
        "replicas",
        "storage_bytes",
    }
)
REPLICA_PROPERTIES = frozenset({"replicas", "min_healthy_replicas", "autoscaling_max_replicas"})
FAILURE_PROPAGATION = (
    "Its impact on each entry point follows the Reliability Engine's dependency semantics (required or "
    "optional dependencies, declared redundancy groups and minimum healthy replicas, failover only when "
    "declared automatic)."
)
FAILURE_UNSUPPORTED = (
    "Failure probabilities, durations, recovery times and outage predictions (never invented).",
    "Rerouted or redistributed load after a failure (no model says where traffic goes).",
    "Latency, error rates, retries, timeouts and queueing during a failure.",
)
CONFIG_INPUTS = (
    "element_id",
    "property (its unit is part of its name)",
    "value (the property's type and range; null clears it)",
)


@dataclass(frozen=True, slots=True)
class ScenarioType:
    id: str
    version: int
    name: str
    description: str
    applies_to: str  # what it names: the workload, nodes, connections, a zone or a region
    inputs: tuple[str, ...]  # its fields, with units
    properties: frozenset[str]  # the IR properties it may change (configuration types)
    analyses: tuple[AnalysisKind, ...]  # the engines that evaluate it
    requires: tuple[str, ...]  # request inputs an analysis needs for it (else that analysis is unsupported)
    overlay: str  # how it is applied to the in-memory copy
    unsupported: tuple[str, ...]
    limit: int  # at most this many parts of this type in one scenario
    output: str  # what it produces

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "applies_to": self.applies_to,
            "inputs": list(self.inputs),
            "properties": sorted(self.properties),
            "analyses": [a.value for a in self.analyses],
            "requires": list(self.requires),
            "overlay": self.overlay,
            "unsupported": list(self.unsupported),
            "limit": self.limit,
            "output": self.output,
        }


def _analyses_of(properties: frozenset[str]) -> tuple[AnalysisKind, ...]:
    read = (
        (A.CAPACITY, CAPACITY_PROPERTIES),
        (A.RELIABILITY, RELIABILITY_PROPERTIES),
        (A.COST, COST_PROPERTIES),
    )
    return tuple(kind for kind, names in read if properties & names)


def _configuration(
    id_: str, name: str, description: str, applies_to: str, properties: frozenset[str]
) -> ScenarioType:
    return ScenarioType(
        id=id_,
        version=1,
        name=name,
        description=description,
        applies_to=applies_to,
        inputs=CONFIG_INPUTS,
        properties=properties,
        analyses=_analyses_of(properties),
        requires=("capacity: a workload profile", "cost: pricing inputs"),
        overlay="The property is set on the named element of an in-memory copy of the exact revision, "
        "stamped with scenario provenance; the copy must remain a valid architecture. The revision itself "
        "is not changed.",
        unsupported=(
            "Properties no simulated engine reads (e.g. security or observability settings).",
            "Scaling behavior a model does not state (no implicit linear scaling).",
        ),
        limit=MAX_CHANGES,
        output="Per analysis, baseline and scenario values of what the property changes, with units.",
    )


def _failure(
    kind: FailureKind,
    description: str,
    applies_to: str,
    target: str,
    overlay: str,
    extra: tuple[str, ...] = (),
) -> ScenarioType:
    return ScenarioType(
        id=f"{kind.value}_failure",
        version=1,
        name=f"{kind.value.capitalize()} failure",
        description=description,
        applies_to=applies_to,
        inputs=(f"target ({target})",),
        properties=frozenset(),
        analyses=(A.RELIABILITY,),
        requires=(),
        overlay=f"{overlay} {FAILURE_PROPAGATION}",
        unsupported=FAILURE_UNSUPPORTED + extra,
        limit=MAX_FAILURES,
        output="Per entry point: unaffected, tolerated, degraded, interrupted or unknown, with the "
        "unavailable elements it requires and what would decide an unknown.",
    )


TYPES: tuple[ScenarioType, ...] = (
    ScenarioType(
        id="workload",
        version=1,
        name="Workload change",
        description="The workload's rates multiplied, compounded over periods, or set to a target rate; "
        "optionally another target utilization.",
        applies_to="the workload profile",
        inputs=(
            "growth (a multiplier above 0)",
            "growth_rate (per period) and periods (1 to 120)",
            "target_rate (a rate with its unit, e.g. requests/second)",
            "target_utilization (above 0, at most 1)",
        ),
        properties=frozenset(),
        analyses=(A.CAPACITY, A.COST),
        requires=("capacity: a workload profile", "cost: pricing inputs and the capacity analysis"),
        overlay="Applied by the Capacity Engine's scenario to the workload profile; the architecture is "
        "unchanged.",
        unsupported=(
            "Workload mix changes the workload profile does not express.",
            "Time-varying load, bursts over time, latency and queueing.",
        ),
        limit=1,
        output="Demand, capacity and utilization per resource, bottlenecks, and cost, baseline and scenario.",
    ),
    _configuration(
        "replicas",
        "Replica change",
        "The replica count, minimum healthy replicas or autoscaling maximum of a component.",
        "nodes",
        REPLICA_PROPERTIES,
    ),
    _configuration(
        "traffic",
        "Traffic routing change",
        "The traffic share, calls per request, cache hit ratio or pool size of a connection.",
        "connections",
        frozenset(CAPACITY_CONNECTION_PROPERTIES),
    ),
    _configuration(
        "resilience",
        "Resilience configuration change",
        "Declared availability, recovery, failover, redundancy, independence, replication, backups or "
        "placement of a component.",
        "nodes",
        RELIABILITY_PROPERTIES - REPLICA_PROPERTIES,
    ),
    _configuration(
        "resources",
        "Resource or pricing configuration change",
        "Declared limits and per-request costs (CPU, throughput, connections, storage, bandwidth, retention) "
        "or the pricing mapping of a component.",
        "nodes",
        (CAPACITY_NODE_PROPERTIES | COST_PROPERTIES) - REPLICA_PROPERTIES - RELIABILITY_PROPERTIES,
    ),
    _failure(
        FailureKind.COMPONENT,
        "A component unavailable.",
        "nodes (not clients or boundaries)",
        "a node id",
        "The component is marked unavailable, not removed: what depends on it stays visible.",
    ),
    _failure(
        FailureKind.CONNECTION,
        "A connection unavailable (the dependency it represents is lost).",
        "connections",
        "a connection id",
        "The connection is marked unavailable, not removed.",
    ),
    _failure(
        FailureKind.ZONE,
        "Every component declared only in one availability zone unavailable.",
        "a zone some component declares in availability_zones",
        "a zone, e.g. eu-west-1a",
        "Components whose declared zones are all the failed zone are unavailable; components declaring other "
        "zones too keep running; components declaring no zone are unknown.",
        ("How replicas or capacity are split across zones (not declared).",),
    ),
    _failure(
        FailureKind.REGION,
        "Every component declared in one region unavailable.",
        "a region some component declares",
        "a region, e.g. eu-west-1",
        "Components declaring the region are unavailable; components declaring no region are unknown.",
    ),
)
BY_ID: dict[str, ScenarioType] = {t.id: t for t in TYPES}
assert len(BY_ID) == len(TYPES)  # noqa: S101 - one id per type
_CONFIGURATION_TYPES = tuple(BY_ID[i] for i in ("replicas", "traffic", "resilience", "resources"))


def type_of_change(change: ConfigurationChange) -> ScenarioType | None:
    """The configuration type a change belongs to (None: no simulated engine reads the property)."""
    return next((t for t in _CONFIGURATION_TYPES if change.property in t.properties), None)


def type_of_failure(failure: Failure) -> ScenarioType:
    return BY_ID[f"{failure.kind.value}_failure"]


def analyses_of_change(change: ConfigurationChange) -> tuple[AnalysisKind, ...]:
    """The engines that read the changed property."""
    return _analyses_of(frozenset({change.property}))
