"""Candidate impact analysis (Milestone 13, phase 6): each candidate evaluated by the engines that model
each dimension — the Simulation Engine (capacity, reliability, cost), the Security and Observability
engines on baseline and overlay — with their model versions, fingerprints and reused inputs; unknown
dimensions kept unknown, partial results kept, nothing combined or invented."""

import json
import uuid
from dataclasses import replace
from typing import Any

from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.evolution.candidates import BaselineRef, Candidate, EvidenceRef, Impact, RuleRef
from core.domain.evolution.goals import EvolutionGoal, FindingRef
from core.domain.evolution.ports import ImpactInputs
from core.domain.evolution.values import (
    CandidateCategory,
    EvidenceSource,
    EvidenceState,
    GoalType,
    ValidationState,
)
from core.domain.observability.values import Dimension as Coverage
from core.domain.simulations.scenarios import ConfigurationChange
from core.domain.validation.options import RevisionInfo
from engines.evolution.impact import Assessor, ImpactEngines
from engines.evolution.rulebook import default_registry
from engines.evolution.rules import RuleContext, generate
from engines.evolution.trigger_engine import evaluate
from engines.observability.service import DeterministicObservabilityEngine
from engines.security.service import DeterministicSecurityEngine
from engines.simulation.service import DeterministicSimulationEngine
from tests.unit.cost.test_cost_capacity import IR as PRICED_IR
from tests.unit.cost.test_cost_capacity import workload as priced_workload
from tests.unit.evolution.test_evolution_triggers import (
    ARCHITECTURE,
    BASELINE,
    IR,
    REVISION,
    capacity,
    observability,
    security,
)
from tests.unit.simulation.test_simulation_cost import PRICING, SNAPSHOT

S, G = EvidenceSource, GoalType
ENGINES = ImpactEngines(
    DeterministicSimulationEngine(), DeterministicSecurityEngine(), DeterministicObservabilityEngine()
)
WORKLOAD = WorkloadProfile(
    "Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of(200, "requests/second")
)
CAPACITY_REF = EvidenceRef(S.CAPACITY, "cap-1", EvidenceState.CURRENT, None, 1, BASELINE.content_hash, "m1")


def candidates(goal: EvolutionGoal, *analyses: Any) -> tuple[Candidate, ...]:
    evaluation = evaluate(IR, BASELINE, (goal,), {a.source: a for a in analyses})
    return generate(
        RuleContext(IR, BASELINE, {goal.key: goal}), evaluation.triggers, default_registry()
    ).candidates


def assessor(inputs: ImpactInputs | None = None, engines: ImpactEngines = ENGINES) -> Assessor:
    inputs = inputs or ImpactInputs(workload=WORKLOAD, capacity=CAPACITY_REF)
    return Assessor(IR, REVISION, ARCHITECTURE, inputs, engines, default_registry())


def by_dimension(candidate: Candidate) -> dict[EvidenceSource, Impact]:
    return {i.dimension: i for i in candidate.impacts}


def test_a_scaling_candidate_is_evaluated_by_the_simulation_engine() -> None:
    goal = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))
    [candidate] = candidates(goal, capacity())
    impacts = by_dimension(assessor().assess(candidate))
    assert set(impacts) == {S.CAPACITY, S.COST, S.RELIABILITY}  # what the rule declares, nothing else
    cap = impacts[S.CAPACITY]
    assert (cap.state, cap.source, cap.inputs) == ("completed", S.SIMULATION, (CAPACITY_REF,))
    assert cap.model_version is not None
    assert cap.baseline_fingerprint != cap.candidate_fingerprint
    utilization = next(d for d in cap.deltas if (d.element_id, d.metric) == ("api", "work_rate.utilization"))
    assert (utilization.baseline, utilization.candidate) == ("1.666666667", "0.833333333")
    assert ("api", "work_rate.capacity") in {(d.element_id, d.metric) for d in cap.deltas}
    cost = impacts[S.COST]
    assert (cost.state, cost.reason) == ("unsupported", "no_pricing")  # unknown stays unknown
    assert cost.deltas == ()
    assert cost.missing


def test_a_cost_impact_uses_the_current_pricing_snapshot() -> None:
    baseline = BaselineRef(uuid.UUID(int=3), 1, content_hash(PRICED_IR))
    revision = RevisionInfo(str(baseline.architecture_id), 1, baseline.content_hash)
    cost_ref = EvidenceRef(S.COST, "cost-1", EvidenceState.CURRENT, None, 1, baseline.content_hash, "c1")
    candidate = Candidate(
        RuleRef("scale-replicas", 1), baseline, CandidateCategory.SCALING, "Run api with 2 replicas",
        "Raise api to 2 replicas.", (ConfigurationChange("api", "replicas", 2),),
        ("increase_workload:100 requests/second",), "Stated by the capacity model.",
        (EvidenceRef(S.CAPACITY, "cap-1", EvidenceState.CURRENT, "x", 1, baseline.content_hash),),
    )  # fmt: skip
    inputs = ImpactInputs(
        workload=priced_workload(),
        pricing=PRICING,
        snapshot=SNAPSHOT,
        provider="aws",
        currency="USD",
        cost=cost_ref,
    )
    assessed = Assessor(
        PRICED_IR, revision, baseline.architecture_id, inputs, ENGINES, default_registry()
    ).assess(candidate)
    cost = by_dimension(assessed)[S.COST]
    assert cost.state == "completed"
    assert cost_ref in cost.inputs
    api = next(d for d in cost.deltas if d.element_id == "api")
    assert (api.unit, api.baseline, api.candidate, api.difference) == (
        "USD/month", "59.568", "119.136", "59.568",
    )  # fmt: skip
    assert {a.label for a in cost.assumptions} >= {"cost.estimate"}


def test_a_security_control_is_evaluated_by_the_security_engine_on_both_sides() -> None:
    analysis = security()
    transit = next(i for i in analysis.items if i.code == "unencrypted_data_in_transit")
    goal = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, transit.item))
    [candidate] = candidates(goal, analysis)
    [impact] = assessor().assess(candidate).impacts
    assert (impact.dimension, impact.source) == (S.SECURITY, S.SECURITY)
    resolved = [c for c in impact.changes if c.kind == "resolved"]
    assert ("unencrypted_data_in_transit", ("api", "api-db", "db")) in {
        (c.code, c.element_ids) for c in resolved
    }
    assert all(d.metric.startswith("findings.") and d.unit == "findings" for d in impact.deltas)
    assert impact.baseline_fingerprint != impact.candidate_fingerprint


def test_an_instrumentation_candidate_is_evaluated_by_the_observability_engine() -> None:
    goal = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.LOGGING)
    [candidate] = candidates(goal, observability())
    [impact] = assessor().assess(candidate).impacts
    assert impact.dimension is S.OBSERVABILITY
    assert "logs_absent" in {c.code for c in impact.changes if c.kind == "resolved"}


def test_without_a_workload_capacity_stays_unknown() -> None:
    goal = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))
    [candidate] = candidates(goal, capacity())
    cap = by_dimension(assessor(ImpactInputs()).assess(candidate))[S.CAPACITY]
    assert (cap.state, cap.reason, cap.deltas) == ("unsupported", "no_workload", ())
    assert "the workload of a current capacity analysis" in cap.missing


def test_an_invalid_candidate_is_not_evaluated() -> None:
    goal = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))
    [candidate] = candidates(goal, capacity())
    invalid = replace(candidate, validation=ValidationState.INVALID)
    impacts = assessor().assess(invalid).impacts
    assert {(i.state, i.reason) for i in impacts} == {("not_evaluated", "candidate_invalid")}


def test_impacts_are_deterministic_the_baseline_unchanged_and_computed_once() -> None:
    analysis = security()
    items = [
        i for i in analysis.items if i.code in ("unencrypted_data_in_transit", "unencrypted_data_at_rest")
    ]
    goals = tuple(EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, i.item)) for i in items)
    evaluation = evaluate(IR, BASELINE, goals, {S.SECURITY: analysis})
    found = generate(
        RuleContext(IR, BASELINE, {g.key: g for g in goals}), evaluation.triggers, default_registry()
    )
    assert len(found.candidates) == 2

    calls: list[str] = []

    class Counting(DeterministicSecurityEngine):
        def analyze(self, ir: Any, revision: RevisionInfo, *args: Any) -> Any:
            calls.append(revision.content_hash)
            return super().analyze(ir, revision, *args)

    before = json.dumps(to_dict(IR), sort_keys=True)
    counting = assessor(engines=replace(ENGINES, security=Counting()))
    first = [counting.assess(c) for c in found.candidates]
    assert calls.count(BASELINE.content_hash) == 1  # the baseline once, each candidate once
    assert len(calls) == 3
    again = [assessor().assess(c) for c in found.candidates]
    assert [c.to_dict() for c in first] == [c.to_dict() for c in again]
    assert json.dumps(to_dict(IR), sort_keys=True) == before
    restored = Candidate.from_dict(json.loads(json.dumps(first[0].to_dict())))
    assert restored == first[0]
