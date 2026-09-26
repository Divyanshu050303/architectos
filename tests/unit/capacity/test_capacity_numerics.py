"""Numerical edge cases (Milestone 7, phase 8): boundaries, zero, huge values, rounding, and inputs
that must be refused rather than crash."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.errors import InvalidQuantity, InvalidScenario
from core.domain.capacity.results import UtilizationState
from core.domain.capacity.scenarios import Scenario
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.validation.options import RevisionInfo
from engines.capacity.service import DeterministicCapacityEngine
from tests.unit.architecture_ir.builders import connection, node

RPS = "requests/second"
REVISION = RevisionInfo("a", 1, "0" * 64)


def architecture(**api: Any) -> ArchitectureIR:
    return ArchitectureIR(
        "Edge",
        nodes=(node("web", NodeKind.CLIENT), node("api", configuration=Configuration(api))),
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
        ),
    )


def analyze(ir: ArchitectureIR, peak: str, *scenarios: Scenario, **workload: Any) -> Any:
    load = WorkloadProfile("P", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of(peak, RPS), **workload)
    return DeterministicCapacityEngine().analyze(
        ir, REVISION, AnalysisRequest(uuid.UUID(int=1), 1, load), scenarios
    )


def throughput(output: Any) -> Any:
    [api] = output.result.components
    return next(u for u in api.utilization if u.resource == "work_rate")


def test_tiny_and_huge_rates_keep_exact_ratios() -> None:
    tiny = throughput(
        analyze(architecture(throughput_limit_per_second=Decimal("0.000000003")), "0.000000001")
    )
    assert tiny.ratio == Decimal("0.333333333")
    huge = throughput(
        analyze(architecture(throughput_limit_per_second=Decimal("999999999999999")), "500000000000000")
    )
    assert huge.ratio == Decimal("0.500000000") or huge.ratio == Decimal("0.5")


def test_a_ratio_is_never_clamped_even_when_enormous() -> None:
    u = throughput(analyze(architecture(throughput_limit_per_second=Decimal("0.001")), "100000"))
    assert (u.state, u.ratio) == (UtilizationState.ABOVE, Decimal(100_000_000))


def test_zero_capacity_and_zero_demand() -> None:
    zero = throughput(analyze(architecture(throughput_limit_per_second=0), "10"))
    assert (zero.state, zero.ratio) == (UtilizationState.NO_CAPACITY, None)
    ir = ArchitectureIR(
        "Idle",
        nodes=architecture().nodes,
        connections=(
            connection(
                "web-api",
                "web",
                "api",
                kind=ConnectionKind.REQUEST,
                protocol="https",
                configuration=Configuration({"traffic_ratio": 0}),
            ),
        ),
    )
    idle = analyze(ir, "10")
    [api] = idle.result.components
    assert api.demand[0].quantity.value == 0


def test_values_beyond_what_a_quantity_holds_become_unknown_not_errors() -> None:
    output = analyze(
        architecture(throughput_per_replica_per_second=Decimal("999999999999999"), replicas=10), "1"
    )
    [api] = output.result.components
    [limit] = [e for e in api.limits if e.resource == "work_rate"]
    assert (limit.known, limit.missing) == (False, ("magnitude",))


def test_growth_beyond_the_limits_is_refused_not_a_crash() -> None:
    ir = architecture(throughput_limit_per_second=1000)
    with pytest.raises(InvalidScenario) as raised:
        analyze(ir, "1000", Scenario("Explode", growth_rate=Decimal(1000), periods=120))
    assert raised.value.details == {"field": "growth", "reason": "out_of_range"}
    with pytest.raises(InvalidScenario):
        analyze(ir, "0.000000001", Scenario("Vanish", growth=Decimal("0.000000001")))  # rounds to 0


def test_rounding_is_half_even_at_nine_places_and_only_for_presentation_of_quantities() -> None:
    third = throughput(analyze(architecture(throughput_limit_per_second=3), "1"))
    assert (third.ratio, third.relative_headroom) == (Decimal("0.333333333"), Decimal("0.666666667"))
    assert Quantity.rounded(Decimal("2.0000000005"), RPS).value == Decimal(2)
    assert Quantity.rounded(Decimal("2.0000000015"), RPS).value == Decimal("2.000000002")


def test_a_scaling_option_too_large_to_state_is_unsupported() -> None:
    ir = architecture(throughput_per_replica_per_second=Decimal("0.000000001"), replicas=1)
    output = analyze(ir, "999999999")
    assert [o for o in output.scaling if o.resource == "work_rate"] == []
    assert [(u.element_id, u.code) for u in output.unsupported_scaling] == [("api", "scaling_unsupported")]


@pytest.mark.parametrize("value", ["1e15", "-1", "NaN", "0.0000000001"])
def test_invalid_numbers_are_refused(value: str) -> None:
    with pytest.raises(InvalidQuantity):
        Quantity.of(value, RPS)


def largest() -> ArchitectureIR:
    """The IR's maximum: 1000 nodes, a web client feeding a 5000-connection DAG of services."""
    from core.architecture_ir.edge import Connection  # noqa: PLC0415 - only this fixture needs it
    from core.architecture_ir.model import MAX_CONNECTIONS, MAX_NODES  # noqa: PLC0415
    from core.architecture_ir.node import Node  # noqa: PLC0415

    config = Configuration(
        {
            "replicas": 3,
            "throughput_per_replica_per_second": 500,
            "cpu_limit_cores": 1,
            "cpu_core_seconds_per_request": Decimal("0.001"),
        }
    )
    nodes = [node("web", NodeKind.CLIENT)] + [
        Node(f"n{i:04d}", NodeKind.SERVICE, f"S{i}", configuration=config) for i in range(MAX_NODES - 1)
    ]
    share = Configuration({"traffic_ratio": Decimal("0.2")})
    links = [
        Connection(
            "entry",
            "web",
            "n0000",
            ConnectionKind.REQUEST,
            "https",
            configuration=Configuration({"traffic_ratio": 1}),
        )
    ]
    for i in range(MAX_NODES - 1):
        for j in range(1, 6):
            target = i + j * 7
            if target < MAX_NODES - 1 and len(links) < MAX_CONNECTIONS:
                links.append(
                    Connection(
                        f"c{len(links):05d}",
                        f"n{i:04d}",
                        f"n{target:04d}",
                        ConnectionKind.REQUEST,
                        f"p{len(links)}",
                        configuration=share,
                    )
                )
    return ArchitectureIR("Largest", nodes=tuple(nodes), connections=tuple(links))


def test_the_largest_architecture_is_analyzed_completely_and_deterministically() -> None:
    ir = largest()
    first = analyze(ir, "1000", Scenario("Double", growth=Decimal(2)))
    again = analyze(ir, "1000", Scenario("Double", growth=Decimal(2)))
    assert first.result.fingerprint == again.result.fingerprint
    assert first.result.status.value == "completed"
    assert len(first.result.components) == 999
    assert [s.result.fingerprint for s in first.scenarios] == [s.result.fingerprint for s in again.scenarios]
