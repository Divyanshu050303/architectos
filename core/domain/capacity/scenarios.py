"""Capacity scenarios: the same architecture and models under an explicitly changed workload or
configuration. Deterministic recalculation, not simulation: nothing varies over time, nothing is
random, and nothing is assumed about how a component scales beyond what its model states.

A scenario says how the workload changes (at most one of: a ``growth`` multiplier, compound growth
``growth_rate`` per period over ``periods``, or an absolute ``target_rate``), may override the
target utilization, and may change declared configuration on named nodes and connections (more
replicas, a higher declared limit, a different traffic share). Only capacity and traffic
properties may change; the scenario never edits the architecture itself.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidQuantity, InvalidScenario
from .results import Bottleneck, CapacityResult, Evidence, Unsupported
from .units import Quantity, exact

MAX_SCENARIOS = 10
MAX_CHANGES = 50
MAX_PERIODS = 120
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
NODE_PROPERTIES = frozenset(
    {
        "replicas",
        "autoscaling_max_replicas",
        "throughput_limit_per_second",
        "throughput_per_replica_per_second",
        "cpu_limit_cores",
        "cpu_core_seconds_per_request",
        "max_connections",
        "storage_bytes",
        "retention_seconds",
        "network_bandwidth_bytes_per_second",
    }
)
CONNECTION_PROPERTIES = frozenset({"traffic_ratio", "calls_per_request", "cache_hit_ratio", "pool_size"})


def _invalid(field: str, reason: str) -> InvalidScenario:
    return InvalidScenario(details={"field": field, "reason": reason})


def _decimal(value: object, field: str) -> Decimal:
    try:
        return exact(value, field)
    except InvalidQuantity as error:
        raise _invalid(field, error.details["reason"]) from None


@dataclass(frozen=True, slots=True)
class ConfigurationChange:
    """``property`` of the node or connection ``element_id`` set to ``value`` for the scenario."""

    element_id: str
    property: str
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.element_id, str) or not 0 < len(self.element_id) <= 128:
            raise _invalid("changes.element_id", "invalid_reference")
        if self.property not in NODE_PROPERTIES | CONNECTION_PROPERTIES:
            raise _invalid("changes.property", "not_changeable")
        object.__setattr__(self, "value", _decimal(self.value, "changes.value"))

    def to_dict(self) -> dict[str, str]:
        return {"element_id": self.element_id, "property": self.property, "value": decimal_to_str(self.value)}


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    growth: Decimal | None = None  # multiplier of the workload's rates, e.g. 2
    growth_rate: Decimal | None = None  # per period, e.g. 0.1 = +10 % per period (compounded)
    periods: int | None = None
    target_rate: Quantity | None = None  # replaces the workload's design rate
    target_utilization: Decimal | None = None  # overrides the workload's
    changes: tuple[ConfigurationChange, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not NAME.fullmatch(self.name):
            raise _invalid("name", "invalid_name")
        ways = [
            self.growth is not None,
            self.growth_rate is not None or self.periods is not None,
            self.target_rate is not None,
        ]
        if sum(ways) > 1:
            raise _invalid("growth", "more_than_one_workload_change")
        self._check_growth()
        if self.target_rate is not None and (
            not isinstance(self.target_rate, Quantity) or self.target_rate.value == 0
        ):
            raise _invalid("target_rate", "must_be_positive")
        if self.target_utilization is not None:
            target = _decimal(self.target_utilization, "target_utilization")
            if not 0 < target <= 1:
                raise _invalid("target_utilization", "out_of_range")
            object.__setattr__(self, "target_utilization", target)
        if not isinstance(self.changes, tuple) or len(self.changes) > MAX_CHANGES:
            raise _invalid("changes", "too_many")
        keys = [(c.element_id, c.property) for c in self.changes]
        if len(keys) != len(set(keys)):
            raise _invalid("changes", "duplicate_change")
        object.__setattr__(
            self, "changes", tuple(sorted(self.changes, key=lambda c: (c.element_id, c.property)))
        )

    def _check_growth(self) -> None:
        if self.growth is not None:
            growth = _decimal(self.growth, "growth")
            if growth == 0:
                raise _invalid("growth", "out_of_range")
            object.__setattr__(self, "growth", growth)
        if self.growth_rate is not None or self.periods is not None:
            if self.growth_rate is None or self.periods is None:
                raise _invalid("periods" if self.periods is None else "growth_rate", "required")
            object.__setattr__(self, "growth_rate", _decimal(self.growth_rate, "growth_rate"))
            if (
                isinstance(self.periods, bool)
                or not isinstance(self.periods, int)
                or not 0 < self.periods <= MAX_PERIODS
            ):
                raise _invalid("periods", "out_of_range")

    @property
    def multiplier(self) -> Decimal | None:
        """The factor applied to the workload's rates, when the scenario scales them."""
        if self.growth is not None:
            return self.growth
        if self.growth_rate is not None and self.periods is not None:
            return (1 + self.growth_rate) ** self.periods
        return None

    def to_dict(self) -> dict[str, Any]:
        def number(value: Decimal | None) -> str | None:
            return decimal_to_str(value) if value is not None else None

        return {
            "name": self.name,
            "growth": number(self.growth),
            "growth_rate": number(self.growth_rate),
            "periods": self.periods,
            "target_rate": self.target_rate.to_dict() if self.target_rate is not None else None,
            "target_utilization": number(self.target_utilization),
            "changes": [c.to_dict() for c in self.changes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        known = {"name", "growth", "growth_rate", "periods", "target_rate", "target_utilization", "changes"}
        if not isinstance(data, Mapping):
            raise _invalid("scenario", "not_an_object")
        unknown = sorted(set(data) - known)
        if unknown:
            raise _invalid(unknown[0], "unknown_field")
        raw_changes = data.get("changes") or []
        if not isinstance(raw_changes, list) or len(raw_changes) > MAX_CHANGES:
            raise _invalid("changes", "too_many")
        changes = []
        for raw in raw_changes:
            if not isinstance(raw, Mapping) or set(raw) != {"element_id", "property", "value"}:
                raise _invalid("changes", "invalid_change")
            changes.append(ConfigurationChange(raw["element_id"], raw["property"], raw["value"]))
        target = data.get("target_rate")
        try:
            target_rate = Quantity.from_dict(target, "target_rate") if target is not None else None
        except InvalidQuantity as error:
            raise _invalid(error.details["field"], error.details["reason"]) from None
        return cls(
            name=data.get("name"),  # type: ignore[arg-type]
            growth=data.get("growth"),
            growth_rate=data.get("growth_rate"),
            periods=data.get("periods"),
            target_rate=target_rate,
            target_utilization=data.get("target_utilization"),
            changes=tuple(changes),
        )


class ScalingKind(StrEnum):
    HORIZONTAL = "horizontal"  # more replicas
    VERTICAL = "vertical"  # more resources per replica


@dataclass(frozen=True, slots=True)
class ScalingOption:
    """What a model says would bring a resource within its target: e.g. 7 replicas instead of 4.
    Only produced where a model defines how capacity scales (``basis`` says how)."""

    node_id: str
    resource: str
    kind: ScalingKind
    current: Quantity
    required: Quantity
    basis: str
    model_id: str
    evidence: tuple[Evidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "resource": self.resource,
            "kind": self.kind.value,
            "current": self.current.to_dict(),
            "required": self.required.to_dict(),
            "basis": self.basis,
            "model_id": self.model_id,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class ResourceDelta:
    """One resource of one node, in the baseline and in the scenario (canonical units)."""

    node_id: str
    resource: str
    demand_before: Decimal | None
    demand_after: Decimal | None
    capacity_before: Decimal | None
    capacity_after: Decimal | None
    utilization_before: Decimal | None
    utilization_after: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        def number(value: Decimal | None) -> str | None:
            return decimal_to_str(value) if value is not None else None

        return {
            "node_id": self.node_id,
            "resource": self.resource,
            "demand_before": number(self.demand_before),
            "demand_after": number(self.demand_after),
            "capacity_before": number(self.capacity_before),
            "capacity_after": number(self.capacity_after),
            "utilization_before": number(self.utilization_before),
            "utilization_after": number(self.utilization_after),
        }


def _bottleneck_key(b: Bottleneck) -> tuple[str, str, str]:
    return (b.node_id, b.resource, b.condition.value)


@dataclass(frozen=True, slots=True)
class Comparison:
    """A scenario against the baseline: what changed in, and what changed out."""

    changed_inputs: tuple[Evidence, ...]
    changed_configuration: tuple[Evidence, ...]
    resources: tuple[ResourceDelta, ...]
    new_bottlenecks: tuple[tuple[str, str, str], ...]  # (node, resource, condition)
    resolved_bottlenecks: tuple[tuple[str, str, str], ...]

    @classmethod
    def between(
        cls, baseline: CapacityResult, scenario: CapacityResult, scenario_def: Scenario
    ) -> Comparison:
        inputs: list[Evidence] = []
        if scenario_def.multiplier is not None:
            inputs.append(Evidence("workload_multiplier", decimal_to_str(scenario_def.multiplier)))
        if scenario_def.target_rate is not None:
            inputs.append(Evidence("target_rate", str(scenario_def.target_rate)))
        if scenario_def.target_utilization is not None:
            inputs.append(Evidence("target_utilization", decimal_to_str(scenario_def.target_utilization)))
        changes = tuple(
            Evidence(f"{c.element_id}.{c.property}", decimal_to_str(c.value)) for c in scenario_def.changes
        )
        before = {(c.node_id, u.resource): u for c in baseline.components for u in c.utilization}
        after = {(c.node_id, u.resource): u for c in scenario.components for u in c.utilization}
        deltas = []
        for key in sorted(set(before) | set(after)):
            b, a = before.get(key), after.get(key)
            deltas.append(
                ResourceDelta(
                    key[0],
                    key[1],
                    b.demand.canonical if b and b.demand else None,
                    a.demand.canonical if a and a.demand else None,
                    b.capacity.canonical if b and b.capacity else None,
                    a.capacity.canonical if a and a.capacity else None,
                    b.ratio if b else None,
                    a.ratio if a else None,
                )
            )
        old = {_bottleneck_key(b) for b in baseline.bottlenecks}
        new = {_bottleneck_key(b) for b in scenario.bottlenecks}
        return cls(tuple(inputs), changes, tuple(deltas), tuple(sorted(new - old)), tuple(sorted(old - new)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_inputs": [e.to_dict() for e in self.changed_inputs],
            "changed_configuration": [e.to_dict() for e in self.changed_configuration],
            "resources": [d.to_dict() for d in self.resources],
            "new_bottlenecks": [list(k) for k in self.new_bottlenecks],
            "resolved_bottlenecks": [list(k) for k in self.resolved_bottlenecks],
        }


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: Scenario
    result: CapacityResult
    scaling: tuple[ScalingOption, ...]
    comparison: Comparison
    unsupported_scaling: tuple[Unsupported, ...] = ()  # constrained, but no model says how it scales

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.to_dict(),
            "result": self.result.to_dict(),
            "scaling": [s.to_dict() for s in self.scaling],
            "unsupported_scaling": [u.to_dict() for u in self.unsupported_scaling],
            "comparison": self.comparison.to_dict(),
        }
