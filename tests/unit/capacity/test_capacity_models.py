"""Component capacity models (Milestone 7, phase 4): model-backed, declared inputs only, unknown
stays unknown."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.results import CapacityResult, ComponentStatus, Estimate, Source
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.validation.options import RevisionInfo
from engines.capacity.context import CapacityContext
from engines.capacity.engine import analyze
from engines.capacity.registry import default_registry
from engines.capacity.traffic import propagate
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "e" * 64)
RPS = "requests/second"


def workload(**overrides: Any) -> WorkloadProfile:
    fields: dict[str, Any] = {
        "name": "Peak",
        "type": WorkloadType.REQUEST_RESPONSE,
        "peak_rate": Quantity.of("1000", RPS),
        "read_ratio": Decimal("0.8"),
        "request_payload": Quantity.of("2", "KB"),
        "response_payload": Quantity.of("10", "KB"),
    }
    return WorkloadProfile(**(fields | overrides))


def shop(
    api: dict[str, Any] | None = None, db: dict[str, Any] | None = None, pool: int | None = 10
) -> ArchitectureIR:
    """web -> api (1000 rps) -> db (reads 80 %, writes 20 %)."""
    api_config: dict[str, Any] = {
        k: v
        for k, v in ({"replicas": 4, "cpu_limit_cores": Decimal("0.5")} | (api or {})).items()
        if v is not None
    }
    db_config: dict[str, Any] = {"max_connections": 100, "storage_bytes": 10**12} | (db or {})
    reads: dict[str, Any] = {"traffic_ratio": 1, "access": "read"} | (
        {"pool_size": pool} if pool is not None else {}
    )
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api", configuration=Configuration(api_config)),
            node("db", NodeKind.DATABASE, configuration=Configuration(db_config)),
        ),
        connections=(
            connection(
                "web-api",
                "web",
                "api",
                kind=ConnectionKind.REQUEST,
                protocol="https",
                interaction=Interaction.SYNCHRONOUS,
                configuration=Configuration({"traffic_ratio": 1}),
            ),
            connection("api-db", "api", "db", configuration=Configuration(reads)),
            connection(
                "api-db-w",
                "api",
                "db",
                protocol="pg-w",
                configuration=Configuration({"traffic_ratio": 1, "access": "write"}),
            ),
        ),
    )


def run(ir: ArchitectureIR, load: WorkloadProfile | None = None) -> CapacityResult:
    request = AnalysisRequest(uuid.UUID(int=1), 1, load or workload())
    return analyze(CapacityContext(ir, REVISION, request), default_registry(), propagate)


def estimates(result: CapacityResult, node_id: str, resource: str, *, limits: bool) -> list[Estimate]:
    component = next(c for c in result.components if c.node_id == node_id)
    return [e for e in (component.limits if limits else component.resources) if e.resource == resource]


def one(result: CapacityResult, node_id: str, resource: str, *, limits: bool) -> Estimate:
    [found] = estimates(result, node_id, resource, limits=limits)
    return found


# --- throughput ------------------------------------------------------------------------------------


def test_a_declared_total_throughput() -> None:
    limit = one(run(shop(api={"throughput_limit_per_second": 1200})), "api", "work_rate", limits=True)
    assert (limit.quantity, limit.source, limit.basis) == (
        Quantity.of("1200", RPS),
        Source.DECLARED,
        "configuration.throughput_limit_per_second",
    )


def test_per_replica_throughput_scales_linearly_and_says_so() -> None:
    result = run(shop(api={"throughput_per_replica_per_second": 300}))
    limit = one(result, "api", "work_rate", limits=True)
    assert (limit.quantity, limit.source) == (Quantity.of("1200", RPS), Source.MODEL_ESTIMATE)  # 300 x 4
    assert {(e.label, e.value) for e in limit.inputs} == {
        ("throughput_per_replica_per_second", "300"),
        ("replicas", "4"),
    }
    api = next(c for c in result.components if c.node_id == "api")
    assert any("linearly" in n for n in api.notes)


def test_without_declared_throughput_there_is_no_throughput_limit() -> None:
    result = run(shop())
    assert estimates(result, "api", "work_rate", limits=True) == []  # no model ran: nothing invented
    api = next(c for c in result.components if c.node_id == "api")
    assert "configuration.throughput_limit_per_second" in api.missing


def test_the_work_unit_follows_the_demand() -> None:
    limit = one(run(shop(db={"throughput_limit_per_second": 5000})), "db", "work_rate", limits=True)
    assert limit.quantity == Quantity.of("5000", "operations/second")


# --- cpu -------------------------------------------------------------------------------------------


def test_cpu_demand_from_a_declared_cost_per_request() -> None:
    result = run(shop(api={"cpu_core_seconds_per_request": Decimal("0.002")}))
    used = one(result, "api", "cpu", limits=False)
    assert (used.quantity, used.source) == (Quantity.of("2", "cores"), Source.MODEL_ESTIMATE)  # 1000 x 2 ms
    limit = one(result, "api", "cpu", limits=True)
    assert limit.quantity == Quantity.of("2", "cores")  # 4 replicas x 0.5


def test_cpu_limit_is_unknown_without_declared_cores() -> None:
    result = run(shop(api={"cpu_core_seconds_per_request": Decimal("0.002"), "cpu_limit_cores": None}))
    limit = one(result, "api", "cpu", limits=True)
    assert (limit.known, limit.source, limit.missing) == (
        False,
        Source.UNKNOWN,
        ("configuration.cpu_limit_cores",),
    )
    assert one(result, "api", "cpu", limits=False).known  # the demand is still known


# --- connections -----------------------------------------------------------------------------------


def test_connection_pools_against_the_declared_maximum() -> None:
    result = run(shop(api={"autoscaling_max_replicas": 10}))
    used = one(result, "db", "connections", limits=False)
    assert used.quantity == Quantity.of("40", "connections")  # pool 10 x 4 replicas
    assert one(result, "db", "connections", limits=True).quantity == Quantity.of("100", "connections")
    db = next(c for c in result.components if c.node_id == "db")
    assert any("100 connections" in n for n in db.notes)  # at the autoscaling maximum


def test_an_undeclared_pool_leaves_connections_unknown() -> None:
    used = one(run(shop(pool=None)), "db", "connections", limits=False)
    assert (used.known, used.missing) == (False, ("api.pool_size",))  # one pool per source


# --- storage ---------------------------------------------------------------------------------------


def test_storage_growth_and_fill_time_from_empty() -> None:
    result = run(shop())
    growth = one(result, "db", "storage_growth", limits=False)
    assert growth.quantity == Quantity.of("400000", "B/s")  # 200 writes/s x 2 KB
    fill = one(result, "db", "time_to_full", limits=False)
    assert fill.quantity == Quantity.rounded(Decimal(10**12) / 400_000 / 86_400, "d")
    assert "from empty" in fill.basis


def test_retention_bounds_what_a_queue_keeps() -> None:
    ir = ArchitectureIR(
        "Events",
        nodes=(
            node("producer"),
            node(
                "q",
                NodeKind.QUEUE,
                configuration=Configuration(
                    {"storage_bytes": 10**12, "retention_seconds": 86_400, "replication_factor": 3}
                ),
            ),
        ),
        connections=(
            connection(
                "producer-q",
                "producer",
                "q",
                kind=ConnectionKind.PUBLISH,
                protocol="kafka",
                configuration=Configuration({"traffic_ratio": 1}),
            ),
        ),
    )
    load = WorkloadProfile(
        "Orders",
        WorkloadType.EVENT_STREAM,
        peak_rate=Quantity.of("100", "events/second"),
        request_payload=Quantity.of("1", "KB"),
    )
    request = AnalysisRequest(uuid.UUID(int=1), 1, load, entries=("producer",))
    result = analyze(CapacityContext(ir, REVISION, request), default_registry(), propagate)
    assert one(result, "q", "storage_growth", limits=False).quantity == Quantity.of(
        "300000", "B/s"
    )  # 100 x 1 KB x 3
    assert one(result, "q", "storage", limits=False).quantity == Quantity.of(300_000 * 86_400, "B")


def test_storage_needs_reads_and_writes_told_apart() -> None:
    ir = ArchitectureIR(
        "Unsplit",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("api"),
            node("db", NodeKind.DATABASE, configuration=Configuration({"storage_bytes": 10**9})),
        ),
        connections=(
            connection(
                "web-api",
                "web",
                "api",
                kind=ConnectionKind.REQUEST,
                protocol="https",
                configuration=Configuration({"traffic_ratio": 1}),
            ),
            connection("api-db", "api", "db", configuration=Configuration({"traffic_ratio": 1})),
        ),
    )
    growth = one(run(ir), "db", "storage_growth", limits=False)
    assert (growth.known, growth.missing) == (False, ("configuration.access",))


# --- bandwidth -------------------------------------------------------------------------------------


def test_bandwidth_from_payloads() -> None:
    result = run(shop(api={"network_bandwidth_bytes_per_second": 10**8}))
    used = one(result, "api", "bandwidth", limits=False)
    assert used.quantity == Quantity.of(1000 * 2000 + 1000 * 10_000, "B/s")
    assert one(result, "api", "bandwidth", limits=True).quantity == Quantity.of(10**8, "B/s")
    assert not one(result, "db", "bandwidth", limits=True).known  # nothing declared: unknown, not 0


def test_bandwidth_needs_response_sizes_for_requests() -> None:
    used = one(run(shop(), workload(response_payload=None)), "api", "bandwidth", limits=False)
    assert used.missing == ("workload.response_payload",)


# --- the whole analysis ----------------------------------------------------------------------------


def test_incomplete_demand_never_becomes_a_number() -> None:
    ir = shop(api={"cpu_core_seconds_per_request": Decimal("0.002")})
    kept = tuple(c for c in ir.connections if c.id != "web-api")
    no_share = connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https")
    undeclared = ArchitectureIR(ir.name, nodes=ir.nodes, connections=(no_share, *kept))
    used = one(run(undeclared), "api", "cpu", limits=False)
    assert (used.known, used.missing) == (False, ("demand",))


def test_components_without_declared_inputs_are_insufficient_and_nothing_is_zero() -> None:
    ir = ArchitectureIR(
        "Bare",
        nodes=(node("web", NodeKind.CLIENT), node("api")),
        connections=(
            connection(
                "web-api",
                "web",
                "api",
                kind=ConnectionKind.REQUEST,
                protocol="https",
                configuration=Configuration({"traffic_ratio": 1}),
            ),
        ),
    )
    result = run(ir, workload(request_payload=None))
    [api] = result.components
    assert api.status is ComponentStatus.INSUFFICIENT_INPUT
    assert all(not e.known for e in (*api.limits, *api.resources))


def test_models_are_versioned_and_deterministic() -> None:
    ids = [m.meta.id for m in default_registry().models()]
    assert (
        ids
        == sorted(ids)
        == [
            "connection-pool",
            "cpu-demand",
            "declared-throughput",
            "network-bandwidth",
            "replica-throughput",
            "storage-growth",
        ]
    )
    ir = shop(
        api={"throughput_per_replica_per_second": 300, "cpu_core_seconds_per_request": Decimal("0.002")}
    )
    first, again = run(ir), run(ir)
    assert first == again
    assert first.fingerprint == again.fingerprint


@pytest.mark.parametrize("value", [0, 10**14])
def test_boundary_values(value: int) -> None:
    limit = one(run(shop(api={"throughput_limit_per_second": value})), "api", "work_rate", limits=True)
    assert limit.quantity == Quantity.of(value, RPS)  # zero is a declared zero, not an unknown
