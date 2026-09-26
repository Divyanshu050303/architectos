"""Demand propagation (Milestone 7, phase 3): explicit routing, traceable multipliers, no guessing."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.errors import InvalidCapacityConfig
from core.domain.capacity.results import Demand
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.validation.options import RevisionInfo
from engines.capacity.context import CapacityContext
from engines.capacity.engine import Propagation
from engines.capacity.traffic import propagate
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "d" * 64)
RPS = "requests/second"
SHARE = Configuration({"traffic_ratio": 1})


def workload(peak: str = "1000", **overrides: Any) -> WorkloadProfile:
    fields: dict[str, Any] = {
        "name": "Peak",
        "type": WorkloadType.REQUEST_RESPONSE,
        "peak_rate": Quantity.of(peak, RPS),
    }
    return WorkloadProfile(**(fields | overrides))


def call(cid: str, source: str, target: str, **config: Any) -> Any:
    return connection(cid, source, target, kind=ConnectionKind.REQUEST, protocol="https",
                      interaction=Interaction.SYNCHRONOUS, configuration=Configuration(config))  # fmt: skip


def data(cid: str, source: str, target: str, protocol: str = "postgresql", **config: Any) -> Any:
    return connection(cid, source, target, protocol=protocol, configuration=Configuration(config))


def run(ir: ArchitectureIR, load: WorkloadProfile | None = None, **request: Any) -> Propagation:
    context = CapacityContext(
        ir, REVISION, AnalysisRequest(uuid.UUID(int=1), 1, load or workload(), **request)
    )
    return propagate(context)


def total(result: Propagation, node_id: str, resource: str | None = None) -> Decimal:
    return sum(
        (
            d.quantity.canonical
            for d in result.nodes.get(node_id, ())
            if resource is None or d.resource == resource
        ),
        Decimal(0),
    )


def linear(**api_db: Any) -> ArchitectureIR:
    return ArchitectureIR(
        "Linear",
        nodes=(node("web", NodeKind.CLIENT), node("api"), node("db", NodeKind.DATABASE)),
        connections=(call("web-api", "web", "api", traffic_ratio=1), data("api-db", "api", "db", **api_db)),
    )


def test_a_linear_path_carries_declared_shares() -> None:
    result = run(linear(calls_per_request=2))
    assert total(result, "api", "request_rate") == 1000
    assert total(result, "db", "operation_rate") == 2000  # two queries per request
    [db] = result.nodes["db"]
    assert db.path == ("api", "api-db", "db")
    assert [(e.label, e.value) for e in db.factors] == [
        ("upstream_work_per_second", "1000"),
        ("calls_per_request", "2"),
    ]
    assert {d.element_id for d in result.connections} == {"web-api", "api-db"}
    assert result.unsupported == ()
    assert result.incomplete == frozenset()


def test_units_follow_the_connection_kind() -> None:
    result = run(linear(traffic_ratio=1))
    api, db = result.nodes["api"][0], result.nodes["db"][0]
    assert (api.quantity.unit, db.quantity.unit) == ("requests/second", "operations/second")


def test_branching_distributes_by_declared_ratios() -> None:
    ir = ArchitectureIR(
        "Branching",
        nodes=(node("web", NodeKind.CLIENT), node("gw", NodeKind.GATEWAY), node("orders"), node("search")),
        connections=(
            call("web-gw", "web", "gw", traffic_ratio=1),
            call("gw-orders", "gw", "orders", traffic_ratio=Decimal("0.3")),
            call("gw-search", "gw", "search", traffic_ratio=Decimal("0.7")),
        ),
    )
    result = run(ir)
    assert (total(result, "orders"), total(result, "search")) == (Decimal(300), Decimal(700))


def test_fan_out_and_aggregation_add_up() -> None:
    ir = ArchitectureIR(
        "Fan-in",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api"),
            node("a"),
            node("b"),
            node("db", NodeKind.DATABASE),
        ),
        connections=(
            call("web-api", "web", "api", traffic_ratio=1),
            call("api-a", "api", "a", calls_per_request=3),  # fan-out
            call("api-b", "api", "b", traffic_ratio=Decimal("0.5")),
            data("a-db", "a", "db", calls_per_request=1),
            data("b-db", "b", "db", calls_per_request=2),
        ),
    )
    result = run(ir)
    assert total(result, "a") == 3000
    assert total(result, "b") == 500
    assert total(result, "db") == 3000 + 1000  # both paths arrive
    assert sorted(d.path for d in result.nodes["db"]) == [("a", "a-db", "db"), ("b", "b-db", "db")]


def test_read_write_split_and_cache_hits() -> None:
    ir = ArchitectureIR(
        "Cache-aside",
        nodes=(node("web", NodeKind.CLIENT), node("api"), node("db", NodeKind.DATABASE)),
        connections=(
            call("web-api", "web", "api", traffic_ratio=1),
            data("api-db-read", "api", "db", traffic_ratio=1, access="read", cache_hit_ratio=Decimal("0.9")),
            data("api-db-write", "api", "db", traffic_ratio=1, access="write", protocol="pg-primary"),
        ),
    )
    result = run(ir, workload(read_ratio=Decimal("0.8")))
    assert total(result, "db", "read_operation_rate") == 80  # 1000 * 0.8 reads * 0.1 misses
    assert total(result, "db", "write_operation_rate") == 200
    read = next(d for d in result.nodes["db"] if d.resource == "read_operation_rate")
    assert {e.label for e in read.factors} >= {"read_ratio", "cache_hit_ratio", "traffic_ratio"}


def test_a_read_write_split_needs_the_workload_read_ratio() -> None:
    result = run(linear(traffic_ratio=1, access="read"))
    assert "db" not in result.nodes
    assert [(u.element_id, u.code, u.missing) for u in result.unsupported] == [
        ("api-db", "routing_unspecified", ("workload.read_ratio",))
    ]


def test_missing_routing_is_reported_not_assumed() -> None:
    ir = ArchitectureIR(
        "Undeclared",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api"),
            node("db", NodeKind.DATABASE),
            node("x", NodeKind.STORAGE),
        ),
        connections=(
            call("web-api", "web", "api", traffic_ratio=1),
            data("api-db", "api", "db"),
            data("db-x", "db", "x", traffic_ratio=1),
        ),
    )
    result = run(ir)
    assert "db" not in result.nodes  # not 100 %, not 0: unknown
    assert result.incomplete == {"db", "x"}  # and everything after it
    [unsupported] = result.unsupported
    assert (unsupported.element_id, unsupported.code, unsupported.missing) == (
        "api-db",
        "routing_unspecified",
        ("configuration.traffic_ratio",),
    )


def test_a_single_component_entry() -> None:
    ir = ArchitectureIR("One", nodes=(node("api"),))
    result = run(ir, entries=("api",))
    [arrival] = result.nodes["api"]
    assert (arrival.quantity, arrival.path) == (Quantity.of("1000", RPS), ("api",))


def test_event_streams_flow_from_broker_to_consumers() -> None:
    ir = ArchitectureIR(
        "Events",
        nodes=(node("producer"), node("q", NodeKind.QUEUE), node("worker", NodeKind.WORKER)),
        connections=(
            connection(
                "producer-q",
                "producer",
                "q",
                kind=ConnectionKind.PUBLISH,
                protocol="kafka",
                configuration=Configuration({"traffic_ratio": 1}),
            ),
            connection(
                "worker-q",
                "worker",
                "q",
                kind=ConnectionKind.CONSUME,
                protocol="kafka",
                configuration=Configuration({"traffic_ratio": 1}),
            ),
        ),
    )
    load = WorkloadProfile("Orders", WorkloadType.EVENT_STREAM, peak_rate=Quantity.of("500", "events/second"))
    result = run(ir, load, entries=("producer",))
    assert total(result, "q", "event_rate") == 500
    assert total(result, "worker", "event_rate") == 500
    assert result.nodes["worker"][0].path == ("q", "worker-q", "worker")


def test_a_batch_enters_as_operations() -> None:
    nodes = (node("job", NodeKind.WORKER), node("db", NodeKind.DATABASE))
    ir = ArchitectureIR("Batch", nodes=nodes, connections=(data("job-db", "job", "db", calls_per_request=1),))
    load = WorkloadProfile(
        "Export", WorkloadType.BATCH, batch_size=36_000, batch_interval=Quantity.of("1", "h")
    )
    result = run(ir, load, entries=("job",))
    assert result.nodes["job"][0].quantity == Quantity.of("10", "operations/second")
    assert total(result, "db") == 10


def test_cycles_are_unsupported_not_guessed() -> None:
    ir = ArchitectureIR(
        "Loop",
        nodes=(node("web", NodeKind.CLIENT), node("a"), node("b"), node("c")),
        connections=(
            call("web-a", "web", "a", traffic_ratio=1),
            call("a-b", "a", "b", traffic_ratio=1),
            call("b-a", "b", "a", traffic_ratio=1),
            call("b-c", "b", "c", traffic_ratio=1),
        ),
    )
    result = run(ir)
    assert {u.element_id for u in result.unsupported if u.code == "cyclic_traffic"} == {"a", "b", "c"}
    assert not ({"a", "b", "c"} & set(result.nodes))


def test_traffic_from_outside_the_workload_makes_downstream_incomplete() -> None:
    ir = ArchitectureIR(
        "Cron",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api"),
            node("cron", NodeKind.WORKER),
            node("db", NodeKind.DATABASE),
        ),
        connections=(
            call("web-api", "web", "api", traffic_ratio=1),
            data("api-db", "api", "db", traffic_ratio=1),
            data("cron-db", "cron", "db", traffic_ratio=1),
        ),
    )
    result = run(ir)
    assert total(result, "db") == 1000  # what is known
    assert result.incomplete == {"db"}  # but a lower bound
    assert ("cron", "unmodeled_source") in {(u.element_id, u.code) for u in result.unsupported}


def test_entries_and_their_shares() -> None:
    ir = ArchitectureIR(
        "Two clients",
        nodes=(node("web", NodeKind.CLIENT), node("mobile", NodeKind.CLIENT), node("api")),
        connections=(
            call("web-api", "web", "api", traffic_ratio=Decimal("0.4")),
            call("mobile-api", "mobile", "api", traffic_ratio=Decimal("0.6")),
        ),
    )
    assert total(run(ir), "api") == 1000
    both = (
        call("web-api", "web", "api", traffic_ratio=1),
        call("mobile-api", "mobile", "api", traffic_ratio=1),
    )
    too_much = ArchitectureIR("Over", nodes=ir.nodes, connections=both)
    assert "entry_shares_exceed_total" in {u.code for u in run(too_much).unsupported}
    no_clients = ArchitectureIR("None", nodes=(node("api"),))
    assert [u.code for u in run(no_clients).unsupported] == ["no_entry"]
    with pytest.raises(InvalidCapacityConfig):
        run(ir, entries=("ghost",))


def test_dependencies_and_replication_carry_no_workload() -> None:
    ir = ArchitectureIR(
        "Replicas",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api"),
            node("db", NodeKind.DATABASE),
            node("replica", NodeKind.DATABASE),
        ),
        connections=(
            call("web-api", "web", "api", traffic_ratio=1),
            data("api-db", "api", "db", traffic_ratio=1),
            connection("db-replica", "db", "replica", kind=ConnectionKind.REPLICATION, protocol="postgresql"),
        ),
    )
    assert "replica" not in run(ir).nodes


def test_propagation_is_deterministic() -> None:
    ir = linear(calls_per_request=Decimal("1.5"))
    first, again = run(ir), run(ir)
    assert first == again
    assert all(isinstance(d, Demand) for d in first.connections)
    assert run(ir).nodes["db"][0].quantity == Quantity.of("1500", "operations/second")


def test_work_in_different_units_is_never_summed() -> None:
    """Review finding: a node receiving requests and operations had them added into one figure."""
    ir = ArchitectureIR(
        "Mixed",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("gw", NodeKind.GATEWAY),
            node("svc"),
            node("db", NodeKind.DATABASE),
        ),
        connections=(
            call("web-gw", "web", "gw", traffic_ratio=1),
            call("gw-svc", "gw", "svc", traffic_ratio=Decimal("0.5")),
            data("gw-svc-data", "gw", "svc", traffic_ratio=Decimal("0.5")),  # svc also receives operations
            data("svc-db", "svc", "db", traffic_ratio=1),
        ),
    )
    result = run(ir)
    assert ("svc", "mixed_work_units") in {(u.element_id, u.code) for u in result.unsupported}
    assert {"svc", "db"} <= result.incomplete  # nothing after it is a known total
    assert "db" not in result.nodes


def test_demand_beyond_a_quantity_is_reported_not_raised() -> None:
    """Review finding: fan-out chains could exceed 10^15 per second and abort the analysis."""
    ir = ArchitectureIR(
        "Fan-out",
        nodes=(node("web", NodeKind.CLIENT), node("a"), node("b"), node("c")),
        connections=(
            call("web-a", "web", "a", traffic_ratio=1),
            call("a-b", "a", "b", calls_per_request=1000),
            call("b-c", "b", "c", calls_per_request=1000),
        ),
    )
    result = run(ir, workload("1000000000"))  # 10^9 -> 10^12 at b -> 10^15 at c: too large
    assert ("b-c", "demand_overflow") in {(u.element_id, u.code) for u in result.unsupported}
    assert "c" in result.incomplete
    assert "c" not in result.nodes  # no number beyond what a quantity holds
