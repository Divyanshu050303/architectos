"""A component's reliability facts, as its architecture declares them.

Reliability inputs are optional configuration properties of the IR (see
``core/architecture_ir/configuration.py``): ``availability`` (the component as a whole),
``replica_availability``, ``mtbf_seconds``, ``mttr_seconds``, ``min_healthy_replicas``,
``failure_independence``, ``failover_mode``, ``failover_seconds``, ``redundancy_group``,
``redundancy_group_min_healthy``, ``replication_lag_seconds``, ``backup_interval_seconds``, with the
existing ``replicas``, ``multi_az``, ``availability_zones``, ``region``, ``replication_mode`` and
``backup_enabled``.

Each fact keeps where it came from: ``declared`` (a value in the configuration) or ``unknown`` (the
configuration says the value is not known), and the provenance of the property when the
architecture records one (e.g. ``terraform``). A property that is absent is simply not a fact: it
is never filled in.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from core.architecture_ir.configuration import ConfigValue
from core.architecture_ir.node import Node
from core.domain.capacity.results import Source
from core.domain.engine_results import Evidence
from core.domain.requirements.value_objects import decimal_to_str

PROPERTIES = (
    "availability",
    "replica_availability",
    "mtbf_seconds",
    "mttr_seconds",
    "replicas",
    "min_healthy_replicas",
    "failure_independence",
    "failover_mode",
    "failover_seconds",
    "redundancy_group",
    "redundancy_group_min_healthy",
    "multi_az",
    "availability_zones",
    "region",
    "replication_mode",
    "replication_lag_seconds",
    "backup_enabled",
    "backup_interval_seconds",
)


def _text(value: ConfigValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return decimal_to_str(value)
    if isinstance(value, tuple):
        return ", ".join(value)
    return str(value)


@dataclass(frozen=True, slots=True)
class Fact:
    """One reliability property of one component."""

    name: str
    value: ConfigValue | None  # None exactly when the source is unknown
    source: Source
    provenance: str | None = None  # e.g. "terraform", "user_edit"
    inferred: bool = False  # the architecture says the value was inferred, not stated or read

    @property
    def proposed(self) -> bool:
        """Inferred, or proposed by a language model: not declared by a person or read from a system."""
        return self.inferred or self.provenance == "llm_proposal"

    @property
    def path(self) -> str:
        return f"configuration.{self.name}"

    def evidence(self) -> Evidence:
        shown = "unknown" if self.value is None else _text(self.value)
        origin = ", ".join(x for x in (self.provenance, "inferred" if self.inferred else None) if x)
        return Evidence(self.path, shown + (f" ({origin})" if origin else ""))


@dataclass(frozen=True, slots=True)
class ComponentReliability:
    node_id: str
    facts: dict[str, Fact] = field(default_factory=dict)

    @classmethod
    def of(cls, node: Node) -> ComponentReliability:
        config, facts = node.configuration, {}
        for name in PROPERTIES:
            origin = node.field_provenance.get(f"configuration.{name}") or node.provenance
            provenance = origin.source.value if origin is not None else None
            inferred = origin is not None and origin.inferred
            if config.is_unknown(name):
                facts[name] = Fact(name, None, Source.UNKNOWN, provenance, inferred)
            elif (value := config.get(name)) is not None:
                facts[name] = Fact(name, value, Source.DECLARED, provenance, inferred)
        return cls(node.id, facts)

    def known(self, name: str) -> ConfigValue | None:
        fact = self.facts.get(name)
        return fact.value if fact is not None else None

    def number(self, name: str) -> Decimal | None:
        value = self.known(name)
        if isinstance(value, bool) or not isinstance(value, int | Decimal):
            return None
        return Decimal(value)

    def missing(self, names: Iterable[str]) -> tuple[str, ...]:
        """The properties among ``names`` without a known value (absent or unknown)."""
        return tuple(f"configuration.{n}" for n in names if self.known(n) is None)

    def evidence(self, names: Iterable[str]) -> tuple[Evidence, ...]:
        return tuple(self.facts[n].evidence() for n in names if n in self.facts)
