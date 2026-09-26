"""Utilization, headroom and bottleneck candidates (Milestone 7, phase 5)."""

import uuid
from decimal import Decimal
from itertools import pairwise
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.results import (
    BottleneckCondition,
    CapacityResult,
    Certainty,
    ComponentResult,
    Utilization,
    UtilizationState,
)
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.validation.options import RevisionInfo
from engines.capacity.context import CapacityContext
from engines.capacity.engine import analyze
from engines.capacity.registry import default_registry
from engines.capacity.traffic import propagate
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "f" * 64)
RPS = "requests/second"


def load(peak: str = "1000", target: str | None = None) -> WorkloadProfile:
    return WorkloadProfile(
        "Peak",
        WorkloadType.REQUEST_RESPONSE,
        peak_rate=Quantity.of(peak, RPS),
        target_utilization=Decimal(target) if target else None,
    )


def call(cid: str, source: str, target: str, ratio: str = "1", **extra: Any) -> Any:
    return connection(
        cid,
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol="https",
        interaction=extra.pop("interaction", Interaction.SYNCHRONOUS),
        configuration=Configuration({"traffic_ratio": Decimal(ratio)}),
        **extra,
    )


def chain(
    *services: tuple[str, dict[str, Any]], connections: tuple[Any, ...] | None = None
) -> ArchitectureIR:
    nodes = (
        node("web", NodeKind.CLIENT),
        *(node(name, configuration=Configuration(cfg)) for name, cfg in services),
    )
    ids = ["web", *(name for name, _ in services)]
    links = connections or tuple(call(f"{a}-{b}", a, b) for a, b in pairwise(ids))
    return ArchitectureIR("Chain", nodes=nodes, connections=links)


def run(ir: ArchitectureIR, workload: WorkloadProfile | None = None) -> CapacityResult:
    request = AnalysisRequest(uuid.UUID(int=1), 1, workload or load())
    return analyze(CapacityContext(ir, REVISION, request), default_registry(), propagate)


def component(result: CapacityResult, node_id: str) -> ComponentResult:
    return next(c for c in result.components if c.node_id == node_id)


def throughput(result: CapacityResult, node_id: str) -> Utilization:
    return next(u for u in component(result, node_id).utilization if u.resource == "work_rate")


def limit(value: int | str) -> dict[str, Any]:
    return {"throughput_limit_per_second": Decimal(value)}


@pytest.mark.parametrize(
    ("capacity", "state", "ratio", "headroom", "condition"),
    [
        (2000, UtilizationState.BELOW, Decimal("0.5"), Decimal(1000), None),
        (1000, UtilizationState.AT, Decimal(1), Decimal(0), BottleneckCondition.AT_CAPACITY),
        (800, UtilizationState.ABOVE, Decimal("1.25"), Decimal(-200), BottleneckCondition.EXCEEDS_CAPACITY),
        (0, UtilizationState.NO_CAPACITY, None, Decimal(-1000), BottleneckCondition.NO_CAPACITY),
    ],
)
def test_demand_against_declared_capacity(
    capacity: int,
    state: UtilizationState,
    ratio: Decimal | None,
    headroom: Decimal,
    condition: BottleneckCondition | None,
) -> None:
    result = run(chain(("api", limit(capacity))))
    u = throughput(result, "api")
    assert (u.demand, u.capacity) == (Quantity.of("1000", RPS), Quantity.of(capacity, RPS))
    assert (u.state, u.ratio, u.headroom) == (state, ratio, headroom)
    if condition is None:
        assert result.bottlenecks == ()
    else:
        [b] = result.bottlenecks
        assert (b.node_id, b.condition, b.certainty) == ("api", condition, Certainty.MODELED)
        assert any(e.label == "capacity" for e in b.evidence)


def test_a_target_utilization_flags_what_is_above_it() -> None:
    result = run(chain(("api", limit(1250))), load(target="0.7"))
    u = throughput(result, "api")
    assert (u.ratio, u.headroom_to_target) == (Decimal("0.8"), Decimal(-125))  # 0.7 x 1250 - 1000
    [b] = result.bottlenecks
    assert (b.condition, b.certainty) == (BottleneckCondition.ABOVE_TARGET, Certainty.MODELED)
    assert run(chain(("api", limit(2000))), load(target="0.7")).bottlenecks == ()


def test_the_lowest_known_limit_binds() -> None:
    config = limit(5000) | {"throughput_per_replica_per_second": 300, "replicas": 3}
    result = run(chain(("api", config)))
    assert throughput(result, "api").capacity == Quantity.of("900", RPS)
    [b] = result.bottlenecks
    assert ("binding_limit", "replica-throughput: throughput_per_replica_per_second * replicas") in {
        (e.label, e.value) for e in b.evidence
    }


def test_unknown_capacity_on_a_waited_path_is_a_candidate_not_a_verdict() -> None:
    result = run(chain(("api", limit(2000)), ("orders", {})))
    u = throughput(result, "orders")
    assert (u.state, u.ratio, u.headroom) == (UtilizationState.UNKNOWN, None, None)
    [b] = result.bottlenecks
    assert (b.node_id, b.condition, b.certainty) == (
        "orders",
        BottleneckCondition.UNKNOWN_CAPACITY,
        Certainty.CANDIDATE,
    )
    assert "may be" in b.explanation


def test_unknown_capacity_behind_an_asynchronous_link_is_not_flagged() -> None:
    links = (
        call("web-api", "web", "api"),
        call("api-mail", "api", "mail", interaction=Interaction.ASYNCHRONOUS),
    )
    result = run(chain(("api", limit(2000)), ("mail", {}), connections=links))
    assert result.bottlenecks == ()
    assert throughput(result, "mail").state is UtilizationState.UNKNOWN  # still reported as unknown


def test_multiple_candidates_are_ordered_modeled_first_then_worst() -> None:
    result = run(chain(("gw", limit(900)), ("api", limit(500)), ("orders", {})))
    assert [(b.node_id, b.certainty) for b in result.bottlenecks] == [
        ("api", Certainty.MODELED),  # utilization 2.0
        ("gw", Certainty.MODELED),  # 1.11
        ("orders", Certainty.CANDIDATE),
    ]


def test_serial_and_parallel_paths_and_the_saturation_multiple() -> None:
    links = (
        call("web-gw", "web", "gw"),
        call("gw-a", "gw", "a", "0.5"),
        call("gw-b", "gw", "b", "0.5"),
    )
    result = run(chain(("gw", limit(4000)), ("a", limit(1000)), ("b", limit(2500)), connections=links))
    assert [throughput(result, n).ratio for n in ("gw", "a", "b")] == [
        Decimal("0.25"),
        Decimal("0.5"),
        Decimal("0.2"),
    ]
    summary = result.summary
    assert (summary.saturation_multiple, summary.saturation_complete) == (
        Decimal(2),
        True,
    )  # "a" saturates at 2x
    assert summary.highest_utilization == Decimal("0.5")


def test_the_saturation_multiple_is_incomplete_when_a_capacity_is_unknown() -> None:
    summary = run(chain(("api", limit(4000)), ("orders", {}))).summary
    assert (summary.saturation_multiple, summary.saturation_complete) == (Decimal(4), False)


def test_incomplete_demand_is_never_compared() -> None:
    links = (
        call("web-api", "web", "api"),
        connection("api-x", "api", "x", kind=ConnectionKind.REQUEST, protocol="https"),
    )
    result = run(chain(("api", limit(2000)), ("x", limit(10)), connections=links))
    u = throughput(result, "x")
    assert (u.demand, u.state) == (None, UtilizationState.UNKNOWN)
    assert "x" not in {b.node_id for b in result.bottlenecks}  # 10 rps might be plenty, or not
    assert ("x", "demand_incomplete") in {(e.element_id, e.code) for e in result.unsupported}
    assert result.summary.saturation_complete is False


def test_other_resources_are_paired_by_name_and_unit() -> None:
    config = limit(5000) | {
        "replicas": 2,
        "cpu_limit_cores": 1,
        "cpu_core_seconds_per_request": Decimal("0.003"),
    }
    result = run(chain(("api", config)))
    cpu = next(u for u in component(result, "api").utilization if u.resource == "cpu")
    assert (cpu.demand, cpu.capacity, cpu.ratio) == (
        Quantity.of("3", "cores"),
        Quantity.of("2", "cores"),
        Decimal("1.5"),
    )
    assert [(b.resource, b.condition) for b in result.bottlenecks] == [
        ("cpu", BottleneckCondition.EXCEEDS_CAPACITY)
    ]


def test_bottleneck_order_and_results_are_stable() -> None:
    ir = chain(("gw", limit(900)), ("api", limit(500)), ("orders", {}))
    assert run(ir) == run(ir)
    assert run(ir).fingerprint == run(ir).fingerprint
