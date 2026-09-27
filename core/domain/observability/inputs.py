"""The observability facts of an architecture's elements, as it declares them.

Observability inputs are optional configuration properties of the IR (see
``core/architecture_ir/configuration.py``):

- components: ``criticality`` (``critical``/``standard``), ``logs``, ``structured_logs``,
  ``correlation_ids``, ``metrics`` (kinds: errors, latency, throughput, saturation, resources,
  availability), ``traces``, ``trace_context`` (``propagate``/``terminate``),
  ``trace_sampling_ratio``, ``health_check``, ``alerts`` (the signals alert rules watch), ``owner``;
  on observability components also ``alert_delivery`` and the existing ``retention_seconds``;
- connections: ``telemetry`` (the signals it carries from its source), ``trace_propagation``,
  ``health_check`` (the source checks the target's health).

A property that is absent is not modeled: never assumed present, never assumed absent. Facts keep
their provenance (``core/domain/facts.py``).
"""

from dataclasses import dataclass
from typing import Self

from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.facts import ElementFacts

NODE_PROPERTIES = (
    "criticality",
    "logs",
    "structured_logs",
    "correlation_ids",
    "metrics",
    "traces",
    "trace_context",
    "trace_sampling_ratio",
    "health_check",
    "alerts",
    "owner",
    "alert_delivery",
    "retention_seconds",
)
CONNECTION_PROPERTIES = ("telemetry", "trace_propagation", "health_check")


def _listed(facts: ElementFacts, name: str) -> tuple[str, ...] | None:
    value = facts.known(name)
    return value if isinstance(value, tuple) else None


@dataclass(frozen=True, slots=True)
class ComponentObservability(ElementFacts):
    """A component's observability facts (``element_id`` is the node's id)."""

    @classmethod
    def of(cls, node: Node) -> Self:
        return cls.read(node, NODE_PROPERTIES)

    @property
    def critical(self) -> bool | None:
        """Whether it is declared critical (True), declared standard (False), or not said (None)."""
        value = self.known("criticality")
        return None if value is None else value == "critical"

    @property
    def metric_kinds(self) -> tuple[str, ...] | None:
        """The metric kinds it declares (an empty tuple: declared none), or None when not modeled."""
        return _listed(self, "metrics")

    @property
    def alert_signals(self) -> tuple[str, ...] | None:
        return _listed(self, "alerts")


@dataclass(frozen=True, slots=True)
class ConnectionObservability(ElementFacts):
    """A connection's observability facts (``element_id`` is the connection's id)."""

    @classmethod
    def of(cls, connection: Connection) -> Self:
        return cls.read(connection, CONNECTION_PROPERTIES)

    @property
    def signals(self) -> tuple[str, ...] | None:
        """The telemetry signals it carries, or None when not modeled."""
        return _listed(self, "telemetry")
