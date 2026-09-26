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
is never filled in. The facts themselves are the engines' shared ``core/domain/facts.py``.
"""

from dataclasses import dataclass
from typing import Self

from core.architecture_ir.node import Node
from core.domain.facts import ElementFacts, Fact

__all__ = ["PROPERTIES", "ComponentReliability", "Fact"]

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


@dataclass(frozen=True, slots=True)
class ComponentReliability(ElementFacts):
    """A node's reliability facts (``element_id`` is the node's id)."""

    @classmethod
    def of(cls, node: Node) -> Self:
        return cls.read(node, PROPERTIES)
