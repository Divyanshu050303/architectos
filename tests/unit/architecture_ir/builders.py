"""Small, valid architectures for the IR tests, and a helper to read violations."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.traceability import Assumption, RequirementRef

LATENCY = uuid.UUID("0190e000-0000-7000-8000-000000000001")
THROUGHPUT = uuid.UUID("0190e000-0000-7000-8000-000000000002")


def node(node_id: str = "api", kind: NodeKind = NodeKind.SERVICE, **overrides: Any) -> Node:
    fields: dict[str, Any] = {"id": node_id, "kind": kind, "name": node_id.title()}
    return Node(**(fields | overrides))


def connection(
    connection_id: str = "api-db", source: str = "api", target: str = "db", **overrides: Any
) -> Connection:
    fields: dict[str, Any] = {
        "id": connection_id,
        "source_id": source,
        "target_id": target,
        "kind": ConnectionKind.DATA_ACCESS,
        "protocol": "postgresql",
    }
    return Connection(**(fields | overrides))


def api_and_postgres(**overrides: Any) -> ArchitectureIR:
    """A web client, an API service and its PostgreSQL database."""
    fields: dict[str, Any] = {
        "name": "Orders",
        "nodes": (
            node("web", NodeKind.CLIENT, name="Web app"),
            node(
                "api",
                name="Orders API",
                technology=Technology("fastapi"),
                configuration=Configuration(
                    {"replicas": 3, "cpu_request_cores": Decimal("0.5"), "memory_limit_bytes": 1_073_741_824}
                ),
            ),
            node(
                "db",
                NodeKind.DATABASE,
                name="Orders DB",
                technology=Technology("postgresql", "16"),
                configuration=Configuration({"storage_bytes": 100 * 10**9, "multi_az": True}),
            ),
        ),
        "connections": (
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https",
                       interaction=Interaction.SYNCHRONOUS),
            connection(),
        ),
    }  # fmt: skip
    return ArchitectureIR(**(fields | overrides))


def llm(confidence: str = "0.8") -> Provenance:
    return Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal(confidence))


def rules(build: Callable[[], object]) -> set[str]:
    """The rules ``build`` breaks (it must raise)."""
    with pytest.raises(InvalidArchitecture) as raised:
        build()
    return {v.rule for v in raised.value.violations}


def violations(build: Callable[[], object]) -> list[dict[str, Any]]:
    with pytest.raises(InvalidArchitecture) as raised:
        build()
    return [v.to_dict() for v in raised.value.violations]


def service_cache_queue() -> ArchitectureIR:
    """An API with a Redis cache, publishing order events to Kafka for a worker."""
    return ArchitectureIR(
        name="Orders with events",
        description="Orders API, cache-aside reads, events consumed by a worker.",
        requirement_refs=(RequirementRef(THROUGHPUT), RequirementRef(LATENCY, 2)),
        nodes=(
            node(
                "api",
                technology=Technology("fastapi"),
                configuration=Configuration(
                    {
                        "replicas": 4,
                        "autoscaling_min_replicas": 2,
                        "autoscaling_max_replicas": 12,
                        "autoscaling_target_cpu_ratio": Decimal("0.7"),
                        "cpu_request_cores": Decimal("0.25"),
                        "availability_zones": ("eu-west-1a", "eu-west-1b"),
                    }
                ),
                requirement_refs=(RequirementRef(LATENCY, 2),),
            ),
            node(
                "cache",
                NodeKind.CACHE,
                technology=Technology("redis", "7.2"),
                configuration=Configuration(
                    {"memory_limit_bytes": 4 * 2**30, "eviction_policy": "allkeys_lru", "persistence": "none"}
                ),
            ),
            node(
                "events",
                NodeKind.QUEUE,
                technology=Technology("kafka"),
                configuration=Configuration(
                    {"partitions": 12, "replication_factor": 3, "retention_seconds": 604_800}
                ),
            ),
            node(
                "worker",
                NodeKind.WORKER,
                configuration=Configuration({"replicas": 2, "runtime": "python3.13"}),
            ),
        ),
        connections=(
            connection(
                "api-cache",
                "api",
                "cache",
                protocol="redis",
                configuration=Configuration({"timeout_seconds": Decimal("0.05")}),
            ),
            connection(
                "api-events",
                "api",
                "events",
                kind=ConnectionKind.PUBLISH,
                protocol="kafka",
                interaction=Interaction.ASYNCHRONOUS,
                critical=False,
            ),
            connection(
                "worker-events",
                "worker",
                "events",
                kind=ConnectionKind.CONSUME,
                protocol="kafka",
                interaction=Interaction.ASYNCHRONOUS,
                configuration=Configuration({"retries": 5, "dead_letter": True}),
            ),
        ),
        assumptions=(
            Assumption(
                "cache-hit-rate", "80 % of reads are served by the cache.", llm("0.6"), ("api-cache",)
            ),
        ),
    )


def discovered() -> ArchitectureIR:
    """Imported from Terraform: verified values, unknown ones, inferred ones and unrecognized settings."""
    terraform = Provenance(
        ProvenanceSource.TERRAFORM,
        "aws_db_instance.orders",
        verified=True,
        actor="discovery:terraform",
        recorded_at=datetime(2026, 9, 26, 8, 30, tzinfo=UTC),
    )
    return ArchitectureIR(
        name="Production (discovered)",
        provenance=Provenance(ProvenanceSource.TERRAFORM, "prod.tfstate", verified=True),
        nodes=(
            node(
                "vpc-main",
                NodeKind.BOUNDARY,
                name="Main VPC",
                configuration=Configuration({"boundary_type": "network", "region": "eu-west-1"}),
            ),
            node(
                "aws_db_instance.orders",
                NodeKind.DATABASE,
                name="orders",
                parent_id="vpc-main",
                technology=Technology("postgresql", "15.4"),
                component="databases/postgresql",
                configuration=Configuration(
                    {"instance_class": "db.r6g.large", "storage_bytes": 200 * 10**9, "multi_az": True},
                    unknown={"max_connections", "backup_retention_seconds"},
                    extra={
                        "parameter_group": "default.postgres15",
                        "iops": 3000,
                        "throughput_ratio": "0.125",
                    },
                ),
                provenance=terraform,
                field_provenance={
                    "configuration.multi_az": Provenance(ProvenanceSource.SYSTEM_DEFAULT, inferred=True)
                },
            ),
        ),
    )
