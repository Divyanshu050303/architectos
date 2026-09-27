"""Trade-off and complexity representation (Milestone 13, phase 7): every claimed benefit or drawback
backed by an engine's evidence, stated by a documented rule, or labelled a consideration for human
review; alternatives side by side; no score, weight, ranking or winner."""

import json
import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from core.architecture_ir.serialization import content_hash
from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence
from core.domain.evolution.candidates import BaselineRef, Candidate, Consequence, EvidenceRef, RuleRef
from core.domain.evolution.errors import InvalidEvolutionResult
from core.domain.evolution.goals import EvolutionGoal, FindingRef
from core.domain.evolution.tradeoffs import alternatives, consequences, with_tradeoffs
from core.domain.evolution.values import (
    Basis,
    CandidateCategory,
    Direction,
    EvidenceSource,
    EvidenceState,
    GoalType,
    ValidationState,
)
from core.domain.observability.values import Dimension as Coverage
from core.domain.simulations.scenarios import ConfigurationChange
from core.domain.validation.options import RevisionInfo
from engines.evolution.impact import Assessor, ImpactInputs
from engines.evolution.rulebook import default_registry
from tests.unit.cost.test_cost_capacity import IR as PRICED_IR
from tests.unit.cost.test_cost_capacity import workload as priced_workload
from tests.unit.evolution.test_evolution_impact import ENGINES, assessor, candidates
from tests.unit.evolution.test_evolution_triggers import capacity, observability, security
from tests.unit.simulation.test_simulation_cost import PRICING, SNAPSHOT

S, G, D, B = EvidenceSource, GoalType, Direction, Basis
WORKLOAD_GOAL = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))
FORBIDDEN = {"score", "rank", "ranking", "weight", "best", "winner", "optimal", "recommended"}


def rows(candidate: Candidate) -> dict[str, Consequence]:
    return {c.dimension: c for c in candidate.consequences}


def scaled() -> Candidate:
    [candidate] = candidates(WORKLOAD_GOAL, capacity())
    return with_tradeoffs(assessor().assess(candidate), (WORKLOAD_GOAL,))


def test_modeled_rows_come_from_the_engines_evidence() -> None:
    table = rows(scaled())
    assert table["capacity"].direction is D.IMPROVES
    assert table["capacity"].basis is B.MODELED
    assert "bottlenecks 1 -> 0" in table["capacity"].statement
    assert Evidence("engine", "simulation") in table["capacity"].evidence
    cost = table["cost"]
    assert (cost.direction, cost.basis) == (D.UNKNOWN, B.MODELED)  # no pricing: unknown, not free
    assert "no_pricing" in cost.statement
    assert "The rule states: 2 more instances run and are billed." in cost.statement


def test_rule_rows_and_considerations_are_labelled() -> None:
    table = rows(scaled())
    assert (table["dependencies"].direction, table["dependencies"].basis) == (D.UNCHANGED, B.RULE)
    assert table["reversibility"].basis is B.RULE
    assert "api.replicas" in table["reversibility"].statement
    for dimension in ("operations", "migration", "failure_modes", "expertise"):
        assert (table[dimension].direction, table[dimension].basis) == (D.CONSIDERATION, B.CONSIDERATION)
    for row in table.values():  # every claim is backed or labelled
        assert row.basis is not B.MODELED or row.evidence


def test_a_cost_increase_is_a_drawback_checked_against_the_ceiling() -> None:
    baseline = BaselineRef(uuid.UUID(int=3), 1, content_hash(PRICED_IR))
    revision = RevisionInfo(str(baseline.architecture_id), 1, baseline.content_hash)
    goal = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(100, "requests/second"))
    ceiling = EvolutionGoal(G.COST_CEILING, amount=Decimal(200), currency="USD")
    candidate = Candidate(
        RuleRef("scale-replicas", 1), baseline, CandidateCategory.SCALING, "Run api with 2 replicas",
        "Raise api to 2 replicas.", (ConfigurationChange("api", "replicas", 2),), (goal.key,),
        "Stated by the capacity model.",
        (EvidenceRef(S.CAPACITY, "cap-1", EvidenceState.CURRENT, "x", 1, baseline.content_hash),),
    )  # fmt: skip
    inputs = ImpactInputs(
        workload=priced_workload(), pricing=PRICING, snapshot=SNAPSHOT, provider="aws", currency="USD"
    )
    assessed = Assessor(
        PRICED_IR, revision, baseline.architecture_id, inputs, ENGINES, default_registry()
    ).assess(candidate)
    cost = rows(with_tradeoffs(assessed, (goal, ceiling)))["cost"]
    assert cost.direction is D.WORSENS
    assert "(59.568)" in cost.statement
    assert "above the ceiling of 200 USD/month" in cost.statement


def test_security_and_observability_rows_follow_the_findings() -> None:
    analysis = security()
    transit = next(i for i in analysis.items if i.code == "unencrypted_data_in_transit")
    goal = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, transit.item))
    [tls] = candidates(goal, analysis)
    table = rows(with_tradeoffs(assessor().assess(tls)))
    assert table["security"].direction is D.IMPROVES
    assert "resolves" in table["security"].statement
    logging = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.LOGGING)
    [logs] = candidates(logging, observability())
    observed = rows(with_tradeoffs(assessor().assess(logs)))["observability"]
    # Emitting logs resolves logs_absent, but nothing collects them yet: a trade-off, shown as such.
    assert observed.direction is D.MIXED
    assert "resolves logs_absent" in observed.statement
    assert "introduces telemetry_not_collected" in observed.statement


def test_an_invalid_candidate_has_no_modeled_consequence() -> None:
    invalid = replace(scaled(), validation=ValidationState.INVALID, consequences=())
    table = rows(with_tradeoffs(assessor().assess(invalid)))
    assert {table[d].direction for d in ("capacity", "cost", "reliability")} == {D.NOT_EVALUATED}


def test_fixture_10_alternatives_are_side_by_side_with_no_winner() -> None:
    horizontal = scaled()
    vertical = with_tradeoffs(
        replace(
            horizontal,
            rule=RuleRef("scale-cpu", 1),
            changes=(ConfigurationChange("api", "cpu_limit_cores", Decimal(2)),),
            impacts=(),
        ),
        (WORKLOAD_GOAL,),
    )
    [compared] = alternatives((vertical, horizontal), (WORKLOAD_GOAL,))
    assert compared.goal == WORKLOAD_GOAL.key
    assert set(compared.candidates) == {horizontal.id, vertical.id}
    assert list(compared.candidates) == sorted(compared.candidates)  # canonical, not ranked
    directions = {(c, d): v for c, d, v in compared.table}
    assert directions[(horizontal.id, "capacity")] is D.IMPROVES
    assert (vertical.id, "capacity") not in directions  # not evaluated: no row is invented
    text = json.dumps([compared.to_dict(), horizontal.to_dict(), vertical.to_dict()])
    for word in FORBIDDEN:
        assert f'"{word}"' not in text, word


def test_consequences_keep_their_basis_honest() -> None:
    with pytest.raises(InvalidEvolutionResult):
        Consequence("capacity", D.CONSIDERATION, B.MODELED, "A model never gives a mere consideration.")
    with pytest.raises(InvalidEvolutionResult):
        Consequence("operations", D.IMPROVES, B.CONSIDERATION, "A consideration never decides a direction.")
    with pytest.raises(InvalidEvolutionResult):
        replace(scaled(), consequences=(rows(scaled())["capacity"],) * 2)  # one row per dimension


def test_the_table_is_deterministic_and_round_trips() -> None:
    first, second = scaled(), scaled()
    assert first.consequences == second.consequences
    assert consequences(first, (WORKLOAD_GOAL,)) == first.consequences
    assert Candidate.from_dict(json.loads(json.dumps(first.to_dict()))) == first
    assert first.id == replace(first, consequences=()).id  # trade-offs never change the proposal
