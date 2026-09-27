"""The simulation orchestrator (Milestone 12, phase 4): explicit steps, analyses selected and gated by
their inputs, failures isolated and reported, outcomes collected, deterministic, and the source
architecture never modified. Evaluators here are stand-ins; the real ones wrap the engines (phases
5 to 7)."""

import json
import logging
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.capacity.errors import InvalidScenario
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.engine_results import Evidence, ModelSet
from core.domain.simulations.entities import SimulationAssumption, SimulationRequest
from core.domain.simulations.errors import InvalidSimulationRequest
from core.domain.simulations.overlay import Overlay
from core.domain.simulations.results import AnalysisRun, Delta, EntryImpact, SimulationResult
from core.domain.simulations.scenarios import ConfigurationChange, Failure, Scenario, WorkloadChange
from core.domain.simulations.values import AnalysisKind, FailureKind, Impact, RunState, SimulationStatus
from core.domain.validation.options import RevisionInfo
from engines.simulation.context import SimulationContext
from engines.simulation.engine import DuplicateEvaluator, Evaluation, EvaluatorMeta, Registry, analyze
from engines.simulation.validation import ScenarioPlan
from tests.unit.architecture_ir.builders import connection, node

A = AnalysisKind
MODELS = ModelSet.of([("stand-in", 1)])


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    config: dict[str, ConfigValue] = values
    return node(node_id, kind, configuration=Configuration(config))


def shop() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            component("api", replicas=2, availability_zones=("eu-west-1a", "eu-west-1b")),
            component("db", NodeKind.DATABASE, availability_zones=("eu-west-1a",)),
            component("cache", NodeKind.CACHE),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql"),
        ),
    )


def workload() -> WorkloadProfile:
    rate = Quantity.of(100, "requests/second")
    return WorkloadProfile("Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=rate)


class StandIn:
    """Records its calls and returns a fixed evaluation."""

    def __init__(self, analysis: AnalysisKind, requires: tuple[str, ...] = (), **evaluation: Any) -> None:
        self.meta = EvaluatorMeta(analysis, 1, analysis.value, "A stand-in.", requires)
        self.calls: list[Mapping[AnalysisKind, Evaluation]] = []
        self.evaluation = evaluation

    def evaluate(
        self,
        context: SimulationContext,
        overlay: Overlay,
        plan: ScenarioPlan,
        earlier: Mapping[AnalysisKind, Evaluation],
    ) -> Evaluation:
        self.calls.append(earlier)
        run = AnalysisRun(self.meta.analysis, RunState.COMPLETED, MODELS, "a" * 64, "b" * 64)
        return Evaluation(run, **self.evaluation)


class Failing(StandIn):
    def __init__(self, analysis: AnalysisKind, error: Exception) -> None:
        super().__init__(analysis)
        self.error = error

    def evaluate(self, *args: Any) -> Evaluation:
        raise self.error


def capacity(**evaluation: Any) -> StandIn:
    return StandIn(A.CAPACITY, ("workload",), **evaluation)


def run(scenario: Scenario, registry: Registry, **fields: Any) -> SimulationResult:
    ir = fields.pop("ir", shop())
    request = SimulationRequest(uuid.UUID(int=1), 1, scenario, **fields)
    return analyze(SimulationContext(ir, RevisionInfo("arch-1", 1, content_hash(ir)), request), registry)


GROWTH = Scenario("Double", workload=WorkloadChange(growth=Decimal(2)))
REPLICAS = Scenario("Scale", changes=(ConfigurationChange("api", "replicas", 4),))
OUTAGE = Scenario("Zone", failures=(Failure(FailureKind.ZONE, "eu-west-1a"),))


def test_by_default_every_concerned_analysis_runs() -> None:
    evaluators = [capacity(), StandIn(A.RELIABILITY), StandIn(A.COST, ("pricing",))]
    result = run(REPLICAS, Registry(evaluators), workload=workload())
    runs = {r.analysis: (r.state, r.reason) for r in result.runs}
    assert runs == {
        A.CAPACITY: (RunState.COMPLETED, None),
        A.RELIABILITY: (RunState.COMPLETED, None),
        A.COST: (RunState.UNSUPPORTED, "no_pricing"),  # never priced on invented prices
    }
    assert result.status is SimulationStatus.PARTIAL
    assert [c.node_id for c in result.components] == ["api"]
    assert result.components[0].changes == (Evidence("api.replicas", "2 -> 4"),)


def test_missing_inputs_leave_the_analysis_unsupported_not_zero() -> None:
    stand_in = capacity()
    result = run(GROWTH, Registry([stand_in]))
    runs = {r.analysis: (r.state, r.reason) for r in result.runs}
    assert runs == {
        A.CAPACITY: (RunState.UNSUPPORTED, "no_workload"),
        A.COST: (RunState.UNSUPPORTED, "no_evaluator"),  # a workload change concerns cost too
    }
    assert stand_in.calls == []  # not calculated
    assert result.status is SimulationStatus.UNSUPPORTED
    assert result.deltas == ()


def test_a_requested_analysis_the_scenario_does_not_concern() -> None:
    reliability = StandIn(A.RELIABILITY)
    registry = Registry([capacity(), reliability])
    result = run(GROWTH, registry, analyses=(A.RELIABILITY,), workload=workload())
    [only] = result.runs
    assert (only.analysis, only.reason) == (A.RELIABILITY, "not_concerned")
    assert reliability.calls == []
    no_evaluator = run(OUTAGE, Registry([]))
    assert [(r.analysis, r.reason) for r in no_evaluator.runs] == [(A.RELIABILITY, "no_evaluator")]


def test_failures_are_isolated_and_reported(caplog: pytest.LogCaptureFixture) -> None:
    broken = Failing(A.CAPACITY, RuntimeError("password=hunter2"))
    refusing = Failing(A.COST, InvalidScenario(details={"field": "changes", "reason": "not_changeable"}))
    reliability = StandIn(A.RELIABILITY, entries=(EntryImpact("web", Impact.TOLERATED, "Covered."),))
    with caplog.at_level(logging.ERROR, logger="architectos.simulation"):
        result = run(REPLICAS, Registry([broken, reliability, refusing]), workload=workload())
    runs = {r.analysis: (r.state, r.reason) for r in result.runs}
    assert runs[A.CAPACITY] == (RunState.FAILED, "engine_error")
    assert runs[A.RELIABILITY] == (RunState.COMPLETED, None)  # still counts
    assert runs[A.COST][0] is RunState.UNSUPPORTED  # the engine refused what the scenario asks
    assert result.status is SimulationStatus.PARTIAL  # never completed when a run failed
    assert "hunter2" not in caplog.text
    assert [r.error_type for r in caplog.records] == ["RuntimeError"]  # type: ignore[attr-defined]


def test_an_invalid_scenario_is_refused_before_any_evaluator_runs() -> None:
    stand_in = capacity()
    with pytest.raises(InvalidSimulationRequest):
        run(Scenario("S", changes=(ConfigurationChange("ghost", "replicas", 2),)), Registry([stand_in]))
    assert stand_in.calls == []


def test_evaluators_see_the_earlier_evaluations_in_order() -> None:
    first, second = capacity(), StandIn(A.COST)
    run(REPLICAS, Registry([first, second]), workload=workload(), analyses=(A.CAPACITY, A.COST))
    assert first.calls == [{}]
    assert list(second.calls[0]) == [A.CAPACITY]


def test_outcomes_are_collected_with_their_trace_and_versions() -> None:
    delta = Delta(A.CAPACITY, "api", "work_rate.demand", "requests/second", Decimal(100), Decimal(200))
    reliability = StandIn(
        A.RELIABILITY,
        entries=(EntryImpact("web", Impact.INTERRUPTED, "web requires db.", ("db",)),),
        impacts={"api": Impact.INTERRUPTED},
        trace=(Evidence("reliability.entries", "web"),),
    )
    scenario = Scenario(
        "All",
        workload=WorkloadChange(growth=Decimal(2)),
        failures=(Failure(FailureKind.ZONE, "eu-west-1a"),),
    )
    result = run(
        scenario,
        Registry([capacity(deltas=(delta,)), reliability]),
        workload=workload(),
        assumptions=(SimulationAssumption("peak", "Peak is Friday evening."),),
    )
    outcomes = {c.node_id: (c.unavailable, c.impact) for c in result.components}
    assert outcomes == {
        "api": (False, Impact.INTERRUPTED),  # loses a zone, and is interrupted through db
        "cache": (False, None),  # declares no zone: undetermined
        "db": (True, None),
    }
    assert [(u.element_id, u.code) for u in result.unsupported] == [("cache", "failure_undetermined")]
    assert result.deltas == (delta,)
    labels = [e.label for e in result.trace]
    assert labels[0] == "overlay.revision"
    assert "plan.workload" in labels
    assert labels[-1] == "reliability.entries"
    assert Evidence("assumption.peak", "Peak is Friday evening.") in result.assumptions
    expected = {("simulation", 1), ("workload", 1), ("zone_failure", 1), ("evaluator.capacity", 1)}
    assert expected <= set(result.engine_set.models)
    assert {x.code for x in result.limitations} == {"model_based", "no_defaults"}


def test_the_same_inputs_give_the_same_result_and_the_revision_is_untouched() -> None:
    ir = shop()
    before = json.dumps(to_dict(ir), sort_keys=True)

    def registry() -> Registry:
        return Registry([capacity(), StandIn(A.RELIABILITY, impacts={"db": Impact.INTERRUPTED})])

    scenario = Scenario("S", changes=REPLICAS.changes, failures=OUTAGE.failures)
    first = run(scenario, registry(), ir=ir, workload=workload())
    again = run(scenario, registry(), ir=ir, workload=workload())
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    assert json.dumps(to_dict(ir), sort_keys=True) == before


def test_a_registry_has_one_evaluator_per_analysis() -> None:
    with pytest.raises(DuplicateEvaluator):
        Registry([capacity(), capacity()])
    with pytest.raises(DuplicateEvaluator):
        EvaluatorMeta(A.COST, 1, "cost", "Cost.", ("telemetry",))  # an input evaluators cannot declare
