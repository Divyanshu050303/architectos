"""Comparing simulations (Milestone 12, phase 8): two scenarios side by side only per analysis that
ran on one common baseline with the same models and engine; anything else marked not comparable with
the reason; units and provenance on every difference; no causal explanation."""

import dataclasses
import json
import uuid
from decimal import Decimal
from typing import Any

from core.architecture_ir.serialization import content_hash
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.engine_results import ModelSet
from core.domain.simulations.comparison import SimulationComparison, compare
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.results import Delta, SimulationResult
from core.domain.simulations.scenarios import ConfigurationChange, Scenario
from core.domain.simulations.values import AnalysisKind
from core.domain.validation.options import RevisionInfo
from engines.simulation.context import SimulationContext
from engines.simulation.engine import analyze
from engines.simulation.registry import default_registry
from tests.unit.simulation.test_simulation_capacity import shop

A = AnalysisKind
D = Decimal
RPS = "requests/second"
WORKLOAD = WorkloadProfile("Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of(100, RPS))


def simulate(replicas: int, workload: WorkloadProfile | None = WORKLOAD) -> SimulationResult:
    ir = shop()
    scenario = Scenario(f"{replicas} replicas", changes=(ConfigurationChange("api", "replicas", replicas),))
    request = SimulationRequest(uuid.UUID(int=1), 1, scenario, analyses=(A.CAPACITY,), workload=workload)
    context = SimulationContext(ir, RevisionInfo("arch-1", 1, content_hash(ir)), request)
    return analyze(context, default_registry())


def metric(comparison: SimulationComparison, name: str, element: str = "api") -> Delta:
    [found] = [d for d in comparison.deltas if (d.element_id, d.metric) == (element, name)]
    return found


def reason(first: SimulationResult, second: SimulationResult, analysis: AnalysisKind = A.CAPACITY) -> Any:
    [found] = [a for a in compare(first, second).analyses if a.analysis is analysis]
    return found.reason


def test_fixture_7_scenarios_on_one_baseline_with_compatible_models() -> None:
    four, six = simulate(4), simulate(6)
    comparison = compare(four, six)
    [run] = [a for a in comparison.analyses if a.analysis is A.CAPACITY]
    assert (run.comparable, run.reason) == (True, None)
    assert run.baseline_fingerprint == four.runs[0].baseline_fingerprint
    capacity = metric(comparison, "work_rate.capacity")
    assert (capacity.unit, capacity.baseline, capacity.scenario, capacity.difference) == (
        RPS,
        D(240),
        D(360),
        D(120),
    )
    assert capacity.percentage == D(50)
    assert comparison.to_dict()["first"] == four.fingerprint


def test_fixture_8_incompatible_model_versions_are_not_compared() -> None:
    four, six = simulate(4), simulate(6)
    [run] = six.runs
    other_models = dataclasses.replace(run, model_set=ModelSet.of([("replica-throughput", 2)]))
    comparison = compare(four, dataclasses.replace(six, runs=(other_models,)))
    [capacity] = [a for a in comparison.analyses if a.analysis is A.CAPACITY]
    assert (capacity.comparable, capacity.reason) == (False, "different_models")
    assert comparison.deltas == ()  # never set side by side as if equivalent
    assert not comparison.comparable


def test_a_different_engine_baseline_or_missing_run_is_not_compared() -> None:
    four = simulate(4)
    engine = ModelSet.of([("simulation", 2), ("evaluator.capacity", 1)])
    assert reason(four, dataclasses.replace(simulate(6), engine_set=engine)) == "different_engine"
    heavier = WorkloadProfile("Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of(200, RPS))
    assert reason(four, simulate(6, workload=heavier)) == "different_baseline"  # another baseline
    assert reason(four, simulate(6, workload=None)) == "not_run"
    assert reason(four, four, A.COST) == "not_run"


def test_a_unit_mismatch_is_never_compared() -> None:
    four, six = simulate(4), simulate(6)
    renamed = tuple(
        dataclasses.replace(d, unit="alerts") if d.metric == "bottlenecks" else d for d in six.deltas
    )
    mismatch = metric(compare(four, dataclasses.replace(six, deltas=renamed)), "bottlenecks", "system")
    assert (mismatch.note, mismatch.comparable, mismatch.difference) == ("unit_mismatch", False, None)


def test_the_comparison_is_deterministic_and_states_no_cause() -> None:
    four, six = simulate(4), simulate(6)
    first, again = compare(four, six), compare(four, six)
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    serialized = json.dumps(first.to_dict()).lower()
    assert "because" not in serialized
    assert "cause" not in serialized
