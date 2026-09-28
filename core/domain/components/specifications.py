"""A component specification: the machine-readable description of one technology (or one provider's
variant of it), versioned, every claim with its provenance.

- **Identity.** ``id`` is the catalog path an architecture node refers to (``Node.component``,
  e.g. ``databases/postgresql``): the category's directory, then the entry. ``technology`` is the IR
  technology name it describes. ``aliases`` are for display and search only — a node is linked to a
  specification by its ``component`` path, never by matching names.
- **Version.** ``version`` counts the specification's revisions: a change of any claim is a new
  version, and the previous version stays readable. ``content_hash`` identifies the exact content;
  ``ref`` (``databases/postgresql@2``) is what an analysis records having used.
- **Claims.** Capabilities, configuration fields, capacity dimensions, scaling methods, failure
  modes, signals, security properties, billing dimensions and operational considerations — each
  with a ``Provenance``. Documented claims cite the specification's ``sources``.
- **Configuration fields** are Architecture IR properties (``NODE_PROPERTIES``): the specification
  says which of them matter for this technology, never defines a property of its own, so there is
  one configuration model. A default is stated only when documented; otherwise it stays unknown.
- **Capacity dimensions** say what can be measured and in which unit and scope; a value is stored
  only with its basis (documented, measured or estimated, with its assumptions) — a generic
  specification states no throughput.
- **Support status** is explicit: ``planned`` entries claim nothing, ``partial`` ones at least their
  capabilities, ``supported`` ones their capabilities, configuration, failure modes, signals and
  security properties.
"""

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from functools import partial
from typing import Any, Self

from core.architecture_ir.component import COMPONENT_REFERENCE, TECHNOLOGY_NAME, NodeKind
from core.architecture_ir.configuration import (
    CONNECTION_PROPERTIES,
    NODE_PROPERTIES,
    ConfigValue,
    ValueType,
)
from core.domain.capacity.units import UNITS
from core.domain.cost.pricing import PricingUnit

from .capabilities import (
    SCALING_METHODS,
    SECURITY_PROPERTIES,
    Capability,
    CapabilityState,
    state_problems,
)
from .entities import (
    CATEGORIES,
    MAX_ITEMS,
    MAX_NAME,
    Engine,
    Hosting,
    Provenance,
    ProvenanceKind,
    Source,
    SupportStatus,
    as_tuple,
    check,
    code,
    items,
    read_provenance,
    strict,
    text,
    texts,
    unique,
    within,
)
from .errors import InvalidSpecification

VALUED = frozenset({ProvenanceKind.DOCUMENTED, ProvenanceKind.MEASURED, ProvenanceKind.ESTIMATED})


def _config_value(value: object, prop: str) -> ConfigValue:
    """A configuration value as the IR types it: decimals from their text, lists as tuples."""
    spec = NODE_PROPERTIES[prop]
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, float):
        raise TypeError("floats are not exact")
    if spec.type is ValueType.DECIMAL and isinstance(value, str | int) and not isinstance(value, bool):
        return Decimal(str(value).strip())
    return value  # type: ignore[return-value]  # checked by the property's specification


def _json_value(value: ConfigValue | Decimal | None) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool | float):
        raise TypeError("not an exact number")
    return Decimal(str(value).strip())


def _known(provenance: object) -> str | None:
    """A structural claim (a failure mode, a signal, a billing dimension) is stated on evidence."""
    return None if isinstance(provenance, Provenance) and provenance.is_known else "provenance"


class Scope(StrEnum):
    """What a capacity value is per."""

    INSTANCE = "instance"
    NODE = "node"
    PARTITION = "partition"
    SHARD = "shard"
    CLUSTER = "cluster"
    SERVICE = "service"
    ACCOUNT = "account"
    QUEUE = "queue"
    TOPIC = "topic"
    ITEM = "item"
    MESSAGE = "message"
    REQUEST = "request"
    FUNCTION = "function"


class SignalType(StrEnum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    HEALTH_CHECK = "health_check"
    EVENT = "event"


class SignalAvailability(StrEnum):
    """How the technology makes a signal available — not whether a deployment collects it."""

    NATIVE = "native"
    REQUIRES_INSTRUMENTATION = "requires_instrumentation"
    REQUIRES_EXTERNAL = "requires_external"


class Effect(StrEnum):
    """What a scaling method may change besides capacity."""

    CONSISTENCY = "consistency"
    LATENCY = "latency"
    COST = "cost"
    AVAILABILITY = "availability"
    OPERATIONS = "operations"


class OperationArea(StrEnum):
    BACKUP_RESTORE = "backup_restore"
    UPGRADES = "upgrades"
    PATCHING = "patching"
    REPLICATION = "replication"
    REBALANCING = "rebalancing"
    SCALING = "scaling"
    MONITORING = "monitoring"
    DISASTER_RECOVERY = "disaster_recovery"
    CAPACITY_PLANNING = "capacity_planning"
    DEPENDENCIES = "dependencies"


class Responsibility(StrEnum):
    PROVIDER = "provider"
    OPERATOR = "operator"
    SHARED = "shared"


@dataclass(frozen=True, slots=True)
class Provider:
    name: str  # e.g. "community", "aws", "google", "azure"
    service: str  # the product's name at that provider

    def __post_init__(self) -> None:
        check([code(self.name, "name"), text(self.service, "service", MAX_NAME)])

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "service": self.service}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(data["name"], data["service"])


@dataclass(frozen=True, slots=True)
class ConfigurationField:
    """An Architecture IR property that matters for this technology."""

    property: str  # a node property of the IR (its unit is part of its name)
    provenance: Provenance  # the default's: unknown when no default is documented
    required: bool = False  # the technology's analysis needs it stated
    user_configurable: bool = True
    engines: tuple[Engine, ...] = ()  # the engines that read it
    default: ConfigValue | None = None  # only when documented for this technology
    description: str | None = None

    def __post_init__(self) -> None:
        spec = NODE_PROPERTIES.get(self.property) if isinstance(self.property, str) else None
        wrong_default = (
            self.default is not None and spec is not None and spec.problems(self.default, "default")
        )
        provenance = self.provenance if isinstance(self.provenance, Provenance) else None
        check(
            [
                None if spec is not None else "property",
                None if provenance is not None else "provenance",
                None if isinstance(self.required, bool) else "required",
                None if isinstance(self.user_configurable, bool) else "user_configurable",
                items(self.engines, Engine, "engines"),
                "default" if wrong_default else None,
                # a default is a documented fact, or there is none
                "provenance"
                if provenance
                and self.default is not None
                and provenance.kind is not ProvenanceKind.DOCUMENTED
                else None,
                "provenance" if provenance and self.default is None and provenance.is_known else None,
                text(self.description, "description", required=False),
            ]
        )
        object.__setattr__(self, "engines", tuple(sorted(set(self.engines))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "property": self.property,
            "required": self.required,
            "user_configurable": self.user_configurable,
            "engines": [e.value for e in self.engines],
            "default": _json_value(self.default),
            "description": self.description,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        prop = data["property"]
        default = data.get("default")
        return cls(
            prop,
            read_provenance(data) if "provenance" in data else Provenance.unknown(),
            data.get("required", False),
            data.get("user_configurable", True),
            tuple(Engine(e) for e in as_tuple(data.get("engines"))),
            None if default is None or prop not in NODE_PROPERTIES else _config_value(default, prop),
            data.get("description"),
        )


@dataclass(frozen=True, slots=True)
class CapacityDimension:
    """Something that can be measured about the component, in a unit and a scope. With a value, the
    value's basis; without one, the IR property an architecture states it in (or nothing: unknown)."""

    id: str
    unit: str  # a capacity unit (core/domain/capacity/units.py)
    scope: Scope
    provenance: Provenance
    value: Decimal | None = None
    property: str | None = None  # the IR property holding the configured value
    assumptions: tuple[str, ...] = ()  # the workload the value holds for
    description: str | None = None

    def __post_init__(self) -> None:
        provenance = self.provenance if isinstance(self.provenance, Provenance) else None
        valued = self.value is not None and provenance is not None
        check(
            [
                code(self.id, "id"),
                None if self.unit in UNITS else "unit",
                None if isinstance(self.scope, Scope) else "scope",
                None if provenance is not None else "provenance",
                None
                if self.value is None or (isinstance(self.value, Decimal) and self.value >= 0)
                else "value",
                None if self.property is None or self.property in NODE_PROPERTIES else "property",
                texts(self.assumptions, "assumptions"),
                text(self.description, "description", required=False),
                # a value is documented, measured or estimated — never inferred or assumed
                "provenance" if valued and provenance and provenance.kind not in VALUED else None,
                "assumptions"
                if valued
                and provenance
                and provenance.kind is ProvenanceKind.ESTIMATED
                and not self.assumptions
                else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "unit": self.unit,
            "scope": self.scope.value,
            "value": _json_value(self.value),
            "property": self.property,
            "assumptions": list(self.assumptions),
            "description": self.description,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            data["id"],
            data["unit"],
            Scope(data["scope"]),
            read_provenance(data),
            _decimal(data.get("value")),
            data.get("property"),
            as_tuple(data.get("assumptions")),
            data.get("description"),
        )


@dataclass(frozen=True, slots=True)
class ScalingMethod:
    """A way the technology scales, what it needs and changes — never assumed available or
    beneficial. ``evaluated_by`` names the engines that can evaluate it (none: ArchitectOS cannot)."""

    id: str  # SCALING_METHODS
    state: CapabilityState
    provenance: Provenance
    requires: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    affects: tuple[Effect, ...] = ()
    evaluated_by: tuple[Engine, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                None if self.id in SCALING_METHODS else "id",
                texts(self.requires, "requires", MAX_NAME),
                texts(self.limitations, "limitations"),
                items(self.affects, Effect, "affects"),
                items(self.evaluated_by, Engine, "evaluated_by"),
                *state_problems(self.state, self.provenance, self.requires),
            ]
        )
        object.__setattr__(self, "affects", tuple(sorted(set(self.affects))))
        object.__setattr__(self, "evaluated_by", tuple(sorted(set(self.evaluated_by))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "requires": list(self.requires),
            "limitations": list(self.limitations),
            "affects": [a.value for a in self.affects],
            "evaluated_by": [e.value for e in self.evaluated_by],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            data["id"],
            CapabilityState(data["state"]),
            read_provenance(data),
            as_tuple(data.get("requires")),
            as_tuple(data.get("limitations")),
            tuple(Effect(a) for a in as_tuple(data.get("affects"))),
            tuple(Engine(e) for e in as_tuple(data.get("evaluated_by"))),
        )


@dataclass(frozen=True, slots=True)
class FailureMode:
    """A known way the component fails — a possibility with its preconditions, never a prediction."""

    id: str
    description: str
    impact: str
    provenance: Provenance
    preconditions: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()  # ids of the specification's signals that show it
    mitigations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                code(self.id, "id"),
                text(self.description, "description"),
                text(self.impact, "impact"),
                _known(self.provenance),
                texts(self.preconditions, "preconditions"),
                items(self.signals, str, "signals"),
                texts(self.mitigations, "mitigations"),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "impact": self.impact,
            "preconditions": list(self.preconditions),
            "signals": list(self.signals),
            "mitigations": list(self.mitigations),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            data["id"],
            data["description"],
            data["impact"],
            read_provenance(data),
            as_tuple(data.get("preconditions")),
            as_tuple(data.get("signals")),
            as_tuple(data.get("mitigations")),
        )


@dataclass(frozen=True, slots=True)
class Signal:
    """A signal the technology can provide. Its presence here says nothing about whether a
    deployment collects it."""

    id: str
    type: SignalType
    availability: SignalAvailability
    provenance: Provenance
    unit: str | None = None  # a capacity unit, when the signal is a quantity
    collection: str | None = None  # how it is collected (an exporter, an API), when known
    description: str | None = None

    def __post_init__(self) -> None:
        check(
            [
                code(self.id, "id"),
                None if isinstance(self.type, SignalType) else "type",
                None if isinstance(self.availability, SignalAvailability) else "availability",
                _known(self.provenance),
                None if self.unit is None or self.unit in UNITS else "unit",
                text(self.collection, "collection", MAX_NAME, required=False),
                text(self.description, "description", required=False),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "availability": self.availability.value,
            "unit": self.unit,
            "collection": self.collection,
            "description": self.description,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            data["id"],
            SignalType(data["type"]),
            SignalAvailability(data["availability"]),
            read_provenance(data),
            data.get("unit"),
            data.get("collection"),
            data.get("description"),
        )


@dataclass(frozen=True, slots=True)
class SecurityProperty:
    """A security control the technology supports (or not). Whether an architecture enables it is its
    configuration's business: ``property`` names the IR property that states it, when there is one."""

    id: str  # SECURITY_PROPERTIES
    state: CapabilityState
    provenance: Provenance
    property: str | None = None  # a node or connection property of the IR
    requires: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        declared = self.property is None or self.property in NODE_PROPERTIES | CONNECTION_PROPERTIES
        check(
            [
                None if self.id in SECURITY_PROPERTIES else "id",
                None if declared else "property",
                texts(self.requires, "requires", MAX_NAME),
                text(self.note, "note", required=False),
                *state_problems(self.state, self.provenance, self.requires),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "property": self.property,
            "requires": list(self.requires),
            "note": self.note,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            data["id"],
            CapabilityState(data["state"]),
            read_provenance(data),
            data.get("property"),
            as_tuple(data.get("requires")),
            data.get("note"),
        )


@dataclass(frozen=True, slots=True)
class BillingDimension:
    """What the technology is billed by — never a price: prices come from the Cost Engine's pricing
    snapshots."""

    id: str
    unit: PricingUnit
    provenance: Provenance
    driver: str | None = None  # the IR property the usage follows, when there is one
    description: str | None = None

    def __post_init__(self) -> None:
        check(
            [
                code(self.id, "id"),
                None if isinstance(self.unit, PricingUnit) else "unit",
                _known(self.provenance),
                None if self.driver is None or self.driver in NODE_PROPERTIES else "driver",
                text(self.description, "description", required=False),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "unit": self.unit.value,
            "driver": self.driver,
            "description": self.description,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            data["id"],
            PricingUnit(data["unit"]),
            read_provenance(data),
            data.get("driver"),
            data.get("description"),
        )


@dataclass(frozen=True, slots=True)
class OperationalConsideration:
    """An operational responsibility, stated — no complexity score."""

    area: OperationArea
    responsibility: Responsibility
    statement: str
    provenance: Provenance

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.area, OperationArea) else "area",
                None if isinstance(self.responsibility, Responsibility) else "responsibility",
                text(self.statement, "statement"),
                _known(self.provenance),
            ]
        )

    @property
    def id(self) -> str:
        return self.area.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "area": self.area.value,
            "responsibility": self.responsibility.value,
            "statement": self.statement,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            OperationArea(data["area"]),
            Responsibility(data["responsibility"]),
            data["statement"],
            read_provenance(data),
        )


type Claim = (
    Capability
    | CapacityDimension
    | ScalingMethod
    | FailureMode
    | Signal
    | SecurityProperty
    | BillingDimension
    | OperationalConsideration
)
CLAIM_SECTIONS = (
    "capabilities",
    "capacity",
    "scaling",
    "failure_modes",
    "signals",
    "security",
    "billing",
    "operations",
)


def _many[T](data: Mapping[str, Any], name: str, build: type[T]) -> tuple[T, ...]:
    raw = as_tuple(data.get(name))
    if len(raw) > MAX_ITEMS:
        raise InvalidSpecification(details={"component": None, "fields": [name]})
    parse = build.from_dict  # type: ignore[attr-defined]
    return tuple(within(f"{name}[{i}]", partial(parse, d)) for i, d in enumerate(raw))


@dataclass(frozen=True, slots=True)
class ComponentSpecification:
    id: str  # catalog path, e.g. "databases/postgresql"
    version: int
    name: str
    category: str
    technology: str  # the IR technology name it describes, e.g. "postgresql"
    node_kinds: tuple[NodeKind, ...]
    support_status: SupportStatus
    description: str
    provider: Provider
    hosting: Hosting | None = None  # None: either, or not applicable
    aliases: tuple[str, ...] = ()
    technology_versions: tuple[str, ...] = ()  # the versions the claims were checked for (none: any)
    replaced_by: str | None = None  # a deprecated entry's successor
    capabilities: tuple[Capability, ...] = ()
    configuration: tuple[ConfigurationField, ...] = ()
    capacity: tuple[CapacityDimension, ...] = ()
    scaling: tuple[ScalingMethod, ...] = ()
    failure_modes: tuple[FailureMode, ...] = ()
    signals: tuple[Signal, ...] = ()
    security: tuple[SecurityProperty, ...] = ()
    billing: tuple[BillingDimension, ...] = ()
    operations: tuple[OperationalConsideration, ...] = ()
    sources: tuple[Source, ...] = ()
    _hash: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._check_identity()
        self._check_claims()
        for name in (*CLAIM_SECTIONS, "sources"):
            ordered = tuple(sorted(getattr(self, name), key=lambda v: v.id))
            object.__setattr__(self, name, ordered)
        object.__setattr__(self, "configuration", tuple(sorted(self.configuration, key=lambda f: f.property)))
        object.__setattr__(self, "node_kinds", tuple(sorted(set(self.node_kinds))))
        object.__setattr__(self, "aliases", tuple(sorted(set(self.aliases))))
        object.__setattr__(self, "technology_versions", tuple(sorted(set(self.technology_versions))))
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "_hash", hashlib.sha256(canonical.encode()).hexdigest())

    def _check_identity(self) -> None:
        category = CATEGORIES.get(self.category) if isinstance(self.category, str) else None
        path = self.id.split("/") if isinstance(self.id, str) else []
        kinds_ok = (
            isinstance(self.node_kinds, tuple)
            and bool(self.node_kinds)
            and all(isinstance(k, NodeKind) for k in self.node_kinds)
            and category is not None
            and set(self.node_kinds) <= category.node_kinds
        )
        version_ok = (
            isinstance(self.version, int) and not isinstance(self.version, bool) and self.version >= 1
        )
        successor_ok = self.replaced_by is None or (
            self.support_status is SupportStatus.DEPRECATED
            and isinstance(self.replaced_by, str)
            and COMPONENT_REFERENCE.fullmatch(self.replaced_by) is not None
            and self.replaced_by != self.id
        )
        check(
            [
                None if COMPONENT_REFERENCE.fullmatch(str(self.id)) and len(path) == 2 else "id",
                "id" if category is not None and path and path[0] != category.directory else None,
                None if version_ok else "version",
                text(self.name, "name", MAX_NAME),
                None if category is not None else "category",
                None if TECHNOLOGY_NAME.fullmatch(str(self.technology)) else "technology",
                None if kinds_ok else "node_kinds",
                None if isinstance(self.support_status, SupportStatus) else "support_status",
                text(self.description, "description"),
                None if isinstance(self.provider, Provider) else "provider",
                None if self.hosting is None or isinstance(self.hosting, Hosting) else "hosting",
                texts(self.aliases, "aliases", MAX_NAME),
                texts(self.technology_versions, "technology_versions", MAX_NAME),
                None if successor_ok else "replaced_by",
            ]
        )

    def _check_claims(self) -> None:
        sections: dict[str, tuple[tuple[Any, ...], type]] = {
            "capabilities": (self.capabilities, Capability),
            "configuration": (self.configuration, ConfigurationField),
            "capacity": (self.capacity, CapacityDimension),
            "scaling": (self.scaling, ScalingMethod),
            "failure_modes": (self.failure_modes, FailureMode),
            "signals": (self.signals, Signal),
            "security": (self.security, SecurityProperty),
            "billing": (self.billing, BillingDimension),
            "operations": (self.operations, OperationalConsideration),
            "sources": (self.sources, Source),
        }
        check([items(values, kind, name) for name, (values, kind) in sections.items()])
        kinds = {k.value for k in self.node_kinds}
        signal_ids = {s.id for s in self.signals}
        cited = {s for _, provenance in self.claims() for s in provenance.sources}
        filled = {name for name, (values, _) in sections.items() if values and name != "sources"}
        required = {
            SupportStatus.SUPPORTED: {
                "capabilities",
                "configuration",
                "failure_modes",
                "signals",
                "security",
            },
            SupportStatus.PARTIAL: {"capabilities"},
        }.get(self.support_status, set())
        check(
            [
                unique((f.property for f in self.configuration), "configuration"),
                *(
                    unique((v.id for v in values), name)
                    for name, (values, _) in sections.items()
                    if name != "configuration"
                ),
                # a configuration field applies to the kinds this technology is modeled as
                *(
                    f"configuration.{f.property}"
                    for f in self.configuration
                    if not NODE_PROPERTIES[f.property].applies_to & kinds
                ),
                *(f"failure_modes.{m.id}.signals" for m in self.failure_modes if set(m.signals) - signal_ids),
                "sources" if cited - {s.id for s in self.sources} else None,
                # planned entries claim nothing; the others claim at least what their status says
                "support_status" if self.support_status is SupportStatus.PLANNED and filled else None,
                *sorted(required - filled),
            ]
        )

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def content_hash(self) -> str:
        return self._hash

    def claims(self) -> Iterator[tuple[str, Provenance]]:
        """Every claim of the specification with its provenance, by path."""
        for name in CLAIM_SECTIONS:
            claim: Claim
            for claim in getattr(self, name):
                yield f"{name}.{claim.id}", claim.provenance
        for configured in self.configuration:
            yield f"configuration.{configured.property}", configured.provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "category": self.category,
            "technology": self.technology,
            "node_kinds": [k.value for k in self.node_kinds],
            "support_status": self.support_status.value,
            "description": self.description,
            "provider": self.provider.to_dict(),
            "hosting": self.hosting.value if self.hosting else None,
            "aliases": list(self.aliases),
            "technology_versions": list(self.technology_versions),
            "replaced_by": self.replaced_by,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "configuration": [c.to_dict() for c in self.configuration],
            "capacity": [c.to_dict() for c in self.capacity],
            "scaling": [s.to_dict() for s in self.scaling],
            "failure_modes": [f.to_dict() for f in self.failure_modes],
            "signals": [s.to_dict() for s in self.signals],
            "security": [s.to_dict() for s in self.security],
            "billing": [b.to_dict() for b in self.billing],
            "operations": [o.to_dict() for o in self.operations],
            "sources": [s.to_dict() for s in self.sources],
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        """Reads untrusted data (a catalog file): unknown keys, wrong types and broken rules are refused
        with the paths of the fields at fault and the component's id."""
        component = data.get("id") if isinstance(data, Mapping) else None
        try:
            return within("", lambda: cls._read(data))
        except InvalidSpecification as error:
            fields = [f.removeprefix(".") for f in error.details["fields"]]
            raise InvalidSpecification(
                details={"component": component if isinstance(component, str) else None, "fields": fields}
            ) from None

    @classmethod
    def _read(cls, data: object) -> Self:
        data = strict(data, cls)
        hosting = data.get("hosting")
        return cls(
            id=data["id"],
            version=data["version"],
            name=data["name"],
            category=data["category"],
            technology=data["technology"],
            node_kinds=tuple(NodeKind(k) for k in as_tuple(data.get("node_kinds"))),
            support_status=SupportStatus(data["support_status"]),
            description=data["description"],
            provider=within("provider", lambda: Provider.from_dict(data["provider"])),
            hosting=Hosting(hosting) if hosting is not None else None,
            aliases=as_tuple(data.get("aliases")),
            technology_versions=as_tuple(data.get("technology_versions")),
            replaced_by=data.get("replaced_by"),
            capabilities=_many(data, "capabilities", Capability),
            configuration=_many(data, "configuration", ConfigurationField),
            capacity=_many(data, "capacity", CapacityDimension),
            scaling=_many(data, "scaling", ScalingMethod),
            failure_modes=_many(data, "failure_modes", FailureMode),
            signals=_many(data, "signals", Signal),
            security=_many(data, "security", SecurityProperty),
            billing=_many(data, "billing", BillingDimension),
            operations=_many(data, "operations", OperationalConsideration),
            sources=_many(data, "sources", Source),
        )
