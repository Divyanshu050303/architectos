"""Workload and capacity scenarios (Milestone 12, phase 5), evaluated by the real Capacity Engine:
outputs traceable to its own results, baseline and scenario on the same models, no linear scaling a
model does not state, unknown demand never zero, and what capacity cannot evaluate reported."""

import uuid
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.results import SYSTEM, Delta, SimulationResult
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.values import AnalysisKind, FailureKind, RunState
from core.domain.validation.options import RevisionInfo
from engines.capacity.service import DeterministicCapacityEngine
from engines.simulation.capacity import capacity_scenario
from engines.simulation.context import SimulationContext
from engines.simulation.engine import analyze
from engines.simulation.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

A = AnalysisKind
D = Decimal
RPS = "requests/second"
WORKLOAD = WorkloadProfile("Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of(100, RPS))


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    config: dict[str, ConfigValue] = values
    return node(node_id, kind, configuration=Configuration(config))


def link(source: str, target: str, kind: ConnectionKind, protocol: str, routed: bool) -> Connection:
    share: dict[str, ConfigValue] = {"traffic_ratio": Decimal(1)} if routed else {}
    return connection(
        f"{source}-{target}", source, target, kind=kind, protocol=protocol, configuration=Configuration(share)
    )


def shop(routed: bool = True) -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            component("api", replicas=2, throughput_per_replica_per_second=60),
            component("db", NodeKind.DATABASE, replicas=1, throughput_limit_per_second=500),
        ),
        connections=(
            link("web", "api", ConnectionKind.REQUEST, "https", routed),
            link("api", "db", ConnectionKind.DATA_ACCESS, "postgresql", routed),
        ),
    )


def simulate(scenario: Scenario, ir: ArchitectureIR | None = None, **fields: Any) -> SimulationResult:
    ir = ir or shop()
    fields.setdefault("workload", WORKLOAD)
    fields.setdefault("analyses", (A.CAPACITY,))
    request = SimulationRequest(uuid.UUID(int=1), 1, scenario, **fields)
    context = SimulationContext(ir, RevisionInfo("arch-1", 1, content_hash(ir)), request)
    return analyze(context, default_registry())


def delta(result: SimulationResult, element: str, metric: str) -> Delta:
    [found] = [d for d in result.deltas if (d.element_id, d.metric) == (element, metric)]
    return found


DOUBLE = Scenario("Double", workload=WorkloadChange(growth=Decimal(2)))


def test_fixture_1_workload_increase_with_supported_inputs() -> None:
    result = simulate(DOUBLE)
    [run] = result.runs
    assert run.state is RunState.COMPLETED
    demand = delta(result, "api", "work_rate.demand")
    assert (demand.unit, demand.baseline, demand.scenario, demand.percentage) == (RPS, D(100), D(200), D(100))
    utilization = delta(result, "api", "work_rate.utilization")
    assert (utilization.baseline, utilization.scenario) == (Decimal("0.833333333"), Decimal("1.666666667"))
    assert delta(result, SYSTEM, "bottlenecks").scenario == 1  # api exceeds its capacity
    assert any(e.label == "capacity.new_bottleneck.api.work_rate" for e in result.trace)
    assert ("capacity.workload_multiplier", "2") in [(e.label, e.value) for e in result.assumptions]


def test_outputs_are_traceable_to_the_capacity_engines_own_results() -> None:
    result = simulate(DOUBLE)
    [run] = result.runs
    ir = shop()
    scenario, _ = capacity_scenario(DOUBLE)
    request = AnalysisRequest(uuid.UUID(int=1), 1, WORKLOAD)
    output = DeterministicCapacityEngine().analyze(
        ir, RevisionInfo("arch-1", 1, content_hash(ir)), request, (scenario,)
    )
    assert run.model_set == output.result.model_set  # the same models on both sides
    assert (run.baseline_fingerprint, run.scenario_fingerprint) == (
        output.result.fingerprint,
        output.scenarios[0].result.fingerprint,
    )


def test_fixture_2_a_workload_increase_without_workload_parameters() -> None:
    result = simulate(DOUBLE, workload=None)
    [run] = result.runs
    assert (run.state, run.reason) == (RunState.UNSUPPORTED, "no_workload")
    assert result.deltas == ()  # nothing invented


def test_fixture_3_a_replica_change_follows_the_declared_model() -> None:
    result = simulate(Scenario("Scale", changes=(ConfigurationChange("api", "replicas", 4),)))
    capacity = delta(result, "api", "work_rate.capacity")
    assert (capacity.baseline, capacity.scenario) == (D(120), D(240))  # 60 per replica, as declared
    assert result.components[0].changes[0].value == "2 -> 4"


def test_no_linear_scaling_where_the_model_does_not_state_it() -> None:
    result = simulate(Scenario("Scale db", changes=(ConfigurationChange("db", "replicas", 3),)))
    capacity = delta(result, "db", "work_rate.capacity")
    assert (capacity.baseline, capacity.scenario, capacity.difference) == (
        D(500),
        D(500),
        D(0),
    )  # a declared total


def test_fixture_6_a_resource_configuration_change() -> None:
    bigger = ConfigurationChange("db", "throughput_limit_per_second", 1000)
    capacity = delta(simulate(Scenario("Bigger db", changes=(bigger,))), "db", "work_rate.capacity")
    assert (capacity.unit, capacity.baseline, capacity.scenario, capacity.percentage) == (
        "operations/second",
        D(500),
        D(1000),
        D(100),
    )


def test_unknown_demand_is_never_zero() -> None:
    result = simulate(DOUBLE, ir=shop(routed=False))
    [run] = result.runs
    assert run.state is RunState.PARTIAL
    assert not [d for d in result.deltas if d.metric == "work_rate.demand"]  # unknown: no value, not 0
    assert {u.code for u in result.unsupported} >= {"demand_incomplete", "routing_unspecified"}
    assert delta(result, SYSTEM, "highest_utilization").comparable is False


def test_what_capacity_cannot_evaluate_is_reported() -> None:
    scenario = Scenario(
        "Mixed",
        workload=WorkloadChange(growth=Decimal(2)),
        changes=(ConfigurationChange("db", "throughput_limit_per_second", None),),
        failures=(Failure(FailureKind.CONNECTION, "api-db"),),
    )
    result = simulate(scenario)
    [run] = result.runs
    assert run.state is RunState.PARTIAL
    codes = {(u.element_id, u.code) for u in result.unsupported}
    assert {("db", "capacity_cannot_clear"), ("api-db", "failure_not_modeled")} <= codes


def test_a_capacity_simulation_is_deterministic() -> None:
    assert simulate(DOUBLE).to_dict() == simulate(DOUBLE).to_dict()
