"""A scenario: a hypothetical change or condition against one architecture revision — never an edit
of the architecture. It is an immutable snapshot, stored with the simulation, and applied to an
in-memory copy of the revision.

A scenario combines, and must contain at least one of:

- a **workload change** (the Capacity Engine's: a ``growth`` multiplier, compound ``growth_rate``
  per period over ``periods``, or an absolute ``target_rate`` with its unit; optionally another
  ``target_utilization``). It is validated by the Capacity Engine's own scenario rules;
- **configuration changes**: one known IR property of a named node or connection set to a value
  (or cleared with ``None``), e.g. ``replicas`` of ``api`` to 6. Units are part of the property's
  name (``cpu_limit_cores``, ``storage_bytes``, ``throughput_limit_per_second``) and the value is
  checked against the property's type and range here, and against the element's kind when applied;
- **failures**: a named component or connection unavailable, or every component declared in a zone
  or a region unavailable. A failure is a condition of the scenario, not a removal.

Conflicts are refused: a property changed twice, a failure listed twice, or a change on an element
the scenario also makes unavailable. Changes and failures are kept in a canonical order, so equal
scenarios have equal fingerprints.
"""

import builtins
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Self

from core.architecture_ir.configuration import (
    CONNECTION_PROPERTIES,
    NODE_PROPERTIES,
    REGION,
    ConfigValue,
    PropertySpec,
    ValueType,
)
from core.domain.capacity.errors import InvalidQuantity, InvalidScenario
from core.domain.capacity.scenarios import Scenario as CapacityScenario
from core.domain.capacity.units import Quantity
from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidSimulationRequest
from .values import FailureKind

MAX_CHANGES = 50
MAX_FAILURES = 50
MAX_ELEMENT_ID = 128
MAX_DESCRIPTION = 500
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


def _invalid(field: str, reason: str, **details: str) -> InvalidSimulationRequest:
    return InvalidSimulationRequest(details={"field": field, "reason": reason, **details})


def _known_fields(data: Mapping[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(str(k) for k in set(data) - known)
    if unknown:
        raise _invalid(f"{where}.{unknown[0]}", "unknown_field")


# --- workload --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkloadChange:
    """How the workload changes: validated, and later applied, by the Capacity Engine."""

    growth: Decimal | None = None
    growth_rate: Decimal | None = None
    periods: int | None = None
    target_rate: Quantity | None = None
    target_utilization: Decimal | None = None

    def __post_init__(self) -> None:
        fields = (self.growth, self.growth_rate, self.periods, self.target_rate, self.target_utilization)
        if all(f is None for f in fields):
            raise _invalid("scenario.workload", "empty")
        checked = self.capacity_scenario("workload")  # the Capacity Engine's rules, not a copy of them
        for name in ("growth", "growth_rate", "target_utilization"):
            object.__setattr__(self, name, getattr(checked, name))

    def capacity_scenario(self, name: str, changes: tuple[Any, ...] = ()) -> CapacityScenario:
        """This change as the Capacity Engine's scenario (with its configuration changes)."""
        try:
            return CapacityScenario(
                name=name,
                growth=self.growth,
                growth_rate=self.growth_rate,
                periods=self.periods,
                target_rate=self.target_rate,
                target_utilization=self.target_utilization,
                changes=changes,
            )
        except InvalidScenario as error:
            raise _invalid(f"scenario.workload.{error.details['field']}", error.details["reason"]) from None

    @property
    def multiplier(self) -> Decimal | None:
        return self.capacity_scenario("workload").multiplier

    def to_dict(self) -> dict[str, Any]:
        def number(value: Decimal | None) -> str | None:
            return decimal_to_str(value) if value is not None else None

        return {
            "growth": number(self.growth),
            "growth_rate": number(self.growth_rate),
            "periods": self.periods,
            "target_rate": self.target_rate.to_dict() if self.target_rate is not None else None,
            "target_utilization": number(self.target_utilization),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        known = {"growth", "growth_rate", "periods", "target_rate", "target_utilization"}
        if not isinstance(data, Mapping):
            raise _invalid("scenario.workload", "not_an_object")
        _known_fields(data, known, "scenario.workload")
        raw_target = data.get("target_rate")
        try:
            target = Quantity.from_dict(raw_target, "target_rate") if raw_target is not None else None
        except InvalidQuantity as error:
            raise _invalid(f"scenario.workload.{error.details['field']}", error.details["reason"]) from None
        return cls(
            growth=data.get("growth"),
            growth_rate=data.get("growth_rate"),
            periods=data.get("periods"),
            target_rate=target,
            target_utilization=data.get("target_utilization"),
        )


# --- configuration -------------------------------------------------------------------------------


def specs_of(name: object) -> tuple[PropertySpec, ...]:
    """The IR's declarations of a property (a node's, a connection's, or both)."""
    if not isinstance(name, str):
        return ()
    return tuple(s for s in (NODE_PROPERTIES.get(name), CONNECTION_PROPERTIES.get(name)) if s is not None)


def _read_value(value: object, specs: tuple[PropertySpec, ...]) -> object:
    """A JSON value as the IR types it: decimals from their text, lists as tuples."""
    if isinstance(value, list):
        return tuple(value)
    decimal = any(s.type is ValueType.DECIMAL for s in specs)
    if decimal and isinstance(value, str | int) and not isinstance(value, bool):
        try:
            return Decimal(str(value).strip())
        except ArithmeticError:
            return value
    return value


def _value_json(value: ConfigValue | None) -> Any:
    if isinstance(value, Decimal):
        return decimal_to_str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


@dataclass(frozen=True, slots=True)
class ConfigurationChange:
    """``property`` of the node or connection ``element_id`` set to ``value`` (``None``: cleared)
    for the scenario."""

    element_id: str
    property: str
    value: ConfigValue | None

    def __post_init__(self) -> None:
        if not isinstance(self.element_id, str) or not 0 < len(self.element_id) <= MAX_ELEMENT_ID:
            raise _invalid("scenario.changes.element_id", "invalid_reference")
        specs = specs_of(self.property)
        if not specs:
            raise _invalid("scenario.changes.property", "unknown_property", property=str(self.property))
        value = _read_value(self.value, specs)
        if isinstance(value, Decimal) and value.is_finite():
            value = value.normalize() + 0
        if value is not None and all(s.problems(value, self.property) for s in specs):
            raise _invalid("scenario.changes.value", "invalid_value", property=self.property)
        object.__setattr__(self, "value", value)

    @builtins.property  # the field "property" shadows the builtin in this class body
    def key(self) -> tuple[str, str]:
        return (self.element_id, self.property)

    def to_dict(self) -> dict[str, Any]:
        return {"element_id": self.element_id, "property": self.property, "value": _value_json(self.value)}

    @classmethod
    def from_dict(cls, data: object) -> Self:
        if not isinstance(data, Mapping) or set(data) != {"element_id", "property", "value"}:
            raise _invalid("scenario.changes", "invalid_change")
        return cls(data["element_id"], data["property"], data["value"])


# --- failures ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Failure:
    """An element, or every component of a declared zone or region, unavailable in the scenario."""

    kind: FailureKind
    target: str  # a node id, a connection id, a zone or a region

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FailureKind):
            raise _invalid("scenario.failures.kind", "unknown_kind")
        if self.kind in {FailureKind.ZONE, FailureKind.REGION}:
            if not isinstance(self.target, str) or not REGION.fullmatch(self.target):
                raise _invalid("scenario.failures.target", "invalid_value")
        elif not isinstance(self.target, str) or not 0 < len(self.target) <= MAX_ELEMENT_ID:
            raise _invalid("scenario.failures.target", "invalid_reference")

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind.value, self.target)

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "target": self.target}

    @classmethod
    def from_dict(cls, data: object) -> Self:
        if not isinstance(data, Mapping) or set(data) != {"kind", "target"}:
            raise _invalid("scenario.failures", "invalid_failure")
        try:
            kind = FailureKind(data["kind"])
        except ValueError:
            raise _invalid("scenario.failures.kind", "unknown_kind") from None
        return cls(kind, data["target"])


# --- the scenario --------------------------------------------------------------------------------


def _first_duplicate(keys: list[tuple[str, str]]) -> str:
    seen: set[tuple[str, str]] = set()
    for key in keys:
        if key in seen:
            return ".".join(key)
        seen.add(key)
    return ""


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str | None = None
    workload: WorkloadChange | None = None
    changes: tuple[ConfigurationChange, ...] = ()
    failures: tuple[Failure, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not NAME.fullmatch(self.name):
            raise _invalid("scenario.name", "invalid_name")
        if self.description is not None and (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description) > MAX_DESCRIPTION
        ):
            raise _invalid("scenario.description", "invalid_text")
        if self.workload is not None and not isinstance(self.workload, WorkloadChange):
            raise _invalid("scenario.workload", "not_an_object")
        self._check_items("changes", self.changes, ConfigurationChange, MAX_CHANGES)
        self._check_items("failures", self.failures, Failure, MAX_FAILURES)
        if self.workload is None and not self.changes and not self.failures:
            raise _invalid("scenario", "empty")
        unavailable = {
            f.target for f in self.failures if f.kind in {FailureKind.COMPONENT, FailureKind.CONNECTION}
        }
        for change in self.changes:
            if change.element_id in unavailable:
                raise _invalid("scenario.changes", "changed_and_unavailable", element_id=change.element_id)
        object.__setattr__(self, "changes", tuple(sorted(self.changes, key=lambda c: c.key)))
        object.__setattr__(self, "failures", tuple(sorted(self.failures, key=lambda f: f.key)))

    @staticmethod
    def _check_items(field: str, items: object, kind: type, limit: int) -> None:
        if not isinstance(items, tuple) or len(items) > limit:
            raise _invalid(f"scenario.{field}", "too_many")
        if not all(isinstance(i, kind) for i in items):
            raise _invalid(f"scenario.{field}", "invalid_item")
        keys = [i.key for i in items]
        if len(keys) != len(set(keys)):
            raise _invalid(f"scenario.{field}", "duplicate", element_id=_first_duplicate(keys))

    @property
    def fingerprint(self) -> str:
        """Identifies what the scenario asks, whatever its name and description."""
        content = {k: v for k, v in self.to_dict().items() if k not in {"name", "description"}}
        return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "workload": self.workload.to_dict() if self.workload is not None else None,
            "changes": [c.to_dict() for c in self.changes],
            "failures": [f.to_dict() for f in self.failures],
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        if not isinstance(data, Mapping):
            raise _invalid("scenario", "not_an_object")
        _known_fields(data, {"name", "description", "workload", "changes", "failures"}, "scenario")
        changes, failures = data.get("changes") or [], data.get("failures") or []
        for field, items, limit in (("changes", changes, MAX_CHANGES), ("failures", failures, MAX_FAILURES)):
            if not isinstance(items, list) or len(items) > limit:
                raise _invalid(f"scenario.{field}", "too_many")
        workload = data.get("workload")
        return cls(
            name=data.get("name"),  # type: ignore[arg-type]
            description=data.get("description"),
            workload=WorkloadChange.from_dict(workload) if workload is not None else None,
            changes=tuple(ConfigurationChange.from_dict(c) for c in changes),
            failures=tuple(Failure.from_dict(f) for f in failures),
        )
