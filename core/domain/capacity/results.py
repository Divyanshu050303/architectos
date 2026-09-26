"""What a capacity analysis produces, and where every number comes from.

Every value carries its **source**:

- ``measured``: observed on the running system (no source provides these yet);
- ``declared``: stated in the architecture (e.g. a throughput limit an architect entered);
- ``catalog``: from the component catalog (none exists yet);
- ``model_estimate``: calculated by a named, versioned model from other values;
- ``assumed``: taken from a workload assumption;
- ``unknown``: not available. An unknown value has no number: it is never zero, never a guess.

Estimates are not measurements: a ``model_estimate`` says what a documented model derives from its
inputs, nothing about how the system behaves in production.

Utilization is ``demand / capacity`` in the same unit, as an exact ratio (not a percentage, never
clamped): 1.5 means demand is 50 % above capacity. When capacity is zero and demand is not, there
is no ratio: the state is ``no_capacity``. When either side is unknown, the ratio is unknown.
Headroom is ``capacity - demand`` (negative when demand exceeds capacity) and relative headroom
``1 - utilization``; with a target utilization ``t``, headroom to target is ``t * capacity - demand``.

Everything is immutable and ordered canonically; ``CapacityResult.fingerprint`` hashes the
deterministic content only, so equal inputs and model versions give equal fingerprints.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from core.domain.requirements.value_objects import decimal_to_str

from .errors import InvalidCapacityResult, InvalidQuantity
from .units import Quantity

MODEL_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")  # e.g. "declared-throughput"
RESOURCE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")  # e.g. "request_rate", "connections"
CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_TEXT = 2000
MAX_ID = 256
MAX_ITEMS = 200
# Resources whose demand grows in proportion to the workload rate (every traffic multiplier is a
# fixed ratio), so capacity / demand is the workload multiple at which they saturate. Connections
# (pools are fixed) and time_to_full are not.
LINEAR_RESOURCES = frozenset({"work_rate", "cpu", "bandwidth", "storage", "storage_growth"})


class Source(StrEnum):
    MEASURED = "measured"
    DECLARED = "declared"
    CATALOG = "catalog"
    MODEL_ESTIMATE = "model_estimate"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class AnalysisStatus(StrEnum):
    """What an analysis established overall (execution states in analyses.py)."""

    COMPLETED = "completed"  # every component in scope has estimates
    PARTIAL = "partial"  # some do, some lack inputs or a model
    INSUFFICIENT_INPUT = "insufficient_input"  # models apply, but inputs are missing everywhere
    UNSUPPORTED = "unsupported"  # no model applies to any component in scope
    FAILED = "failed"  # the analysis could not run (no result)


class ComponentStatus(StrEnum):
    ESTIMATED = "estimated"
    INSUFFICIENT_INPUT = "insufficient_input"
    UNSUPPORTED = "unsupported"


class UtilizationState(StrEnum):
    BELOW = "below"  # demand < capacity
    AT = "at"  # demand == capacity
    ABOVE = "above"  # demand > capacity
    NO_CAPACITY = "no_capacity"  # capacity is 0 and demand is not
    IDLE = "idle"  # no demand reaches it
    UNKNOWN = "unknown"  # demand or capacity unknown


class Certainty(StrEnum):
    MODELED = "modeled"  # demand and capacity both known: the model says it is constrained
    CANDIDATE = "candidate"  # the evidence is incomplete: worth investigating, not established


class BottleneckCondition(StrEnum):
    EXCEEDS_CAPACITY = "exceeds_capacity"
    AT_CAPACITY = "at_capacity"
    ABOVE_TARGET = "above_target"
    NO_CAPACITY = "no_capacity"
    UNKNOWN_CAPACITY = "unknown_capacity"  # on a path demand reaches, capacity unknown


def _check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidCapacityResult(details={"fields": found})


def _text(value: object, name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    ok = isinstance(value, str) and (value.strip() or not required) and len(value) <= MAX_TEXT
    return None if ok else name


def _ids(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return name
    return None if all(isinstance(v, str) and 0 < len(v) <= MAX_ID for v in values) else name


def _pattern(value: object, pattern: re.Pattern[str], name: str) -> str | None:
    return None if isinstance(value, str) and pattern.fullmatch(value) else name


def _number(value: Decimal | None) -> str | None:
    return decimal_to_str(value) if value is not None else None


def _quantity(value: Quantity | None) -> dict[str, str] | None:
    return value.to_dict() if value is not None else None


def _read_quantity(data: Any) -> Quantity | None:
    try:
        return Quantity.from_dict(data) if data is not None else None
    except InvalidQuantity:
        raise InvalidCapacityResult(details={"fields": ["quantity"]}) from None


def _read_decimal(data: Any) -> Decimal | None:
    return Decimal(data) if data is not None else None


@dataclass(frozen=True, slots=True)
class Evidence:
    label: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "value": self.value}


def _evidence(values: object) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return "evidence"
    ok = all(
        isinstance(e, Evidence) and isinstance(e.label, str) and isinstance(e.value, str) for e in values
    )
    return None if ok else "evidence"


def _read_evidence(data: Any) -> tuple[Evidence, ...]:
    return tuple(Evidence(e["label"], e["value"]) for e in data or ())


@dataclass(frozen=True, slots=True)
class Estimate:
    """One value about one element: a capacity limit, a resource amount or a demand. ``quantity``
    is None exactly when ``source`` is ``unknown``; ``basis`` says how it was obtained (the formula,
    or the property it was declared in); ``missing`` names the inputs an unknown value lacks."""

    element_id: str
    resource: str
    quantity: Quantity | None
    source: Source
    basis: str
    model_id: str | None = None
    model_version: int | None = None
    inputs: tuple[Evidence, ...] = ()
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "missing",
            tuple(sorted(set(self.missing))) if isinstance(self.missing, tuple) else self.missing,
        )
        unknown = self.source is Source.UNKNOWN
        _check(
            [
                None
                if isinstance(self.element_id, str) and 0 < len(self.element_id) <= MAX_ID
                else "element_id",
                _pattern(self.resource, RESOURCE, "resource"),
                None if isinstance(self.source, Source) else "source",
                None if (self.quantity is None) == unknown else "quantity",
                None if self.quantity is None or isinstance(self.quantity, Quantity) else "quantity",
                _text(self.basis, "basis"),
                None if self.model_id is None or MODEL_ID.fullmatch(self.model_id) else "model_id",
                None
                if (self.model_version is None) == (self.model_id is None)
                and (
                    self.model_version is None
                    or (isinstance(self.model_version, int) and self.model_version >= 1)
                )
                else "model_version",
                None if self.source is not Source.MODEL_ESTIMATE or self.model_id else "model_id",
                _evidence(self.inputs),
                _ids(self.missing, "missing"),
            ]
        )

    @property
    def known(self) -> bool:
        return self.quantity is not None

    def sort_key(self) -> tuple[str, str]:
        return (self.element_id, self.resource)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "resource": self.resource,
            "quantity": _quantity(self.quantity),
            "source": self.source.value,
            "basis": self.basis,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "inputs": [e.to_dict() for e in self.inputs],
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                element_id=data["element_id"],
                resource=data["resource"],
                quantity=_read_quantity(data.get("quantity")),
                source=Source(data["source"]),
                basis=data["basis"],
                model_id=data.get("model_id"),
                model_version=data.get("model_version"),
                inputs=_read_evidence(data.get("inputs")),
                missing=tuple(data.get("missing") or ()),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class Demand:
    """Demand reaching one node or connection, with how it got there: the path from the workload's
    entry, and every factor applied on the way (traffic shares, fan-out, cache hits, read/write)."""

    element_id: str
    resource: str
    quantity: Quantity
    path: tuple[str, ...]
    factors: tuple[Evidence, ...] = ()
    source: Source = Source.MODEL_ESTIMATE

    def __post_init__(self) -> None:
        _check(
            [
                None
                if isinstance(self.element_id, str) and 0 < len(self.element_id) <= MAX_ID
                else "element_id",
                _pattern(self.resource, RESOURCE, "resource"),
                None if isinstance(self.quantity, Quantity) else "quantity",
                _ids(self.path, "path"),
                _evidence(self.factors),
                None if self.source in (Source.MODEL_ESTIMATE, Source.ASSUMED, Source.MEASURED) else "source",
            ]
        )

    def sort_key(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.element_id, self.resource, self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "resource": self.resource,
            "quantity": self.quantity.to_dict(),
            "path": list(self.path),
            "factors": [e.to_dict() for e in self.factors],
            "source": self.source.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            quantity = _read_quantity(data["quantity"])
            if quantity is None:
                raise ValueError("quantity")
            return cls(
                data["element_id"],
                data["resource"],
                quantity,
                tuple(data["path"]),
                _read_evidence(data.get("factors")),
                Source(data.get("source", Source.MODEL_ESTIMATE.value)),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class Utilization:
    """Demand against capacity for one resource of one node, in one unit."""

    resource: str
    demand: Quantity | None
    capacity: Quantity | None
    target: Decimal | None = None  # the target utilization in force, if any

    def __post_init__(self) -> None:
        _check(
            [
                _pattern(self.resource, RESOURCE, "resource"),
                None
                if self.demand is None
                or self.capacity is None
                or self.demand.dimension is self.capacity.dimension
                else "capacity",
                None if self.target is None or Decimal(0) < self.target <= 1 else "target",
            ]
        )

    @property
    def state(self) -> UtilizationState:
        if self.demand is not None and self.demand.value == 0:
            return UtilizationState.IDLE
        if self.demand is None or self.capacity is None:
            return UtilizationState.UNKNOWN
        demand, capacity = self.demand.canonical, self.capacity.canonical
        if capacity == 0:
            return UtilizationState.NO_CAPACITY
        if demand < capacity:
            return UtilizationState.BELOW
        return UtilizationState.AT if demand == capacity else UtilizationState.ABOVE

    @property
    def ratio(self) -> Decimal | None:
        """demand / capacity, exact to 9 decimal places; None when there is no ratio."""
        if self.demand is None or self.capacity is None or self.capacity.canonical == 0:
            return None
        return _round(self.demand.canonical / self.capacity.canonical)

    @property
    def headroom(self) -> Decimal | None:
        """capacity - demand, in the canonical unit (negative when exceeded)."""
        if self.demand is None or self.capacity is None:
            return None
        return _round(self.capacity.canonical - self.demand.canonical)

    @property
    def relative_headroom(self) -> Decimal | None:
        ratio = self.ratio
        return None if ratio is None else 1 - ratio

    @property
    def headroom_to_target(self) -> Decimal | None:
        """target * capacity - demand, in the canonical unit; None without a target."""
        if self.target is None or self.demand is None or self.capacity is None:
            return None
        return _round(self.target * self.capacity.canonical - self.demand.canonical)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "demand": _quantity(self.demand),
            "capacity": _quantity(self.capacity),
            "target": _number(self.target),
            "state": self.state.value,
            "ratio": _number(self.ratio),
            "headroom": _number(self.headroom),
            "relative_headroom": _number(self.relative_headroom),
            "headroom_to_target": _number(self.headroom_to_target),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["resource"],
                _read_quantity(data.get("demand")),
                _read_quantity(data.get("capacity")),
                _read_decimal(data.get("target")),
            )
        except (KeyError, ValueError, TypeError, ArithmeticError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1e-9")).normalize() + 0


@dataclass(frozen=True, slots=True)
class ComponentResult:
    """Everything the analysis established about one node."""

    node_id: str
    status: ComponentStatus
    models: tuple[tuple[str, int], ...] = ()  # (id, version) of every model that applied
    demand: tuple[Demand, ...] = ()
    limits: tuple[Estimate, ...] = ()  # capacity limits
    resources: tuple[Estimate, ...] = ()  # resource amounts (CPU, connections, storage, …)
    utilization: tuple[Utilization, ...] = ()
    missing: tuple[str, ...] = ()  # inputs a model needed and did not get
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("demand", "limits", "resources"):
            items = getattr(self, name)
            if isinstance(items, tuple):
                object.__setattr__(self, name, tuple(sorted(items, key=lambda i: i.sort_key())))
        if isinstance(self.utilization, tuple):
            object.__setattr__(self, "utilization", tuple(sorted(self.utilization, key=lambda u: u.resource)))
        if isinstance(self.missing, tuple):
            object.__setattr__(self, "missing", tuple(sorted(set(self.missing))))
        if isinstance(self.models, tuple):
            object.__setattr__(self, "models", tuple(sorted(set(self.models))))
        _check(
            [
                None if isinstance(self.node_id, str) and 0 < len(self.node_id) <= MAX_ID else "node_id",
                None if isinstance(self.status, ComponentStatus) else "status",
                None
                if isinstance(self.models, tuple)
                and all(
                    isinstance(m, tuple)
                    and len(m) == 2
                    and isinstance(m[0], str)
                    and MODEL_ID.fullmatch(m[0])
                    and isinstance(m[1], int)
                    and m[1] >= 1
                    for m in self.models
                )
                else "models",
                None if (self.status is ComponentStatus.UNSUPPORTED) == (not self.models) else "models",
                None if self.status is not ComponentStatus.INSUFFICIENT_INPUT or self.missing else "missing",
                _ids(self.missing, "missing"),
                None
                if isinstance(self.notes, tuple) and all(_text(n, "n") is None for n in self.notes)
                else "notes",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status.value,
            "models": [list(m) for m in self.models],
            "demand": [d.to_dict() for d in self.demand],
            "limits": [e.to_dict() for e in self.limits],
            "resources": [e.to_dict() for e in self.resources],
            "utilization": [u.to_dict() for u in self.utilization],
            "missing": list(self.missing),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                node_id=data["node_id"],
                status=ComponentStatus(data["status"]),
                models=tuple((m[0], m[1]) for m in data.get("models") or ()),
                demand=tuple(Demand.from_dict(d) for d in data.get("demand") or ()),
                limits=tuple(Estimate.from_dict(e) for e in data.get("limits") or ()),
                resources=tuple(Estimate.from_dict(e) for e in data.get("resources") or ()),
                utilization=tuple(Utilization.from_dict(u) for u in data.get("utilization") or ()),
                missing=tuple(data.get("missing") or ()),
                notes=tuple(data.get("notes") or ()),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None


_CONDITION_ORDER = {c: i for i, c in enumerate(BottleneckCondition)}


@dataclass(frozen=True, slots=True)
class Bottleneck:
    """A component or resource that constrains, or may constrain, the architecture. ``certainty``
    is ``modeled`` only when demand and capacity are both known; otherwise it is a candidate."""

    node_id: str
    resource: str
    condition: BottleneckCondition
    certainty: Certainty
    utilization: Utilization
    explanation: str
    remediation: str
    evidence: tuple[Evidence, ...] = ()
    assumptions: tuple[str, ...] = ()  # keys of the workload assumptions it rests on

    def __post_init__(self) -> None:
        if isinstance(self.assumptions, tuple):
            object.__setattr__(self, "assumptions", tuple(sorted(set(self.assumptions))))
        known = self.utilization.demand is not None and self.utilization.capacity is not None
        _check(
            [
                None if isinstance(self.node_id, str) and 0 < len(self.node_id) <= MAX_ID else "node_id",
                _pattern(self.resource, RESOURCE, "resource"),
                None if isinstance(self.condition, BottleneckCondition) else "condition",
                None if isinstance(self.certainty, Certainty) else "certainty",
                None if (self.certainty is Certainty.MODELED) == known else "certainty",
                _text(self.explanation, "explanation"),
                _text(self.remediation, "remediation"),
                _evidence(self.evidence),
                _ids(self.assumptions, "assumptions"),
            ]
        )

    def sort_key(self) -> tuple[Any, ...]:
        """Modeled before candidates, then by condition, then the highest utilization first."""
        ratio = self.utilization.ratio
        return (
            self.certainty is not Certainty.MODELED,
            _CONDITION_ORDER[self.condition],
            -(ratio if ratio is not None else Decimal(0)),
            self.node_id,
            self.resource,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "resource": self.resource,
            "condition": self.condition.value,
            "certainty": self.certainty.value,
            "utilization": self.utilization.to_dict(),
            "explanation": self.explanation,
            "remediation": self.remediation,
            "evidence": [e.to_dict() for e in self.evidence],
            "assumptions": list(self.assumptions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["node_id"],
                data["resource"],
                BottleneckCondition(data["condition"]),
                Certainty(data["certainty"]),
                Utilization.from_dict(data["utilization"]),
                data["explanation"],
                data["remediation"],
                _read_evidence(data.get("evidence")),
                tuple(data.get("assumptions") or ()),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class Unsupported:
    """A calculation the analysis could not make, and why: no model for a component, missing
    inputs, or a topology whose traffic semantics are not defined (e.g. a synchronous cycle)."""

    element_id: str
    code: str
    message: str
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.missing, tuple):
            object.__setattr__(self, "missing", tuple(sorted(set(self.missing))))
        _check(
            [
                None
                if isinstance(self.element_id, str) and 0 < len(self.element_id) <= MAX_ID
                else "element_id",
                _pattern(self.code, CODE, "code"),
                _text(self.message, "message"),
                _ids(self.missing, "missing"),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "code": self.code,
            "message": self.message,
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(data["element_id"], data["code"], data["message"], tuple(data.get("missing") or ()))
        except (KeyError, TypeError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class Limitation:
    """Something no model of the analysis could establish (e.g. no catalog, no measurements)."""

    code: str
    message: str

    def __post_init__(self) -> None:
        _check([_pattern(self.code, CODE, "code"), _text(self.message, "message")])

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(data["code"], data["message"])
        except (KeyError, TypeError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class ModelSet:
    """Which models ran: a version derived from their ids and versions."""

    version: str
    models: tuple[tuple[str, int], ...] = ()

    @classmethod
    def of(cls, models: Iterable[tuple[str, int]]) -> ModelSet:
        ordered = tuple(sorted(set(models)))
        return cls(hashlib.sha256(json.dumps(ordered).encode()).hexdigest()[:16], ordered)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "models": [list(m) for m in self.models]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelSet:
        return cls(data["version"], tuple((m[0], m[1]) for m in data["models"]))


@dataclass(frozen=True, slots=True)
class Summary:
    """Counts only, derived from the result."""

    components: Mapping[str, int]  # per component status
    bottlenecks: Mapping[str, int]  # per certainty
    unsupported: int
    highest_utilization: Decimal | None  # the largest known ratio, if any
    # The smallest capacity / demand over resources that grow with the workload: the workload
    # multiple at which the first known limit is reached. Complete only when every component the
    # workload reaches has a known throughput utilization; otherwise an unknown limit may come first.
    saturation_multiple: Decimal | None = None
    saturation_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": dict(self.components),
            "bottlenecks": dict(self.bottlenecks),
            "unsupported": self.unsupported,
            "highest_utilization": _number(self.highest_utilization),
            "saturation_multiple": _number(self.saturation_multiple),
            "saturation_complete": self.saturation_complete,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Summary:
        return cls(
            data["components"],
            data["bottlenecks"],
            data["unsupported"],
            _read_decimal(data.get("highest_utilization")),
            _read_decimal(data.get("saturation_multiple")),
            bool(data.get("saturation_complete", False)),
        )


def derive_status(components: Iterable[ComponentResult]) -> AnalysisStatus:
    """completed: every component in scope is estimated; partial: some are; insufficient_input:
    none is, and at least one lacked inputs for a model that applies; unsupported: no model
    applies to any (or nothing is in scope)."""
    statuses = Counter(c.status for c in components)
    estimated = statuses[ComponentStatus.ESTIMATED]
    if estimated and estimated == sum(statuses.values()):
        return AnalysisStatus.COMPLETED
    if estimated:
        return AnalysisStatus.PARTIAL
    if statuses[ComponentStatus.INSUFFICIENT_INPUT]:
        return AnalysisStatus.INSUFFICIENT_INPUT
    return AnalysisStatus.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class CapacityResult:
    """The engine's output for one revision, workload, model set and configuration."""

    model_set: ModelSet
    context_fingerprint: str  # identifies the inputs besides the IR (workload, settings, assumptions)
    components: tuple[ComponentResult, ...] = ()
    connections: tuple[Demand, ...] = ()  # demand carried by each connection
    bottlenecks: tuple[Bottleneck, ...] = ()
    unsupported: tuple[Unsupported, ...] = ()
    limitations: tuple[Limitation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", tuple(sorted(self.components, key=lambda c: c.node_id)))
        object.__setattr__(self, "connections", tuple(sorted(self.connections, key=Demand.sort_key)))
        object.__setattr__(self, "bottlenecks", tuple(sorted(self.bottlenecks, key=Bottleneck.sort_key)))
        object.__setattr__(
            self, "unsupported", tuple(sorted(set(self.unsupported), key=lambda u: (u.element_id, u.code)))
        )
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations), key=lambda x: x.code)))
        ids = [c.node_id for c in self.components]
        _check([None if len(ids) == len(set(ids)) else "components"])

    @property
    def status(self) -> AnalysisStatus:
        return derive_status(self.components)

    @property
    def summary(self) -> Summary:
        statuses = Counter(c.status.value for c in self.components)
        certainty = Counter(b.certainty.value for b in self.bottlenecks)
        ratios = [u.ratio for c in self.components for u in c.utilization if u.ratio is not None]
        multiples = [
            _round(u.capacity.canonical / u.demand.canonical)
            for c in self.components
            for u in c.utilization
            if u.resource in LINEAR_RESOURCES
            and u.demand is not None
            and u.capacity is not None
            and u.demand.canonical > 0
        ]
        reached = [
            c
            for c in self.components
            if any(d.quantity.canonical > 0 for d in c.demand) or c.node_id in self._incomplete()
        ]
        complete = bool(reached) and all(
            any(u.resource == "work_rate" and u.ratio is not None for u in c.utilization) for c in reached
        )
        return Summary(
            components={s.value: statuses.get(s.value, 0) for s in ComponentStatus},
            bottlenecks={c.value: certainty.get(c.value, 0) for c in Certainty},
            unsupported=len(self.unsupported),
            highest_utilization=max(ratios) if ratios else None,
            saturation_multiple=min(multiples) if multiples else None,
            saturation_complete=complete
            and not self._incomplete()
            and not any(u.code == "no_entry" for u in self.unsupported),
        )

    def _incomplete(self) -> set[str]:
        """Nodes whose demand is not fully known (the analysis reports each as unsupported)."""
        return {u.element_id for u in self.unsupported if u.code in {"demand_incomplete", "cyclic_traffic"}}

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_set": self.model_set.to_dict(),
            "context_fingerprint": self.context_fingerprint,
            "status": self.status.value,
            "components": [c.to_dict() for c in self.components],
            "connections": [d.to_dict() for d in self.connections],
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
            "unsupported": [u.to_dict() for u in self.unsupported],
            "limitations": [x.to_dict() for x in self.limitations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapacityResult:
        try:
            return cls(
                ModelSet.from_dict(data["model_set"]),
                data["context_fingerprint"],
                tuple(ComponentResult.from_dict(c) for c in data.get("components") or ()),
                tuple(Demand.from_dict(d) for d in data.get("connections") or ()),
                tuple(Bottleneck.from_dict(b) for b in data.get("bottlenecks") or ()),
                tuple(Unsupported.from_dict(u) for u in data.get("unsupported") or ()),
                tuple(Limitation.from_dict(x) for x in data.get("limitations") or ()),
            )
        except (KeyError, TypeError, IndexError) as error:
            raise InvalidCapacityResult(details={"fields": [type(error).__name__]}) from None

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the deterministic content: equal inputs, equal fingerprint."""
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
