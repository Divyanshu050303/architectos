"""Execution safety and resource limits (Milestone 12, phase 9): configurable limits within hard caps,
checked before expensive work, predictable refusals, output bounded by a stated cut, adversarial
inputs refused or handled, and the largest architecture evaluated in bounded time."""

import time
import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import MAX_CONNECTIONS, ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.errors import InvalidSimulationRequest
from core.domain.simulations.limits import DEFAULT_LIMITS, SimulationLimits
from core.domain.simulations.results import MAX_DELTAS, SimulationResult
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.values import AnalysisKind, FailureKind, RunState
from core.domain.validation.options import RevisionInfo
from engines.simulation.context import SimulationContext
from engines.simulation.engine import analyze
from engines.simulation.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.simulation.test_simulation_capacity import WORKLOAD, shop

A = AnalysisKind
K = FailureKind
DOUBLE = Scenario("Double", workload=WorkloadChange(growth=Decimal(2)))


def simulate(
    scenario: Scenario,
    limits: SimulationLimits = DEFAULT_LIMITS,
    ir: ArchitectureIR | None = None,
    **fields: Any,
) -> SimulationResult:
    ir = ir or shop()
    request = SimulationRequest(uuid.UUID(int=1), 1, scenario, **fields)
    context = SimulationContext(ir, RevisionInfo("arch-1", 1, content_hash(ir)), request)
    return analyze(context, default_registry(), limits)


def refusal(scenario: Scenario, limits: SimulationLimits) -> dict[str, Any]:
    with pytest.raises(InvalidSimulationRequest) as raised:
        simulate(scenario, limits)
    return dict(raised.value.details)


def test_limits_are_configurable_within_hard_caps() -> None:
    assert DEFAULT_LIMITS.to_dict() == {
        "max_changes": 50,
        "max_failures": 50,
        "max_affected_components": 1000,
        "max_deltas": MAX_DELTAS,
    }
    bad: list[dict[str, Any]] = [
        {"max_changes": 0},
        {"max_failures": 51},
        {"max_deltas": MAX_DELTAS + 1},
        {"max_changes": True},
    ]
    for values in bad:
        with pytest.raises(ValueError, match="must be between 1 and"):
            SimulationLimits(**values)


def test_over_limit_scenarios_are_refused_before_anything_runs() -> None:
    changes = (ConfigurationChange("api", "replicas", 3), ConfigurationChange("db", "replicas", 2))
    too_many = refusal(Scenario("Two", changes=changes), SimulationLimits(max_changes=1))
    assert too_many == {"field": "scenario.changes", "reason": "too_many", "limit": 1}
    outage = Scenario("Out", failures=(Failure(K.COMPONENT, "api"), Failure(K.COMPONENT, "db")))
    assert refusal(outage, SimulationLimits(max_failures=1))["reason"] == "too_many"
    affected = refusal(outage, SimulationLimits(max_affected_components=1))
    assert affected == {"field": "scenario.failures", "reason": "too_many_affected", "limit": 1}


def test_the_output_is_cut_in_canonical_order_and_the_cut_is_stated() -> None:
    full = simulate(DOUBLE, workload=WORKLOAD, analyses=(A.CAPACITY,))
    cut = simulate(DOUBLE, SimulationLimits(max_deltas=3), workload=WORKLOAD, analyses=(A.CAPACITY,))
    assert len(full.deltas) > 3
    assert cut.deltas == full.deltas[:3]
    [truncated] = [u for u in cut.unsupported if u.code == "deltas_truncated"]
    assert f"{len(full.deltas) - 3} comparisons" in truncated.message
    assert cut.status.value == "partial"  # never complete when something is left out


@pytest.mark.parametrize(
    "raw",
    [
        {"name": "S", "changes": [{"element_id": "x" * 129, "property": "replicas", "value": 2}]},
        {"name": "S", "changes": [{"element_id": "api", "property": "replicas", "value": "2; DROP"}]},
        {"name": "S", "workload": {"growth": "1e400"}},
        {"name": "S", "workload": {"growth": "NaN"}},
        {"name": "S", "failures": [{"kind": "zone", "target": "../../etc"}]},
        {"name": "S", "failures": [{"kind": "component", "target": "api"}] * 51},
        {"name": "<script>", "failures": [{"kind": "component", "target": "api"}]},
    ],
)
def test_malformed_or_adversarial_inputs_are_refused(raw: dict[str, Any]) -> None:
    with pytest.raises(InvalidSimulationRequest):
        Scenario.from_dict(raw)


def test_an_extreme_but_valid_workload_is_handled_not_crashed() -> None:
    extreme = Scenario("Huge", workload=WorkloadChange(growth=Decimal("999999999999")))
    [run] = simulate(extreme, workload=WORKLOAD, analyses=(A.CAPACITY,)).runs
    assert run.state in {RunState.COMPLETED, RunState.PARTIAL}


def _link(a: int, b: int) -> Connection:
    return connection(
        f"c{a}-{b}",
        f"s{a}",
        f"s{b}",
        kind=ConnectionKind.REQUEST,
        protocol="https",
        interaction=Interaction.SYNCHRONOUS,
        configuration=Configuration({"traffic_ratio": Decimal("0.01")}),
    )


def _largest() -> ArchitectureIR:
    count = 1000
    values: dict[str, ConfigValue] = {
        "replicas": 2,
        "throughput_per_replica_per_second": 100,
        "availability": Decimal("0.999"),
        "failure_independence": "independent",
    }
    nodes = [node("web", NodeKind.CLIENT)]
    for i in range(count - 1):
        zones: dict[str, ConfigValue] = {
            "availability_zones": ("eu-west-1a",) if i % 2 else ("eu-west-1a", "eu-west-1b")
        }
        nodes.append(node(f"s{i}", configuration=Configuration(values | zones)))
    links = [connection("web-s0", "web", "s0", kind=ConnectionKind.REQUEST, protocol="https")]
    seen: set[tuple[int, int]] = set()
    i = 0
    while len(links) < MAX_CONNECTIONS:
        a, b = i % (count - 1), (i // (count - 1) + 1 + i) % (count - 1)
        i += 1
        if b not in (a, 0) and (a, b) not in seen:
            seen.add((a, b))
            links.append(_link(a, b))
    return ArchitectureIR("Largest", nodes=tuple(nodes), connections=tuple(links))


def test_the_largest_architecture_is_evaluated_in_bounded_time() -> None:
    """1,000 nodes and 5,000 connections, a doubled workload and a zone failure reaching every
    component: measured about 2 s; the bound leaves room for slow machines."""
    scenario = Scenario(
        "Everything", workload=WorkloadChange(growth=Decimal(2)), failures=(Failure(K.ZONE, "eu-west-1a"),)
    )
    started = time.perf_counter()
    result = simulate(scenario, ir=_largest(), workload=WORKLOAD)
    assert time.perf_counter() - started < 30
    assert len(result.components) == 999
