"""The twelve evolution fixtures end to end through the engine port (Milestone 13, phase 10), with
evidence from the real engines; determinism of candidate ids and order, overlays, diffs, triggers and
impact references (also with goals and evidence given in another order); the baseline never
modified; no network, storage or randomness reached; bounded work on a large architecture."""

import ast
import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence
from core.domain.evolution.candidates import BaselineRef, Candidate, EvidenceRef, RuleRef
from core.domain.evolution.entities import EvolutionRequest
from core.domain.evolution.evidence import StoredAnalysis, from_reliability
from core.domain.evolution.goals import EvolutionGoal, FindingRef
from core.domain.evolution.overlays import apply_candidate
from core.domain.evolution.ports import ImpactInputs
from core.domain.evolution.results import EvolutionResult, FindingType
from core.domain.evolution.tradeoffs import alternatives
from core.domain.evolution.validation import validate_candidate
from core.domain.evolution.values import (
    CandidateCategory,
    Direction,
    EvidenceSource,
    EvidenceState,
    EvolutionStatus,
    GoalType,
    ValidationState,
)
from core.domain.observability.values import Dimension as Coverage
from core.domain.reliability.analyses import ReliabilityAnalysis, ReliabilityAnalysisRequest
from core.domain.reliability.reports import ReliabilityReport
from core.domain.simulations.scenarios import ConfigurationChange
from core.domain.validation.options import RevisionInfo
from engines.evolution.impact import Assessor
from engines.evolution.rulebook import default_registry
from engines.evolution.service import MAX_EVALUATED, DeterministicEvolutionEngine
from engines.evolution.trigger_engine import evaluate
from engines.reliability.service import DeterministicReliabilityEngine
from engines.validation.service import DeterministicValidationEngine
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.evolution.test_evolution_impact import ENGINES, WORKLOAD
from tests.unit.evolution.test_evolution_triggers import (
    ARCHITECTURE,
    BASELINE,
    IR,
    REVISION,
    capacity,
    observability,
    reliability,
    security,
    shop,
    stored,
)

S, G, F, V = EvidenceSource, GoalType, FindingType, ValidationState
ENGINE = DeterministicEvolutionEngine()
SCALE = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))
AVAILABLE = EvolutionGoal(G.AVAILABILITY_OBJECTIVE, target=Quantity.of("0.999", "ratio"))
LOGGING = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.LOGGING)
FORBIDDEN_KEYS = {"score", "rank", "ranking", "weight", "best", "winner", "optimal", "recommended"}


def analyze(
    goals: tuple[EvolutionGoal, ...],
    *analyses: StoredAnalysis,
    ir: ArchitectureIR = IR,
    inputs: ImpactInputs | None = None,
    **request: Any,
) -> EvolutionResult:
    revision = RevisionInfo(str(ARCHITECTURE), 1, content_hash(ir))
    return ENGINE.analyze(
        ir,
        revision,
        EvolutionRequest(ARCHITECTURE, 1, goals, **request),
        {a.source: a for a in analyses},
        inputs or ImpactInputs(workload=WORKLOAD),
    )


def by_rule(result: EvolutionResult) -> dict[str, Candidate]:
    return {c.rule.id: c for c in result.candidates}


def types(result: EvolutionResult) -> set[FindingType]:
    return {f.type for f in result.findings}


def test_fixture_1_a_capacity_bottleneck_with_sufficient_evidence() -> None:
    result = analyze((SCALE,), capacity())
    candidate = by_rule(result)["scale-replicas"]
    assert candidate.changes == (ConfigurationChange("api", "replicas", 4),)
    assert candidate.validation is V.VALID
    impact = {i.dimension: i for i in candidate.impacts}[S.CAPACITY]
    assert (impact.state, impact.source) == ("completed", S.SIMULATION)
    assert {r.dimension: r.direction for r in candidate.consequences}["capacity"] is Direction.IMPROVES


def test_fixture_2_a_capacity_finding_with_missing_inputs_proposes_nothing() -> None:
    bare = ArchitectureIR(
        "Bare",
        nodes=(node("web", NodeKind.CLIENT), node("api", configuration=Configuration({"replicas": 2}))),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https",
                       configuration=Configuration({"traffic_ratio": Decimal(1)})),
        ),
    )  # fmt: skip
    result = analyze((SCALE,), capacity(ir=bare), ir=bare)
    assert result.candidates == ()  # api's capacity is not declared: nothing is fabricated
    assert types(result) & {F.GOAL_NOT_EVALUABLE, F.MISSING_EVIDENCE}
    assert result.missing


def test_fixture_3_a_cost_constraint_with_valid_pricing_evidence() -> None:
    facts = (Evidence("currency", "USD"), Evidence("monthly", "640"), Evidence("complete", "true"),
             Evidence("largest_component", "db"))  # fmt: skip
    cost = stored(S.COST, facts=facts)
    over = analyze((EvolutionGoal(G.COST_CEILING, amount=Decimal(500), currency="USD"),), cost)
    [finding] = over.findings
    assert (finding.type, finding.element_ids) == (F.NO_APPLICABLE_RULE, ("db",))
    assert over.candidates == ()  # no rule reduces cost: nothing is invented
    within = analyze((EvolutionGoal(G.COST_CEILING, amount=Decimal(700), currency="USD"),), cost)
    assert (types(within), within.status) == ({F.GOAL_ALREADY_MET}, EvolutionStatus.COMPLETED)


def test_fixture_4_a_reliability_finding_with_explicit_redundancy_semantics() -> None:
    result = analyze((AVAILABLE,), reliability())
    candidate = by_rule(result)["add-replica"]
    assert (candidate.category, candidate.changes) == (
        CandidateCategory.REDUNDANCY,
        (ConfigurationChange("db", "replicas", 2),),
    )
    assert "db.configuration.failover_mode" in candidate.missing
    assert {i.dimension for i in candidate.impacts} == {S.CAPACITY, S.COST, S.RELIABILITY}


def test_fixture_5_a_security_finding_linked_to_a_supported_control() -> None:
    analysis = security()
    transit = next(i for i in analysis.items if i.code == "unencrypted_data_in_transit")
    goal = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, transit.item))
    result = analyze((goal,), analysis)
    candidate = by_rule(result)["require-tls"]
    assert candidate.validation is V.VALID
    assert {r.dimension: r.direction for r in candidate.consequences}["security"] is Direction.IMPROVES


def test_fixture_6_an_observability_requirement_with_a_modeled_coverage_gap() -> None:
    result = analyze((LOGGING,), observability())
    candidate = by_rule(result)["enable-signal"]
    assert candidate.changes == (ConfigurationChange("api", "logs", True),)
    row = {r.dimension: r for r in candidate.consequences}["observability"]
    assert row.direction is Direction.MIXED  # logs emitted, but not collected: shown, not hidden


def test_fixture_7_a_new_requirement_the_architecture_cannot_evaluate() -> None:
    goal = EvolutionGoal(G.SATISFY_REQUIREMENT, requirement_id=uuid.UUID(int=77))
    result = analyze((goal,), reliability(), security(), observability(), stored(S.VALIDATION))
    assert types(result) == {F.GOAL_UNSUPPORTED}
    assert result.status is EvolutionStatus.INSUFFICIENT_EVIDENCE
    assert result.unsupported_goals == (goal.key,)
    assert result.candidates == ()


def _handmade(*changes: ConfigurationChange) -> Candidate:
    return Candidate(
        RuleRef("scale-replicas", 1), BASELINE, CandidateCategory.SCALING, "A proposal", "Changes.", changes,
        (SCALE.key,), "Stated.",
        (EvidenceRef(S.CAPACITY, "a1", EvidenceState.CURRENT, "x", 1, BASELINE.content_hash),),
    )  # fmt: skip


@pytest.mark.parametrize(
    ("change", "state"),
    [
        (ConfigurationChange("cache", "replicas", 3), V.INVALID),  # fixture 8: no such component
        (ConfigurationChange("api", "tls", True), V.UNSUPPORTED),  # fixture 9: a connection's property
    ],
)
def test_fixtures_8_and_9_invalid_candidates_are_never_presented_as_valid(
    change: ConfigurationChange, state: ValidationState
) -> None:
    validated = validate_candidate(IR, REVISION, _handmade(change), DeterministicValidationEngine())
    assert validated.validation is state
    [note] = validated.validation_notes
    assert note.element_ids == (change.element_id,)
    assessor = Assessor(IR, REVISION, ARCHITECTURE, ImpactInputs(), ENGINES, default_registry())
    assessed = assessor.assess(validated)
    assert {(i.state, i.reason) for i in assessed.impacts} == {("not_evaluated", f"candidate_{state.value}")}


def test_fixture_10_alternatives_with_distinct_tradeoffs_and_no_winner() -> None:
    cpu = ArchitectureIR(
        "Cpu",
        nodes=(node("web", NodeKind.CLIENT), node("api", configuration=Configuration(
            {"replicas": 2, "cpu_limit_cores": Decimal(1),
             "cpu_core_seconds_per_request": Decimal("0.02")}))),
        connections=(connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https",
                                configuration=Configuration({"traffic_ratio": Decimal(1)})),),
    )  # fmt: skip
    result = analyze((SCALE,), capacity(ir=cpu), ir=cpu)
    rules = by_rule(result)
    assert set(rules) == {"scale-cpu", "scale-replicas"}
    assert rules["scale-cpu"].changes == (ConfigurationChange("api", "cpu_limit_cores", Decimal(2)),)
    assert rules["scale-replicas"].changes == (ConfigurationChange("api", "replicas", 4),)
    [compared] = alternatives(result.candidates, result.goals)
    assert set(compared.candidates) == {c.id for c in result.candidates}
    assert list(compared.candidates) == sorted(compared.candidates)  # canonical, not a ranking
    assert result.summary()["candidates_by_goal"] == {SCALE.key: 2}
    text = json.dumps([result.to_dict(), compared.to_dict()])
    assert not any(f'"{word}"' in text for word in FORBIDDEN_KEYS)


def test_fixture_11_stale_evidence_against_a_newer_revision() -> None:
    old = capacity(ir=shop("Shop v1"))
    result = analyze((SCALE,), old)
    assert result.candidates == ()
    assert {F.STALE_EVIDENCE, F.GOAL_NOT_EVALUABLE} <= types(result)
    assert [e.state for e in result.evidence] == [EvidenceState.STALE]


def test_fixture_12_a_candidate_proposal_leaves_the_baseline_unchanged() -> None:
    before, digest = json.dumps(to_dict(IR), sort_keys=True), content_hash(IR)
    result = analyze((SCALE, AVAILABLE, LOGGING), capacity(), reliability(), observability())
    assert result.candidates
    for candidate in result.candidates:
        overlay = apply_candidate(IR, candidate)
        assert overlay.content_hash != digest
        assert candidate.baseline == BASELINE
    assert (json.dumps(to_dict(IR), sort_keys=True), content_hash(IR)) == (before, digest)


# --- determinism ----------------------------------------------------------------------------------


def test_identical_inputs_give_identical_results_whatever_the_order() -> None:
    analyses = (capacity(), reliability(), security(), observability())
    goals = (SCALE, AVAILABLE, LOGGING)
    first = analyze(goals, *analyses)
    again = analyze(goals[::-1], *analyses[::-1])
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    assert [c.id for c in first.candidates] == [c.id for c in again.candidates]
    for one, two in zip(first.candidates, again.candidates, strict=True):
        assert apply_candidate(IR, one).to_dict() == apply_candidate(IR, two).to_dict()  # overlays and diffs
        assert [i.to_dict() for i in one.impacts] == [i.to_dict() for i in two.impacts]  # impact references
    by_source = {a.source: a for a in analyses}
    triggers = evaluate(IR, BASELINE, goals, by_source).triggers
    assert triggers == evaluate(IR, BASELINE, goals[::-1], dict(reversed(by_source.items()))).triggers
    assert [t.key for t in triggers] == sorted(t.key for t in triggers)


# --- regression and bounds ------------------------------------------------------------------------

FORBIDDEN_IMPORTS = ("httpx", "requests", "socket", "urllib", "anthropic", "sqlalchemy", "persistence",
                     "apps", "random", "importlib")  # fmt: skip


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for statement in ast.walk(ast.parse(path.read_text() or "")):
        if isinstance(statement, ast.Import):
            names |= {a.name for a in statement.names}
        elif isinstance(statement, ast.ImportFrom) and statement.module and statement.level == 0:
            names.add(statement.module)
    return names


def test_the_evolution_engine_reaches_no_network_storage_or_randomness() -> None:
    for root in ("engines/evolution", "core/domain/evolution", "core/domain/decisions"):
        for path in sorted(Path(root).glob("*.py")):
            if path.name.endswith("_service.py"):
                continue  # the use cases read repositories through the unit-of-work port
            for name in _imports(path):
                assert name.split(".")[0] not in FORBIDDEN_IMPORTS, (path, name)


def test_no_other_engine_depends_on_evolution() -> None:
    for path in sorted(Path("engines").rglob("*.py")):
        if path.parts[1] != "evolution":
            assert not any(n.startswith("engines.evolution") for n in _imports(path)), path


def test_a_large_architecture_is_analyzed_with_bounded_work() -> None:
    size = 300
    ir = ArchitectureIR(
        "Large",
        nodes=(node("web", NodeKind.CLIENT), *(
            node(f"s{i:03d}", configuration=Configuration(
                {"replicas": 1, "availability": Decimal("0.999"), "failure_independence": "independent"}))
            for i in range(size)
        )),
        connections=tuple(
            connection(f"web-s{i:03d}", "web", f"s{i:03d}", kind=ConnectionKind.REQUEST, protocol="https")
            for i in range(size)
        ),
    )  # fmt: skip
    revision = RevisionInfo(str(ARCHITECTURE), 1, content_hash(ir))
    at = datetime(2026, 9, 27, tzinfo=UTC)
    request = ReliabilityAnalysisRequest(ARCHITECTURE, 1)
    reliable = DeterministicReliabilityEngine().analyze(ir, revision, request, ())
    pending = ReliabilityAnalysis(
        uuid.uuid4(), uuid.uuid4(), ARCHITECTURE, 1, revision.content_hash, "pending", None, at
    )
    report = ReliabilityReport.of(pending.start(at).finish(reliable, at), {})
    started = time.perf_counter()
    result = analyze((AVAILABLE,), from_reliability(report, reliable.findings), ir=ir, inputs=ImpactInputs())
    elapsed = time.perf_counter() - started
    assert len(result.candidates) == MAX_EVALUATED  # cut in canonical order, and the cut is stated
    assert "candidates_truncated" in {x.code for x in result.limitations}
    assert elapsed < 30  # measured ~2 s here; the bound guards against unbounded growth
    assert result.baseline == BaselineRef(ARCHITECTURE, 1, content_hash(ir))
