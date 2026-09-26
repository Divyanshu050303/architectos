"""Read-only graph queries for the engines (Architecture IR phase 5)."""

import dataclasses

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology

from .builders import connection, node, service_cache_queue


def test_neighbours_and_kinds() -> None:
    topology = Topology(service_cache_queue())
    assert [c.id for c in topology.outgoing("api")] == ["api-cache", "api-events"]
    assert [c.id for c in topology.outgoing("api", ConnectionKind.PUBLISH)] == ["api-events"]
    assert [n.id for n in topology.successors("api")] == ["cache", "events"]
    assert [n.id for n in topology.predecessors("events")] == ["api", "worker"]
    assert [n.id for n in topology.nodes_of_kind(NodeKind.QUEUE, NodeKind.CACHE)] == ["cache", "events"]
    assert topology.node("ghost") is None
    assert topology.connection("api-cache") is not None


def test_what_depends_on_what() -> None:
    topology = Topology(service_cache_queue())
    assert [n.id for n in topology.reachable_from("api")] == ["cache", "events"]
    # Everything that relies on the broker, directly or not: what its failure can affect.
    assert [n.id for n in topology.dependents_of("events")] == ["api", "worker"]
    assert topology.dependents_of("worker") == ()


def test_cycles_do_not_loop_forever() -> None:
    ir = ArchitectureIR(
        "Mesh",
        nodes=(node("a"), node("b"), node("c")),
        connections=(
            connection("a-b", "a", "b", kind=ConnectionKind.REQUEST, protocol="grpc"),
            connection("b-c", "b", "c", kind=ConnectionKind.REQUEST, protocol="grpc"),
            connection("c-a", "c", "a", kind=ConnectionKind.REQUEST, protocol="grpc"),
        ),
    )
    assert [n.id for n in Topology(ir).reachable_from("a")] == ["b", "c"]
    assert [n.id for n in Topology(ir).dependents_of("a")] == ["b", "c"]


def test_containment() -> None:
    ir = service_cache_queue()
    region = node("eu", NodeKind.BOUNDARY)
    vpc = node("vpc", NodeKind.BOUNDARY, parent_id="eu")
    nodes = (region, vpc, *(dataclasses.replace(n, parent_id="vpc") for n in ir.nodes))
    topology = Topology(dataclasses.replace(ir, nodes=nodes))
    assert [n.id for n in topology.children("vpc")] == ["api", "cache", "events", "worker"]
    assert [n.id for n in topology.ancestors("api")] == ["vpc", "eu"]
    assert [n.id for n in topology.components()] == ["api", "cache", "events", "worker"]
