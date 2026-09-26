"""Small, valid architectures for the IR tests, and a helper to read violations."""

import uuid
from collections.abc import Callable
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
