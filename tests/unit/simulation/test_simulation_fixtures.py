"""The ten scenario fixtures end to end through the engine port (Milestone 12, phase 11): each one's
status, runs and what stays unknown; determinism of overlay, result, ordering, numbers, comparison and
traces (also with the architecture and scenario given in another order); the revision never modified;
no network, persistence or telemetry reached from the engine."""

import ast
import json
import random
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.cost.pricing import PricingSnapshot
from core.domain.engine_results import ModelSet
from core.domain.simulations.comparison import compare
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.errors import InvalidSimulationRequest
from core.domain.simulations.ports import SimulationOutput
from core.domain.simulations.results import SimulationResult
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.values import AnalysisKind, FailureKind, Impact, RunState, SimulationStatus
from core.domain.validation.options import RevisionInfo
from engines.simulation.service import DeterministicSimulationEngine
from tests.unit.architecture_ir.builders import node
from tests.unit.cost.test_cost_capacity import IR as COST_IR
from tests.unit.cost.test_cost_capacity import workload
from tests.unit.simulation import test_simulation_capacity as capacity
from tests.unit.simulation import test_simulation_cost as cost
from tests.unit.simulation import test_simulation_failure as failure

A = AnalysisKind
D = Decimal
ENGINE = DeterministicSimulationEngine()
DOUBLE = Scenario("Double", workload=WorkloadChange(growth=D(2)))


def unmodeled() -> ArchitectureIR:
    """The capacity shop with a cache that declares no capacity and a third party (fixture 9)."""
    base = capacity.shop()
    return ArchitectureIR(
        "Shop",
        nodes=(
            *base.nodes,
            capacity.component("cache", NodeKind.CACHE),
            node("stripe", NodeKind.EXTERNAL),
        ),
        connections=(
            *base.connections,
            capacity.link("api", "cache", ConnectionKind.DATA_ACCESS, "redis", True),
            capacity.link("api", "stripe", ConnectionKind.REQUEST, "https", True),
        ),
    )


@dataclass(frozen=True)
class Case:
    ir: ArchitectureIR
    scenario: Scenario
    fields: dict[str, Any] = field(default_factory=dict)
    snapshot: PricingSnapshot | None = None


def workload_case(scenario: Scenario, ir: ArchitectureIR | None = None, **fields: Any) -> Case:
    return Case(
        ir or capacity.shop(), scenario, {"workload": capacity.WORKLOAD, "analyses": (A.CAPACITY,)} | fields
    )


def failure_case(scenario: Scenario, ir: ArchitectureIR | None = None) -> Case:
    return Case(ir or failure.shop(), scenario, {"analyses": (A.RELIABILITY,)})


REPLICAS = Scenario("Scale", changes=(ConfigurationChange("api", "replicas", 4),))
CASES = {
    "1-workload-supported": workload_case(DOUBLE),
    "2-workload-missing": workload_case(DOUBLE, workload=None),
    "3-replicas": workload_case(REPLICAS),
    "4-component-failure": failure_case(failure.down("db")),
    "5-unknown-failover": failure_case(
        failure.down("api-a"), failure.shop(a={"failover_mode": None}, b={"failover_mode": None})
    ),
    "6-resources": workload_case(
        Scenario("Bigger db", changes=(ConfigurationChange("db", "throughput_limit_per_second", 1000),))
    ),
    "9-unsupported-behavior": workload_case(DOUBLE, unmodeled()),
    "cost": Case(
        COST_IR,
        cost.DOUBLE,
        {"workload": workload(), "pricing": cost.PRICING, "analyses": (A.COST,)},
        cost.SNAPSHOT,
    ),
    "mixed": Case(
        failure.shop(),
        Scenario(
            "Mixed",
            changes=(ConfigurationChange("db", "availability", D("0.9999")),),
            failures=(Failure(FailureKind.COMPONENT, "queue"), Failure(FailureKind.CONNECTION, "api-b-db")),
        ),
        {"analyses": (A.RELIABILITY,)},
    ),
}
EXPECTED = {  # status, then each analysis's run state (and reason when it did not run)
    "1-workload-supported": ("completed", {"capacity": ("completed", None)}),
    "2-workload-missing": ("unsupported", {"capacity": ("unsupported", "no_workload")}),
    "3-replicas": ("completed", {"capacity": ("completed", None)}),
    "4-component-failure": ("completed", {"reliability": ("completed", None)}),
    "5-unknown-failover": ("partial", {"reliability": ("partial", None)}),
    "6-resources": ("completed", {"capacity": ("completed", None)}),
    "9-unsupported-behavior": ("partial", {"capacity": ("partial", None)}),
    "cost": ("completed", {"cost": ("completed", None)}),
}


def run(case: Case, ir: ArchitectureIR | None = None, scenario: Scenario | None = None) -> SimulationOutput:
    ir = ir or case.ir
    request = SimulationRequest(cost.ARCHITECTURE, 1, scenario or case.scenario, **case.fields)
    revision = RevisionInfo(str(cost.ARCHITECTURE), 1, content_hash(ir))
    currency = "USD" if case.snapshot else None
    provider = "aws" if case.snapshot else None
    return ENGINE.simulate(ir, revision, request, (), case.snapshot, provider, currency)


def shuffled(case: Case, seed: int) -> tuple[ArchitectureIR, Scenario]:
    rng = random.Random(seed)  # noqa: S311 -- a reproducible order, not a secret
    nodes, connections = list(case.ir.nodes), list(case.ir.connections)
    rng.shuffle(nodes)
    rng.shuffle(connections)
    changes, failures = list(case.scenario.changes), list(case.scenario.failures)
    rng.shuffle(changes)
    rng.shuffle(failures)
    ir = ArchitectureIR(case.ir.name, nodes=tuple(nodes), connections=tuple(connections))
    return ir, replace(case.scenario, changes=tuple(changes), failures=tuple(failures))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_fixture_gives_its_status_and_runs(name: str) -> None:
    result = run(CASES[name]).result
    status, runs = EXPECTED[name]
    assert result.status.value == status
    assert {r.analysis.value: (r.state.value, r.reason) for r in result.runs} == runs
    for d in result.deltas:  # unknown is never 0 and never differenced
        if d.baseline is None or d.scenario is None:
            assert (d.difference, d.percentage) == (None, None)
    assert {x.code for x in result.limitations} >= {"model_based", "no_defaults"}


def test_fixture_9_a_partial_result_names_the_components_that_were_not_calculated() -> None:
    result = run(CASES["9-unsupported-behavior"]).result
    assert result.status is SimulationStatus.PARTIAL
    gaps = {u.element_id: u for u in result.unsupported}
    assert gaps["cache"].code == "capacity_insufficient_input"
    assert "configuration.throughput_limit_per_second" in gaps["cache"].missing
    assert gaps["stripe"].code == "capacity_insufficient_input"
    assert "api" not in gaps  # what was calculated is still reported, side by side
    demand = next(d for d in result.deltas if (d.element_id, d.metric) == ("api", "work_rate.demand"))
    assert (demand.baseline, demand.scenario, demand.comparable) == (D(100), D(200), True)
    assert not [d for d in result.deltas if d.element_id == "cache" and d.metric == "work_rate.capacity"]


@pytest.mark.parametrize(
    ("scenario", "fields", "field_name"),
    [
        (
            Scenario("Ghost", failures=(Failure(FailureKind.COMPONENT, "ghost"),)),
            {},
            "scenario.failures.target",
        ),
        (
            Scenario("Ghost", changes=(ConfigurationChange("ghost", "replicas", 2),)),
            {},
            "scenario.changes.element_id",
        ),
        (DOUBLE, {"entries": ("ghost",)}, "entries"),
    ],
)
def test_fixture_10_a_scenario_referencing_a_nonexistent_component_is_refused(
    scenario: Scenario, fields: dict[str, Any], field_name: str
) -> None:
    case = workload_case(scenario, **fields)
    before = to_dict(case.ir)
    with pytest.raises(InvalidSimulationRequest) as refused:
        run(case)
    assert refused.value.details["field"] == field_name
    assert refused.value.details["reason"] == "unknown_element"
    assert refused.value.details["element_id"] == "ghost"
    assert to_dict(case.ir) == before


def test_fixtures_7_and_8_compare_only_compatible_models() -> None:
    four = run(workload_case(Scenario("4", changes=(ConfigurationChange("api", "replicas", 4),)))).result
    six = run(workload_case(Scenario("6", changes=(ConfigurationChange("api", "replicas", 6),)))).result
    assert compare(four, six).comparable  # fixture 7
    [run_] = six.runs
    other = replace(six, runs=(replace(run_, model_set=ModelSet.of([("replica-throughput", 2)])),))
    incompatible = compare(four, other)  # fixture 8
    assert (incompatible.comparable, incompatible.deltas) == (False, ())
    assert compare(four, six).to_dict() == compare(four, six).to_dict()


@pytest.mark.parametrize("name", sorted(CASES))
def test_identical_inputs_give_identical_outputs(name: str) -> None:
    case = CASES[name]
    first, second = run(case), run(case)
    assert json.dumps(dict(first.overlay), sort_keys=True) == json.dumps(dict(second.overlay), sort_keys=True)
    assert first.result.to_dict() == second.result.to_dict()
    assert first.result.fingerprint == second.result.fingerprint
    for seed in (1, 2, 3):  # the order the architecture and scenario are given in does not matter
        ir, scenario = shuffled(case, seed)
        other = run(case, ir, scenario)
        assert other.result.to_dict() == first.result.to_dict()
        assert dict(other.overlay) == dict(first.overlay)


@pytest.mark.parametrize("name", sorted(CASES))
def test_outputs_are_canonically_ordered_and_exact(name: str) -> None:
    result = run(CASES[name]).result
    assert [c.node_id for c in result.components] == sorted(c.node_id for c in result.components)
    keys = [(d.analysis.value, d.element_id, d.metric) for d in result.deltas]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    for d in result.deltas:
        for value in (d.baseline, d.scenario, d.difference, d.percentage):
            assert value is None or isinstance(value, Decimal)  # exact, never a float
    assert SimulationResult.from_dict(result.to_dict()) == result  # round trip, stored as served


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_revision_is_never_modified(name: str) -> None:
    case = CASES[name]
    before, digest = json.dumps(to_dict(case.ir), sort_keys=True), content_hash(case.ir)
    output = run(case)
    assert (json.dumps(to_dict(case.ir), sort_keys=True), content_hash(case.ir)) == (before, digest)
    assert output.overlay["revision"]["content_hash"] == digest  # the overlay cites the untouched revision


def test_failures_mark_rather_than_remove() -> None:
    result = run(CASES["4-component-failure"]).result
    outcomes = {c.node_id: c for c in result.components}
    assert outcomes["db"].unavailable
    assert any(e.impact is Impact.INTERRUPTED for e in result.entries)
    assert all(r.state is not RunState.FAILED for r in result.runs)


FORBIDDEN = (
    "httpx",
    "requests",
    "socket",
    "urllib",
    "anthropic",
    "sqlalchemy",
    "persistence",
    "apps",
    "random",
)


def test_the_engine_reaches_no_network_storage_or_randomness() -> None:
    roots = [Path("engines/simulation"), Path("core/domain/simulations")]
    for path in (p for root in roots for p in root.glob("*.py")):
        tree = ast.parse(path.read_text())
        for statement in ast.walk(tree):
            names = []
            if isinstance(statement, ast.Import):
                names = [a.name for a in statement.names]
            elif isinstance(statement, ast.ImportFrom) and statement.module and statement.level == 0:
                names = [statement.module]
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN, (path, name)
