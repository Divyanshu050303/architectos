"""The evolution domain contract (Milestone 13, phase 1): typed, validated goals, candidates and
results; stable ids and deterministic order; proposals never applied; unknown and unsupported kept;
no score, rank or winner."""

import dataclasses
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.domain.capacity.units import Quantity
from core.domain.engine_results import Evidence, Limitation, ModelSet
from core.domain.evolution.candidates import (
    BaselineRef,
    Candidate,
    Effect,
    EvidenceRef,
    Impact,
    RuleRef,
)
from core.domain.evolution.entities import (
    PENDING,
    EvidenceCitation,
    EvolutionAnalysis,
    EvolutionConstraints,
    EvolutionError,
    EvolutionRequest,
)
from core.domain.evolution.errors import (
    InvalidEvolutionRequest,
    InvalidEvolutionResult,
    InvalidEvolutionTransition,
)
from core.domain.evolution.goals import EvolutionGoal, FindingRef
from core.domain.evolution.results import EvolutionFinding, EvolutionResult, FindingType
from core.domain.evolution.values import (
    Basis,
    CandidateCategory,
    EvidenceSource,
    EvidenceState,
    EvolutionStatus,
    GoalSource,
    GoalType,
    ProposalStatus,
    ValidationState,
)
from core.domain.observability.values import Dimension as Coverage
from core.domain.requirements.enums import RequirementPriority
from core.domain.simulations.scenarios import ConfigurationChange

G, S, D = GoalType, EvidenceSource, Decimal
ARCHITECTURE = uuid.UUID(int=7)
ANALYSIS = uuid.UUID(int=8)
HASH = "a" * 64
OTHER_HASH = "b" * 64
BASELINE = BaselineRef(ARCHITECTURE, 3, HASH)
WORKLOAD = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, "requests/second"))
MODELS = ModelSet.of([("evolution", 1), ("scale-replicas", 1)])


def current(item: str = "scaling:api:work_rate") -> EvidenceRef:
    return EvidenceRef(S.CAPACITY, str(ANALYSIS), EvidenceState.CURRENT, item, 3, HASH, "abc123")


def candidate(**fields: Any) -> Candidate:
    values: dict[str, Any] = {
        "rule": RuleRef("scale-replicas", 1),
        "baseline": BASELINE,
        "category": CandidateCategory.SCALING,
        "title": "Run api with 4 replicas",
        "description": "Raise api's replicas from 2 to 4, as the capacity model requires for 200 rps.",
        "changes": (ConfigurationChange("api", "replicas", 4),),
        "goals": (WORKLOAD.key,),
        "rationale": "The replica-throughput model needs 4 replicas at the target utilization.",
        "evidence": (current(),),
    }
    return Candidate(**(values | fields))


# --- goals ----------------------------------------------------------------------------------------


def test_goals_are_typed_with_explicit_targets_and_units() -> None:
    assert WORKLOAD.method is S.CAPACITY
    assert WORKLOAD.key == "increase_workload:200 requests/second"
    per_minute = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(12000, "requests/minute"))
    assert per_minute.key == WORKLOAD.key  # the same rate in another unit is the same goal
    ceiling = EvolutionGoal(G.COST_CEILING, amount=D("500.00"), currency="EUR")
    assert (ceiling.key, ceiling.method) == ("cost_ceiling:500 EUR/month", S.COST)
    availability = EvolutionGoal(G.AVAILABILITY_OBJECTIVE, target=Quantity.of("99.9", "%"))
    assert availability.key == "availability_objective:0.999 ratio"
    recovery = EvolutionGoal(G.RECOVERY_OBJECTIVE, target=Quantity.of(5, "min"))
    assert recovery.method is S.RELIABILITY
    finding = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, "sec_0123"))
    assert (finding.key, finding.method) == ("address_finding:security:sec_0123", S.SECURITY)
    coverage = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.TRACING)
    assert coverage.method is S.OBSERVABILITY
    requirement = EvolutionGoal(G.SATISFY_REQUIREMENT, requirement_id=uuid.UUID(int=5))
    assert requirement.method is S.REQUIREMENT


@pytest.mark.parametrize(
    ("fields", "field", "reason"),
    [
        ({"type": G.INCREASE_WORKLOAD}, "goals.target", "required"),
        ({"type": G.INCREASE_WORKLOAD, "target": Quantity.of(1, "ms")}, "goals.target", "wrong_dimension"),
        ({"type": G.INCREASE_WORKLOAD, "target": Quantity.of(0, "requests/second")}, "goals.target",
         "out_of_range"),
        ({"type": G.AVAILABILITY_OBJECTIVE, "target": Quantity.of(100, "%")}, "goals.target", "out_of_range"),
        ({"type": G.COST_CEILING, "amount": D(5)}, "goals.currency", "required"),
        ({"type": G.COST_CEILING, "amount": D(5), "currency": "eur"}, "goals.currency", "invalid_currency"),
        ({"type": G.COST_CEILING, "amount": D(-1), "currency": "EUR"}, "goals.amount",
         "not_a_non_negative_amount"),
        (
            {"type": G.OBSERVABILITY_COVERAGE, "dimension": Coverage.LOGGING, "amount": D(1)},
            "goals.amount",
            "not_applicable",
        ),
        (
            {"type": G.SATISFY_REQUIREMENT, "requirement_id": uuid.UUID(int=1),
             "source": GoalSource.REQUIREMENT},
            "goals.derived_from",
            "required",
        ),
    ],
)  # fmt: skip
def test_invalid_goals_are_refused_with_the_field(fields: dict[str, Any], field: str, reason: str) -> None:
    with pytest.raises(InvalidEvolutionRequest) as refused:
        EvolutionGoal(**fields)
    assert (refused.value.details["field"], refused.value.details["reason"]) == (field, reason)


def test_a_finding_goal_names_an_engine_with_stable_finding_ids() -> None:
    with pytest.raises(InvalidEvolutionRequest, match="invalid"):
        FindingRef(S.COST, "cost_1")  # cost has no findings to address
    with pytest.raises(InvalidEvolutionRequest, match="invalid"):
        FindingRef(S.SECURITY, "")


def test_goals_round_trip_with_their_provenance_and_priority() -> None:
    derived = EvolutionGoal(
        G.AVAILABILITY_OBJECTIVE,
        target=Quantity.of("0.999", "ratio"),
        priority=RequirementPriority.HIGH,
        source=GoalSource.REQUIREMENT,
        derived_from=uuid.UUID(int=9),
    )
    for goal in (
        derived,
        WORKLOAD,
        EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.RELIABILITY, "r1")),
    ):
        assert EvolutionGoal.from_dict(json.loads(json.dumps(goal.to_dict()))) == goal
    with pytest.raises(InvalidEvolutionRequest, match="invalid"):
        EvolutionGoal.from_dict({"type": "reduce_operational_complexity"})  # no engine evaluates it


# --- candidates -----------------------------------------------------------------------------------


def test_a_candidate_is_a_proposal_with_a_stable_id() -> None:
    first = candidate()
    reordered = candidate(
        changes=(ConfigurationChange("db", "replicas", 2), ConfigurationChange("api", "replicas", 4)),
    )
    same = candidate(
        changes=(ConfigurationChange("api", "replicas", 4), ConfigurationChange("db", "replicas", 2)),
        title="Another title",  # presentation does not change the proposal
    )
    assert first.id.startswith("evo_")
    assert reordered.id == same.id != first.id
    assert [c.element_id for c in reordered.changes] == ["api", "db"]
    assert first.status is ProposalStatus.PROPOSED
    assert first.validation is ValidationState.NOT_VALIDATED
    assert candidate(rule=RuleRef("scale-replicas", 2)).id != first.id  # another rule version
    assert candidate(baseline=BaselineRef(ARCHITECTURE, 4, OTHER_HASH)).id != first.id


def test_stale_or_missing_evidence_never_supports_a_candidate() -> None:
    stale = EvidenceRef(S.CAPACITY, str(ANALYSIS), EvidenceState.STALE, "scaling:api", 2, OTHER_HASH)
    for evidence in ((stale,), (), (EvidenceRef(S.RELIABILITY, "latest", EvidenceState.MISSING),)):
        with pytest.raises(InvalidEvolutionResult):
            candidate(evidence=evidence)


def test_malformed_candidates_are_refused() -> None:
    for fields in (
        {"changes": ()},
        {"changes": (ConfigurationChange("api", "replicas", 4), ConfigurationChange("api", "replicas", 5))},
        {"goals": ()},
        {"title": " "},
        {"status": "applied"},
    ):
        with pytest.raises(InvalidEvolutionResult):
            candidate(**fields)


def test_effects_state_their_basis_and_candidates_round_trip() -> None:
    effects = {
        "benefits": (
            Effect("capacity", "utilization_within_goal", "Utilization 0.83 at 200 rps.", Basis.MODELED),
        ),
        "tradeoffs": (Effect("cost", "more_instances", "Two more instances are billed.", Basis.RULE),),
        "complexity": (
            Effect("operations", "more_replicas", "More replicas to deploy.", Basis.CONSIDERATION),
        ),
        "risks": (Effect("reliability", "shared_state", "Replicas may share state.", Basis.CONSIDERATION),),
    }
    proposed = candidate(
        **effects,
        missing=("api.configuration.max_connections",),
        impacts=(Impact(S.CAPACITY, "completed", S.SIMULATION, None, "c" * 64, "d" * 64, "v1"),),
    )
    restored = Candidate.from_dict(json.loads(json.dumps(proposed.to_dict())))
    assert restored == proposed
    assert restored.to_dict()["id"] == proposed.id
    tampered = proposed.to_dict() | {"id": "evo_" + "0" * 20}
    with pytest.raises(InvalidEvolutionResult):
        Candidate.from_dict(tampered)
    assert not {"score", "rank", "weight", "best", "winner"} & set(proposed.to_dict())


# --- results --------------------------------------------------------------------------------------


def result(**fields: Any) -> EvolutionResult:
    values: dict[str, Any] = {"baseline": BASELINE, "model_set": MODELS, "goals": (WORKLOAD,)}
    return EvolutionResult(**(values | fields))


def test_the_status_follows_what_was_established() -> None:
    assert result(candidates=(candidate(),)).status is EvolutionStatus.COMPLETED
    stale = EvolutionFinding(
        FindingType.STALE_EVIDENCE,
        "The capacity analysis is of revision 2.",
        WORKLOAD.key,
        evidence=(EvidenceRef(S.CAPACITY, str(ANALYSIS), EvidenceState.STALE, None, 2, OTHER_HASH),),
    )
    assert result(candidates=(candidate(),), findings=(stale,)).status is EvolutionStatus.PARTIAL
    unsupported = EvolutionFinding(FindingType.GOAL_UNSUPPORTED, "No engine evaluates it.", WORKLOAD.key)
    none = result(findings=(unsupported,))
    assert none.status is EvolutionStatus.INSUFFICIENT_EVIDENCE
    assert none.unsupported_goals == (WORKLOAD.key,)
    met = EvolutionFinding(FindingType.GOAL_ALREADY_MET, "Utilization is below the goal.", WORKLOAD.key)
    assert result(findings=(met,)).status is EvolutionStatus.COMPLETED  # an answer, not a gap


def test_results_are_ordered_counted_and_fingerprinted_without_a_winner() -> None:
    security_goal = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, "sec_1"))
    control = candidate(
        rule=RuleRef("require-tls", 1),
        category=CandidateCategory.SECURITY_CONTROL,
        changes=(ConfigurationChange("api-db", "tls", True),),
        goals=(security_goal.key,),
        evidence=(EvidenceRef(S.SECURITY, str(ANALYSIS), EvidenceState.CURRENT, "sec_1", 3, HASH),),
    )
    two = candidate(changes=(ConfigurationChange("api", "replicas", 4),))
    three = candidate(
        rule=RuleRef("scale-resources", 1),
        changes=(ConfigurationChange("api", "throughput_limit_per_second", 400),),
    )
    missing = EvolutionFinding(
        FindingType.MISSING_EVIDENCE, "No cost analysis exists.", None, missing=("cost_analysis",)
    )
    forward = result(
        goals=(WORKLOAD, security_goal), candidates=(control, two, three), findings=(missing,),
        limitations=(Limitation("proposals_only", "Nothing is applied."),),
        assumptions=(Evidence("peak", "Peak lasts two hours."),),
    )  # fmt: skip
    backward = result(
        goals=(security_goal, WORKLOAD), candidates=(three, two, control), findings=(missing,),
        limitations=(Limitation("proposals_only", "Nothing is applied."),),
        assumptions=(Evidence("peak", "Peak lasts two hours."),),
    )  # fmt: skip
    assert forward == backward
    assert forward.fingerprint == backward.fingerprint
    assert [c.category for c in forward.candidates] == [
        CandidateCategory.SCALING, CandidateCategory.SCALING, CandidateCategory.SECURITY_CONTROL,
    ]  # fmt: skip
    summary = forward.summary()
    assert summary["candidates_by_goal"] == {security_goal.key: 1, WORKLOAD.key: 2}  # alternatives
    assert summary["missing"] == 1
    assert forward.missing == ("cost_analysis",)
    text = json.dumps(forward.to_dict())
    assert not {"score", "rank", "weight", "best", "winner"} & set(json.loads(text))
    assert EvolutionResult.from_dict(json.loads(text)) == forward


def test_malformed_results_are_refused() -> None:
    for fields in (
        {"goals": ()},
        {"goals": (WORKLOAD, WORKLOAD)},
        {"candidates": (candidate(), candidate())},  # the same proposal twice
        {"candidates": (candidate(baseline=BaselineRef(ARCHITECTURE, 4, OTHER_HASH)),)},
        {"candidates": (candidate(goals=("cost_ceiling:1 EUR/month",)),)},  # a goal not asked for
        {"findings": (EvolutionFinding(FindingType.NO_APPLICABLE_RULE, "No rule.", "unknown:goal"),)},
    ):
        with pytest.raises(InvalidEvolutionResult):
            result(**fields)


# --- request and lifecycle ------------------------------------------------------------------------


def test_the_request_is_canonical_and_bounded() -> None:
    coverage = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.METRICS)
    request = EvolutionRequest(
        ARCHITECTURE,
        3,
        (WORKLOAD, coverage),
        requirement_ids=(uuid.UUID(int=2), uuid.UUID(int=1), uuid.UUID(int=2)),
        constraints=EvolutionConstraints(("db", "api"), (CandidateCategory.REDUNDANCY,), max_replicas=6),
        scope=("db", "api"),
        evidence=(EvidenceCitation(S.SECURITY, uuid.UUID(int=4)), EvidenceCitation(S.CAPACITY, ANALYSIS)),
        assumptions=(Evidence("peak", "Peak lasts two hours."),),
        label="Growth",
    )
    assert [g.key for g in request.goals] == sorted([WORKLOAD.key, coverage.key])
    assert request.requirement_ids == (uuid.UUID(int=1), uuid.UUID(int=2))
    assert request.scope == ("api", "db")
    assert request.cited(S.CAPACITY) == ANALYSIS
    assert request.cited(S.COST) is None
    inputs = request.inputs()
    assert inputs["constraints"] == {
        "frozen_elements": ["api", "db"], "excluded_categories": ["redundancy"], "max_replicas": 6,
    }  # fmt: skip
    assert "nodes" not in json.dumps(inputs)  # the topology is never part of the request


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"goals": ()}, "goals"),
        ({"goals": (WORKLOAD, WORKLOAD)}, "goals"),
        ({"revision_number": 0}, "revision_number"),
        ({"requirement_ids": ()}, "requirement_ids"),
        ({"scope": ()}, "scope"),
        ({"evidence": (EvidenceCitation(S.CAPACITY, ANALYSIS), EvidenceCitation(S.CAPACITY, ANALYSIS))},
         "evidence"),
        ({"assumptions": (Evidence("Peak", "x"),)}, "assumptions.key"),
        ({"label": ""}, "label"),
    ],
)  # fmt: skip
def test_invalid_requests_are_refused(fields: dict[str, Any], field: str) -> None:
    values: dict[str, Any] = {"architecture_id": ARCHITECTURE, "revision_number": 3, "goals": (WORKLOAD,)}
    with pytest.raises(InvalidEvolutionRequest) as refused:
        EvolutionRequest(**(values | fields))
    assert refused.value.details["field"] == field
    with pytest.raises(InvalidEvolutionRequest):
        EvolutionConstraints(max_replicas=0)
    with pytest.raises(InvalidEvolutionRequest):
        EvidenceCitation(S.SIMULATION, ANALYSIS)  # simulations are not evidence of the baseline alone


def test_the_lifecycle_ends_in_what_the_result_established() -> None:
    at = datetime(2026, 9, 27, tzinfo=UTC)
    analysis = EvolutionAnalysis(uuid.uuid4(), uuid.uuid4(), ARCHITECTURE, 3, HASH, PENDING, None, at)
    finished = analysis.start(at).finish(result(candidates=(candidate(),)), at)
    assert (finished.status, finished.finished) == ("completed", True)
    with pytest.raises(InvalidEvolutionTransition):
        finished.fail(EvolutionError("engine_error", "The analysis could not run."), at)
    failed = analysis.start(at).fail(EvolutionError("engine_error", "The analysis could not run."), at)
    assert failed.status == "failed"
    assert dataclasses.replace(analysis).result is None  # an analysis never carries the architecture
