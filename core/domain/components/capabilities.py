"""What a technology can do, as structured identifiers with a state — never prose, never inferred
from the category.

A **capability claim** is one identifier of the vocabulary (``CAPABILITIES``) with its state:

- ``native``: the technology provides it as documented;
- ``requires_configuration``: it provides it once configured (``requires`` names what);
- ``requires_external``: it needs another service or an add-on (``requires`` names which);
- ``unsupported``: the technology does not provide it;
- ``unknown``: no evidence either way — never assumed from another product of the category.

A claim is about the **technology**, not about a deployment: that PostgreSQL supports encryption
in transit says nothing about whether an architecture enables it (the architecture's configuration
says that, and the engines read the configuration).

The scaling methods (``SCALING_METHODS``) and security properties (``SECURITY_PROPERTIES``) have
vocabularies of their own, with the same states. Adding an identifier is one entry here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from .entities import MAX_NAME, Provenance, as_tuple, check, read_provenance, strict, text, texts


class CapabilityState(StrEnum):
    NATIVE = "native"
    REQUIRES_CONFIGURATION = "requires_configuration"
    REQUIRES_EXTERNAL = "requires_external"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


REQUIRING = frozenset({CapabilityState.REQUIRES_CONFIGURATION, CapabilityState.REQUIRES_EXTERNAL})

CAPABILITIES: Mapping[str, str] = {
    # data models and queries
    "transactions": "Atomic multi-operation transactions.",
    "relational_queries": "SQL queries over relations, with joins.",
    "document_queries": "Queries over structured documents.",
    "key_value_access": "Reads and writes by key.",
    "wide_column_access": "Reads and writes of partitioned wide rows.",
    "full_text_search": "Full-text indexing and search.",
    "analytical_queries": "Aggregations over large data sets.",
    "strong_consistency": "Reads observe every acknowledged write.",
    "eventual_consistency": "Replicas converge after writes.",
    "time_to_live": "Automatic expiry of stored items.",
    "in_memory_storage": "Data held in memory.",
    "durable_storage": "Data persisted to durable media.",
    # messaging
    "message_queue": "Point-to-point delivery of messages to consumers.",
    "publish_subscribe": "Delivery of each message to every subscription.",
    "event_streaming": "An ordered, retained log that consumers read at their own position.",
    "message_ordering": "Ordered delivery (within a stated scope).",
    "message_replay": "Re-reading retained messages.",
    "dead_letter_queue": "Setting aside messages that repeatedly fail.",
    # storage
    "object_storage": "Objects addressed by key in buckets or containers.",
    "file_storage": "A shared file system.",
    # scaling and availability
    "horizontal_scaling": "Adding instances or nodes.",
    "vertical_scaling": "Larger instances.",
    "read_replication": "Read-only replicas of the data.",
    "automatic_failover": "Promotion of a replica when the primary fails.",
    "partitioning": "Data or work divided across partitions.",
    "autoscaling": "Capacity adjusted automatically to load.",
    "scale_to_zero": "No running instance while idle.",
    "multi_region_replication": "Data replicated across regions.",
    "backup_restore": "Backups and restores of the data.",
    "point_in_time_recovery": "Restores to a chosen moment.",
    # security
    "authentication": "Clients prove their identity.",
    "authorization": "Access controlled per identity.",
    "encryption_at_rest": "Stored data encrypted.",
    "encryption_in_transit": "Traffic encrypted (TLS).",
    "audit_logging": "A record of who did what.",
    # networking
    "load_balancing": "Traffic distributed across targets.",
    "tls_termination": "TLS ended at the component.",
    "request_routing": "Requests routed by path, host or header.",
    "rate_limiting": "Requests limited per client or route.",
    "content_caching": "Responses cached near clients.",
    "name_resolution": "Names resolved to addresses.",
    "request_filtering": "Requests inspected and blocked by rules.",
    # observability
    "metrics_collection": "Metrics collected and stored.",
    "log_aggregation": "Logs collected and searched.",
    "distributed_tracing": "Traces collected across services.",
    "dashboards": "Visualization of telemetry.",
    "alerting": "Notifications on conditions.",
}

SCALING_METHODS: Mapping[str, str] = {
    "vertical": "Larger instances (more CPU or memory per instance).",
    "horizontal": "More instances serving the same workload.",
    "read_replicas": "Read-only replicas serving reads.",
    "partitioning": "Data or work divided across partitions.",
    "sharding": "Data divided across independent nodes by a key.",
    "clustering": "Nodes forming one cluster.",
    "autoscaling": "Instances added and removed automatically.",
    "consumer_scaling": "More consumers reading a queue or partitions.",
    "regional_replication": "Replicas in other regions.",
}

SECURITY_PROPERTIES: Mapping[str, str] = {
    "authentication": "Clients authenticate.",
    "authorization": "Access is controlled per identity.",
    "encryption_at_rest": "Stored data is encrypted.",
    "encryption_in_transit": "Traffic is encrypted.",
    "secret_management": "Credentials come from a secret manager.",
    "network_exposure": "Network exposure can be restricted.",
    "audit_logging": "Actions are recorded.",
    "data_retention": "Data retention and deletion can be controlled.",
}


def state_problems(state: object, provenance: object, requires: tuple[str, ...]) -> list[str | None]:
    """The rules every stated claim follows: a known state rests on evidence, an unknown one on none,
    and a state that needs something names it."""
    known = isinstance(provenance, Provenance) and provenance.is_known
    return [
        None if isinstance(state, CapabilityState) else "state",
        None if isinstance(provenance, Provenance) else "provenance",
        "provenance" if state is CapabilityState.UNKNOWN and known else None,
        "provenance" if state is not CapabilityState.UNKNOWN and not known else None,
        "requires" if state in REQUIRING and not requires else None,
    ]


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    state: CapabilityState
    provenance: Provenance
    requires: tuple[str, ...] = ()  # the configuration, service or add-on it needs
    note: str | None = None

    def __post_init__(self) -> None:
        check(
            [
                None if self.id in CAPABILITIES else "id",
                texts(self.requires, "requires", MAX_NAME),
                text(self.note, "note", required=False),
                *state_problems(self.state, self.provenance, self.requires),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
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
            as_tuple(data.get("requires")),
            data.get("note"),
        )
