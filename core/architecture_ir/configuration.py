"""Structured configuration of nodes and connections.

A configuration has three parts, kept apart on purpose:

- ``values``: **known properties** with a type, a range and the kinds they apply to (below). Units
  are part of the name and always canonical (``memory_limit_bytes``, ``retention_seconds``), so a
  value never needs a unit to be read and cannot be misread. Numbers are exact (int or Decimal).
- ``unknown``: known properties whose value is **not known** (e.g. discovery could not read it).
  An unknown value is never confused with an absent one, and never filled in.
- ``extra``: properties this IR does not recognize, **preserved as found** (e.g. from an import),
  so nothing is silently dropped. Engines must not rely on them.

Adding a property: add a ``PropertySpec`` here (a new optional property is backward compatible).
Which technologies support which values is the component catalog's concern, not this module's.
"""

import re
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Self

from .component import NodeKind
from .errors import Violation, raise_if
from .values import (
    Json,
    canonical_decimal,
    freeze_json,
    frozen,
    json_problems,
    number_problems,
    text_problems,
)

type ConfigValue = int | Decimal | str | bool | tuple[str, ...]

PROPERTY_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
MAX_PROPERTIES = 100
MAX_TEXT_VALUE = 200
MAX_LIST_VALUES = 50
REGION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")  # e.g. eu-west-1, eu-west-1a
# As a pricing snapshot writes them (core/domain/cost/pricing.py): services and conditions are
# lower-case identifiers ("rds", "on_demand"); SKUs are the provider's ("db.r6g.large").
PRICING_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PRICING_SKU = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
CONNECTION = "connection"


class ValueType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    TEXT = "text"
    CHOICE = "choice"
    TEXT_LIST = "text_list"


@dataclass(frozen=True, slots=True)
class PropertySpec:
    name: str
    type: ValueType
    applies_to: frozenset[str]  # node kinds, or {"connection"}
    description: str
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    choices: frozenset[str] = frozenset()
    pattern: re.Pattern[str] | None = None

    def problems(self, value: object, path: str) -> list[Violation]:
        match self.type:
            case ValueType.INTEGER | ValueType.DECIMAL:
                problems = self._number_problems(value, path)
            case ValueType.BOOLEAN:
                ok = isinstance(value, bool)
                problems = [] if ok else [Violation("invalid_value", f"{path} must be true or false.", path)]
            case ValueType.CHOICE:
                options = ", ".join(sorted(self.choices))
                ok = value in self.choices
                problems = (
                    [] if ok else [Violation("invalid_value", f"{path} must be one of: {options}.", path)]
                )
            case ValueType.TEXT:
                problems = self._text_problems(value, path)
            case ValueType.TEXT_LIST:
                problems = self._list_problems(value, path)
        return problems

    def _list_problems(self, value: object, path: str) -> list[Violation]:
        if not isinstance(value, tuple) or len(value) > MAX_LIST_VALUES:
            return [
                Violation(
                    "invalid_value", f"{path} must be a list of at most {MAX_LIST_VALUES} values.", path
                )
            ]
        return [p for i, v in enumerate(value) for p in self._text_problems(v, f"{path}[{i}]")]

    def _number_problems(self, value: object, path: str) -> list[Violation]:
        if self.type is ValueType.INTEGER and (isinstance(value, bool) or not isinstance(value, int)):
            return [Violation("invalid_value", f"{path} must be a whole number.", path)]
        problems = number_problems(value, path)
        if problems or not isinstance(value, int | Decimal):
            return problems
        if self.minimum is not None and value < self.minimum:
            return [Violation("out_of_range", f"{path} must be at least {self.minimum}.", path)]
        if self.maximum is not None and value > self.maximum:
            return [Violation("out_of_range", f"{path} must be at most {self.maximum}.", path)]
        return []

    def _text_problems(self, value: object, path: str) -> list[Violation]:
        problems = text_problems(value, path, MAX_TEXT_VALUE, required=True)
        if not problems and self.pattern is not None and not self.pattern.fullmatch(str(value)):
            problems.append(Violation("invalid_value", f"{path} is not in the expected format.", path))
        return problems


def _spec(
    name: str,
    type_: ValueType,
    applies_to: Iterable[str],
    description: str,
    *,
    minimum: str | None = None,
    maximum: str | None = None,
    choices: Iterable[str] = (),
    pattern: re.Pattern[str] | None = None,
) -> PropertySpec:
    return PropertySpec(
        name,
        type_,
        frozenset(applies_to),
        description,
        Decimal(minimum) if minimum is not None else None,
        Decimal(maximum) if maximum is not None else None,
        frozenset(choices),
        pattern,
    )


K = NodeKind
_COMPUTE = {K.SERVICE, K.WORKER, K.GATEWAY}
_RUNNING = _COMPUTE | {K.LOAD_BALANCER, K.DATABASE, K.CACHE, K.QUEUE, K.OBSERVABILITY}
_RESOURCED = _COMPUTE | {K.DATABASE, K.CACHE, K.QUEUE, K.OBSERVABILITY}
_DATA = {K.DATABASE, K.CACHE, K.STORAGE}
_DEPLOYED = set(NodeKind) - {K.CLIENT, K.BOUNDARY}
_PLACED = set(NodeKind)
_I, _D, _B, _T, _C, _L = (
    ValueType.INTEGER,
    ValueType.DECIMAL,
    ValueType.BOOLEAN,
    ValueType.TEXT,
    ValueType.CHOICE,
    ValueType.TEXT_LIST,
)

NODE_PROPERTIES: dict[str, PropertySpec] = {
    s.name: s
    for s in [
        # runtime and scale
        _spec("runtime", _T, {K.SERVICE, K.WORKER}, "Language runtime, e.g. python3.13."),
        _spec("replicas", _I, _RUNNING, "Instances running, including any primary.", minimum="0"),
        _spec(
            "autoscaling_min_replicas", _I, _COMPUTE, "Fewest instances the autoscaler keeps.", minimum="0"
        ),
        _spec("autoscaling_max_replicas", _I, _COMPUTE, "Most instances the autoscaler starts.", minimum="0"),
        _spec(
            "autoscaling_target_cpu_ratio",
            _D,
            _COMPUTE,
            "CPU utilization the autoscaler targets (0.7 = 70 %).",
            minimum="0.01",
            maximum="1",
        ),
        # resources (per instance)
        _spec("cpu_request_cores", _D, _RESOURCED, "CPU reserved per instance, in cores.", minimum="0"),
        _spec("cpu_limit_cores", _D, _RESOURCED, "CPU limit per instance, in cores.", minimum="0"),
        _spec("memory_request_bytes", _I, _RESOURCED, "Memory reserved per instance, in bytes.", minimum="0"),
        _spec("memory_limit_bytes", _I, _RESOURCED, "Memory limit per instance, in bytes.", minimum="0"),
        _spec("instance_class", _T, _RUNNING, "Provider instance class, e.g. db.r6g.large."),
        _spec(
            "deployment_model",
            _C,
            _DEPLOYED,
            "How it runs.",
            choices={
                "kubernetes",
                "container_service",
                "serverless",
                "virtual_machine",
                "managed_service",
                "on_premises",
            },
        ),
        # placement
        _spec("region", _T, _PLACED, "Cloud region, e.g. eu-west-1.", pattern=REGION),
        _spec("availability_zones", _L, _PLACED, "Availability zones used.", pattern=REGION),
        _spec("multi_az", _B, _DEPLOYED - {K.EXTERNAL}, "Runs across availability zones."),
        # data
        _spec(
            "storage_bytes",
            _I,
            _DATA | {K.QUEUE, K.OBSERVABILITY},
            "Provisioned storage, in bytes.",
            minimum="0",
        ),
        _spec(
            "max_connections",
            _I,
            {K.DATABASE, K.CACHE, K.GATEWAY, K.LOAD_BALANCER},
            "Connection limit.",
            minimum="0",
        ),
        _spec(
            "replication_mode",
            _C,
            _DATA,
            "How data is replicated.",
            choices={"none", "asynchronous", "synchronous"},
        ),
        _spec("backup_enabled", _B, _DATA, "Backups are taken."),
        _spec("backup_retention_seconds", _I, _DATA, "How long backups are kept, in seconds.", minimum="0"),
        _spec(
            "eviction_policy",
            _C,
            {K.CACHE},
            "What the cache evicts when full.",
            choices={
                "noeviction",
                "allkeys_lru",
                "allkeys_lfu",
                "allkeys_random",
                "volatile_lru",
                "volatile_lfu",
                "volatile_random",
                "volatile_ttl",
            },
        ),
        _spec(
            "persistence",
            _C,
            {K.CACHE},
            "Whether the cache survives a restart.",
            choices={"none", "snapshot", "append_only"},
        ),
        # messaging
        _spec("partitions", _I, {K.QUEUE}, "Partitions (or shards) of the queue or topic.", minimum="1"),
        _spec("replication_factor", _I, {K.QUEUE}, "Copies of each message kept.", minimum="1"),
        _spec(
            "retention_seconds",
            _I,
            {K.QUEUE, K.STORAGE, K.OBSERVABILITY},
            "How long data is kept, in seconds.",
            minimum="0",
        ),
        # capacity (declared by the architect or read from a system; read by the capacity engine)
        _spec(
            "throughput_limit_per_second",
            _D,
            _DEPLOYED,
            "Most units of work (requests, operations or events) the component handles per second, in total.",
            minimum="0",
        ),
        _spec(
            "throughput_per_replica_per_second",
            _D,
            _RUNNING,
            "Most units of work one replica handles per second.",
            minimum="0",
        ),
        _spec(
            "cpu_core_seconds_per_request",
            _D,
            _COMPUTE,
            "CPU time one unit of work costs, in core-seconds (0.02 = 20 ms of one core).",
            minimum="0",
        ),
        _spec(
            "network_bandwidth_bytes_per_second",
            _I,
            _DEPLOYED,
            "Network throughput available to the component, in bytes per second.",
            minimum="0",
        ),
        # pricing (read by the cost engine: how a component maps to its organization's price list;
        # nothing is inferred when they are absent)
        _spec(
            "pricing_service",
            _T,
            _DEPLOYED,
            "Service that bills the component in the pricing snapshot, e.g. rds.",
            pattern=PRICING_IDENTIFIER,
        ),
        _spec(
            "pricing_sku",
            _T,
            _DEPLOYED,
            "SKU of the component's main billed resource, e.g. db.r6g.large (else instance_class is used).",
            pattern=PRICING_SKU,
        ),
        _spec(
            "pricing_storage_sku",
            _T,
            {K.DATABASE, K.QUEUE, K.OBSERVABILITY},
            "SKU of the component's provisioned storage, priced per GB-month.",
            pattern=PRICING_SKU,
        ),
        _spec(
            "pricing_conditions",
            _L,
            _DEPLOYED,
            "Conditions the price must carry, e.g. on_demand.",
            pattern=PRICING_IDENTIFIER,
        ),
        # boundaries
        _spec(
            "boundary_type",
            _C,
            {K.BOUNDARY},
            "What the boundary delimits.",
            choices={"system", "network", "region", "availability_zone", "account", "cluster", "trust_zone"},
        ),
    ]
}

CONNECTION_PROPERTIES: dict[str, PropertySpec] = {
    s.name: s
    for s in [
        _spec("timeout_seconds", _D, {CONNECTION}, "How long the source waits, in seconds.", minimum="0"),
        _spec("retries", _I, {CONNECTION}, "Retries after a failure.", minimum="0"),
        _spec("tls", _B, {CONNECTION}, "Traffic is encrypted in transit."),
        _spec("dead_letter", _B, {CONNECTION}, "Messages that keep failing go to a dead-letter queue."),
        _spec("port", _I, {CONNECTION}, "Target port.", minimum="1", maximum="65535"),
        # traffic (read by the capacity engine; nothing is assumed when they are absent)
        _spec(
            "traffic_ratio",
            _D,
            {CONNECTION},
            "Share of the source's work that uses this connection (0.3 = 30 %).",
            minimum="0",
            maximum="1",
        ),
        _spec(
            "calls_per_request",
            _D,
            {CONNECTION},
            "Calls made over this connection per unit of work that uses it (fan-out).",
            minimum="0",
            maximum="1000",
        ),
        _spec(
            "cache_hit_ratio",
            _D,
            {CONNECTION},
            "Share of this traffic a cache in front of the target answers; only misses arrive.",
            minimum="0",
            maximum="1",
        ),
        _spec(
            "pool_size",
            _I,
            {CONNECTION},
            "Connections each replica of the source keeps open to the target (a connection pool).",
            minimum="0",
            maximum="100000",
        ),
        _spec(
            "access",
            _C,
            {CONNECTION},
            "Which side of the workload's read/write mix this data access carries.",
            choices={"read", "write", "read_write"},
        ),
    ]
}

# Pairs that must be ordered: (lower, upper).
_ORDERED_PAIRS = (
    ("autoscaling_min_replicas", "autoscaling_max_replicas"),
    ("cpu_request_cores", "cpu_limit_cores"),
    ("memory_request_bytes", "memory_limit_bytes"),
)


def _canonical(value: object) -> object:
    if isinstance(value, Decimal) and value.is_finite():
        return canonical_decimal(value)
    if isinstance(value, list):
        value = tuple(value)
    if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
        return tuple(sorted(set(value)))  # zones and other lists are sets: one representation
    return value


def _is_number(value: object) -> bool:
    return isinstance(value, int | Decimal) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class Configuration:
    values: Mapping[str, ConfigValue] = field(default_factory=dict)
    unknown: Set[str] = frozenset()  # stored as a frozenset
    extra: Mapping[str, Json] = field(default_factory=dict)

    def __copy__(self) -> Self:
        return self  # immutable: a copy would be indistinguishable

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        return self

    def __post_init__(self) -> None:
        if isinstance(self.values, Mapping):
            object.__setattr__(self, "values", frozen({k: _canonical(v) for k, v in self.values.items()}))
        if isinstance(self.unknown, Set | list | tuple):
            object.__setattr__(self, "unknown", frozenset(self.unknown))
        if isinstance(self.extra, Mapping):
            object.__setattr__(self, "extra", freeze_json(self.extra))
        raise_if(self.problems())

    def __bool__(self) -> bool:
        return bool(self.values or self.unknown or self.extra)

    def get(self, name: str) -> ConfigValue | None:
        return self.values.get(name)

    def is_unknown(self, name: str) -> bool:
        return name in self.unknown

    def property_names(self) -> frozenset[str]:
        """Every property this configuration mentions: known, unknown or preserved."""
        return frozenset(self.values) | frozenset(self.unknown) | frozenset(self.extra)

    def problems(self) -> list[Violation]:
        """Shape only; whether each property fits its owner is ``problems_for``'s question."""
        if not isinstance(self.values, Mapping) or not isinstance(self.extra, Mapping):
            return [Violation("not_an_object", "Configuration values must be objects.", None)]
        if not isinstance(self.unknown, frozenset) or not all(isinstance(k, str) for k in self.unknown):
            return [Violation("invalid_value", "unknown must be a list of property names.", "unknown")]
        problems: list[Violation] = []
        if len(self.property_names()) > MAX_PROPERTIES:
            problems.append(
                Violation("too_many", f"A configuration has at most {MAX_PROPERTIES} properties.", None)
            )
        for key in sorted(self.property_names(), key=str):
            if not isinstance(key, str) or not PROPERTY_KEY.fullmatch(key):
                problems.append(Violation("invalid_key", f"{key!r} is not a valid property name.", str(key)))
        for key in sorted(frozenset(self.unknown) & frozenset(self.values)):
            problems.append(Violation("contradictory", f"{key} cannot be both known and unknown.", key))
        for key in sorted(frozenset(self.extra) & (frozenset(self.values) | frozenset(self.unknown))):
            problems.append(Violation("duplicate_property", f"{key} is given twice.", key))
        for key, value in self.extra.items():
            problems += json_problems(value, key)
        return problems

    def problems_for(self, specs: Mapping[str, PropertySpec], owner: str) -> list[Violation]:
        """The known properties against their specifications for ``owner`` (a node kind, or
        "connection")."""
        problems: list[Violation] = []
        for key in sorted(frozenset(self.values) | frozenset(self.unknown)):
            spec = specs.get(key)
            if spec is None:
                problems.append(
                    Violation(
                        "unknown_property",
                        f"{key} is not a known property; unrecognized settings belong in extra.",
                        key,
                    )
                )
            elif owner not in spec.applies_to:
                problems.append(Violation("not_applicable", f"{key} does not apply to a {owner}.", key))
            elif key in self.values:
                problems += spec.problems(self.values[key], key)
        for key in sorted(frozenset(self.extra) & frozenset(specs)):
            problems.append(
                Violation("misplaced_property", f"{key} is a known property; give it in values.", key)
            )
        for lower, upper in _ORDERED_PAIRS:
            low, high = self.values.get(lower), self.values.get(upper)
            if _is_number(low) and _is_number(high) and low > high:  # type: ignore[operator]
                problems.append(Violation("inconsistent", f"{lower} is greater than {upper}.", lower))
        return problems
