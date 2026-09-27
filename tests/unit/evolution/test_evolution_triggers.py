"""Evidence-driven triggers (Milestone 13, phase 3): triggers from the real engines' stored results,
each referencing its source analysis, revision, content hash and model version; stale evidence
reported and never used; missing evidence requested, not invented; no urgency; deterministic."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.capacity.analyses import AnalysisReport, AnalysisRequest, CapacityAnalysis
from core.domain.capacity.units import Quantity
from core.domain.capacity.workload import WorkloadProfile, WorkloadType
from core.domain.engine_results import Evidence
from core.domain.evolution.candidates import BaselineRef
from core.domain.evolution.evidence import (
    RequirementCheck,
    StoredAnalysis,
    from_capacity,
    from_observability,
    from_reliability,
    from_security,
)
from core.domain.evolution.goals import EvolutionGoal, FindingRef
from core.domain.evolution.results import FindingType
from core.domain.evolution.values import EvidenceSource, EvidenceState, GoalType
from core.domain.observability.analyses import ObservabilityAnalysis, ObservabilityAnalysisRequest
from core.domain.observability.reports import ObservabilityReport
from core.domain.observability.values import Dimension as Coverage
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.reliability.analyses import ReliabilityAnalysis, ReliabilityAnalysisRequest
from core.domain.reliability.reports import ReliabilityReport
from core.domain.security.analyses import SecurityAnalysis, SecurityAnalysisRequest
from core.domain.security.reports import SecurityReport
from core.domain.validation.options import RevisionInfo
from engines.capacity.service import DeterministicCapacityEngine
from engines.evolution.rulebook import default_registry
from engines.evolution.rules import RuleContext, generate
from engines.evolution.trigger_engine import TriggerEvaluation, evaluate
from engines.observability.service import DeterministicObservabilityEngine
from engines.reliability.service import DeterministicReliabilityEngine
from engines.security.service import DeterministicSecurityEngine
from tests.unit.architecture_ir.builders import connection, node

S, G, F = EvidenceSource, GoalType, FindingType
ARCHITECTURE = uuid.UUID(int=7)
AT = datetime(2026, 9, 27, tzinfo=UTC)
RPS = "requests/second"


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: ConfigValue) -> Any:
    return node(node_id, kind, configuration=Configuration(values))


def shop(name: str = "Shop") -> ArchitectureIR:
    """api needs more replicas at 200 rps; db is a single replica holding confidential data
    unencrypted, reached over a flow without tls; api declares no logs."""
    routed: dict[str, ConfigValue] = {"traffic_ratio": Decimal(1)}
    return ArchitectureIR(
        name,
        nodes=(
            node("web", NodeKind.CLIENT),
            component(
                "api", replicas=2, throughput_per_replica_per_second=60, criticality="critical", logs=False,
                metrics=("latency",), traces=True, health_check=True, alerts=("errors",),
                availability=Decimal("0.999"), failure_independence="independent",
            ),
            component(
                "db", NodeKind.DATABASE, replicas=1, throughput_limit_per_second=500,
                data_classification="confidential", encryption_at_rest=False, availability=Decimal("0.99"),
                failure_independence="independent",
            ),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https",
                       configuration=Configuration(routed)),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql",
                       configuration=Configuration({"tls": False, "data_classification": "confidential",
                                                    **routed})),
        ),
    )  # fmt: skip


IR = shop()
BASELINE = BaselineRef(ARCHITECTURE, 1, content_hash(IR))
REVISION = RevisionInfo(str(ARCHITECTURE), 1, BASELINE.content_hash)


def capacity(rate: int = 200, ir: ArchitectureIR = IR, number: int = 1) -> StoredAnalysis:
    revision = RevisionInfo(str(ARCHITECTURE), number, content_hash(ir))
    workload = WorkloadProfile("Peak", WorkloadType.REQUEST_RESPONSE, peak_rate=Quantity.of(rate, RPS))
    request = AnalysisRequest(ARCHITECTURE, number, workload)
    output = DeterministicCapacityEngine().analyze(ir, revision, request, ())
    analysis = (
        CapacityAnalysis(
            uuid.uuid4(), uuid.uuid4(), ARCHITECTURE, number, revision.content_hash, "pending", None, AT
        )
        .start(AT)
        .finish(output.result, AT)
    )
    report = AnalysisReport.of(analysis, request.inputs(), output.scaling, output.unsupported_scaling)
    return from_capacity(report, output.result.bottlenecks)


def reliability() -> StoredAnalysis:
    result = DeterministicReliabilityEngine().analyze(
        IR, REVISION, ReliabilityAnalysisRequest(ARCHITECTURE, 1), ()
    )
    analysis = (
        ReliabilityAnalysis(
            uuid.uuid4(), uuid.uuid4(), ARCHITECTURE, 1, BASELINE.content_hash, "pending", None, AT
        )
        .start(AT)
        .finish(result, AT)
    )
    return from_reliability(ReliabilityReport.of(analysis, {}), result.findings)


def security() -> StoredAnalysis:
    request = SecurityAnalysisRequest(ARCHITECTURE, 1)
    result = DeterministicSecurityEngine().analyze(IR, REVISION, request, ArchitecturePolicy(), ())
    analysis = (
        SecurityAnalysis(
            uuid.uuid4(), uuid.uuid4(), ARCHITECTURE, 1, BASELINE.content_hash, "pending", None, AT
        )
        .start(AT)
        .finish(result, AT)
    )
    return from_security(SecurityReport.of(analysis, {}), result.findings)


def observability() -> StoredAnalysis:
    request = ObservabilityAnalysisRequest(ARCHITECTURE, 1)
    result = DeterministicObservabilityEngine().analyze(IR, REVISION, request, ArchitecturePolicy(), ())
    analysis = (
        ObservabilityAnalysis(
            uuid.uuid4(), uuid.uuid4(), ARCHITECTURE, 1, BASELINE.content_hash, "pending", None, AT
        )
        .start(AT)
        .finish(result, AT)
    )
    return from_observability(ObservabilityReport.of(analysis, {}), result.findings)


def stored(source: EvidenceSource, **fields: Any) -> StoredAnalysis:
    values: dict[str, Any] = {
        "source": source, "analysis_id": uuid.UUID(int=99), "revision_number": 1,
        "content_hash": BASELINE.content_hash, "model_version": "v1", "status": "completed",
    }  # fmt: skip
    return StoredAnalysis(**(values | fields))


WORKLOAD = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(200, RPS))


def run(goals: tuple[EvolutionGoal, ...], *analyses: StoredAnalysis, **options: Any) -> TriggerEvaluation:
    return evaluate(IR, BASELINE, goals, {a.source: a for a in analyses}, **options)


def types(evaluation: TriggerEvaluation) -> set[FindingType]:
    return {f.type for f in evaluation.findings}


def test_fixture_1_a_modeled_bottleneck_becomes_a_trigger_and_a_candidate() -> None:
    analysis = capacity()
    evaluation = run((WORKLOAD,), analysis)
    [trigger] = evaluation.triggers
    assert (trigger.kind.value, trigger.code, trigger.element_id) == ("scaling_option", "work_rate", "api")
    assert (trigger.fact("current"), trigger.fact("required"), trigger.fact("scaling")) == (
        "2",
        "4",
        "horizontal",
    )
    ref = trigger.evidence
    assert (ref.source, ref.reference, ref.state) == (
        S.CAPACITY,
        str(analysis.analysis_id),
        EvidenceState.CURRENT,
    )
    assert (ref.revision_number, ref.content_hash, ref.model_version) == (
        1,
        BASELINE.content_hash,
        analysis.model_version,
    )
    assert ref.item == "scaling:api:work_rate:horizontal"
    assert "severity" not in trigger.to_dict()  # no urgency is inferred
    context = RuleContext(IR, BASELINE, {WORKLOAD.key: WORKLOAD})
    [candidate] = generate(context, evaluation.triggers, default_registry()).candidates
    assert candidate.changes[0].to_dict() == {"element_id": "api", "property": "replicas", "value": 4}


def test_capacity_evidence_of_another_workload_does_not_describe_the_goal() -> None:
    evaluation = run((WORKLOAD,), capacity(rate=100))
    assert evaluation.triggers == ()
    [finding] = evaluation.findings
    assert finding.type is F.GOAL_NOT_EVALUABLE
    assert finding.missing == ("a capacity analysis of the baseline at 200 requests/second",)


def test_a_goal_already_met_is_reported_not_triggered() -> None:
    small = EvolutionGoal(G.INCREASE_WORKLOAD, target=Quantity.of(50, RPS))
    evaluation = run((small,), capacity(rate=50))
    assert (evaluation.triggers, types(evaluation)) == ((), {F.GOAL_ALREADY_MET})


def test_fixture_11_stale_evidence_against_a_newer_revision_is_never_used() -> None:
    old = capacity(ir=shop("Shop v1"), number=1)  # another content
    baseline = BaselineRef(ARCHITECTURE, 2, BASELINE.content_hash)
    evaluation = evaluate(IR, baseline, (WORKLOAD,), {S.CAPACITY: old})
    assert evaluation.triggers == ()
    assert types(evaluation) == {F.STALE_EVIDENCE, F.GOAL_NOT_EVALUABLE}
    [stale] = [f for f in evaluation.findings if f.type is F.STALE_EVIDENCE]
    assert stale.evidence[0].state is EvidenceState.STALE
    assert stale.evidence[0].content_hash == old.content_hash
    assert [e.state for e in evaluation.evidence] == [EvidenceState.STALE]
    restored = replace(old, content_hash=BASELINE.content_hash)  # same content, another number: current
    assert evaluate(IR, baseline, (WORKLOAD,), {S.CAPACITY: restored}).triggers


def test_missing_evidence_is_requested_not_invented() -> None:
    ceiling = EvolutionGoal(G.COST_CEILING, amount=Decimal(500), currency="USD")
    evaluation = run((ceiling,))
    assert evaluation.triggers == ()
    assert types(evaluation) == {F.MISSING_EVIDENCE, F.GOAL_NOT_EVALUABLE}
    assert [(e.source, e.state) for e in evaluation.evidence] == [(S.COST, EvidenceState.MISSING)]


def test_fixture_3_a_cost_ceiling_against_valid_pricing_evidence() -> None:
    ceiling = EvolutionGoal(G.COST_CEILING, amount=Decimal(500), currency="USD")

    def cost(monthly: str, complete: bool, currency: str = "USD") -> StoredAnalysis:
        facts = (
            Evidence("currency", currency),
            Evidence("monthly", monthly),
            Evidence("complete", "true" if complete else "false"),
            Evidence("largest_component", "db"),
        )
        return stored(S.COST, facts=facts)

    assert types(run((ceiling,), cost("420", complete=True))) == {F.GOAL_ALREADY_MET}
    over = run((ceiling,), cost("640.5", complete=True))
    [finding] = over.findings
    assert finding.type is F.NO_APPLICABLE_RULE
    assert "640.5 USD/month exceeds the ceiling 500 USD/month; the largest component is db" in finding.message
    assert finding.element_ids == ("db",)
    assert types(run((ceiling,), cost("420", complete=False))) == {F.GOAL_NOT_EVALUABLE}  # a lower bound
    assert "lower bound" in run((ceiling,), cost("620", complete=False)).findings[0].message
    assert types(run((ceiling,), cost("420", complete=True, currency="EUR"))) == {F.GOAL_NOT_EVALUABLE}


def test_fixture_4_reliability_findings_with_explicit_redundancy_semantics() -> None:
    goal = EvolutionGoal(G.AVAILABILITY_OBJECTIVE, target=Quantity.of("0.999", "ratio"))
    evaluation = run((goal,), reliability())
    triggered = {(t.code, t.element_id) for t in evaluation.triggers}
    assert ("single_point_of_failure", "db") in triggered
    assert all(t.source is S.RELIABILITY for t in evaluation.triggers)
    unknown = [f for f in evaluation.findings if f.type is F.MISSING_EVIDENCE]  # not modeled: not a trigger
    assert all(len(f.element_ids) == 1 for f in unknown)  # one finding per element, each exact
    assert {"api", "db"} <= {e for f in unknown for e in f.element_ids}
    context = RuleContext(IR, BASELINE, {goal.key: goal})
    generation = generate(context, evaluation.triggers, default_registry())
    assert [c.rule.id for c in generation.candidates] == ["add-replica"]
    assert F.NO_APPLICABLE_RULE in {f.type for f in generation.findings}  # e.g. missing failover: no rule


def test_fixture_5_a_security_finding_linked_to_a_supported_control() -> None:
    analysis = security()
    [transit] = [i for i in analysis.items if i.code == "unencrypted_data_in_transit"]
    assert transit.element_id == "api-db"  # a connection finding concerns its connection
    goal = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, transit.item))
    evaluation = run((goal,), analysis)
    [trigger] = evaluation.triggers
    assert trigger.message is not None
    assert trigger.message.startswith("Review whether api-db")
    context = RuleContext(IR, BASELINE, {goal.key: goal})
    [candidate] = generate(context, evaluation.triggers, default_registry()).candidates
    assert (candidate.rule.id, candidate.changes[0].element_id) == ("require-tls", "api-db")
    addressed = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, "sec_not_reported"))
    assert types(run((addressed,), analysis)) == {F.GOAL_ALREADY_MET}


def test_fixture_6_an_observability_requirement_with_a_modeled_coverage_gap() -> None:
    logging = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.LOGGING)
    evaluation = run((logging,), observability())
    [trigger] = evaluation.triggers
    assert (trigger.code, trigger.element_id) == ("logs_absent", "api")
    tracing = EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.TRACING)
    traced = run((tracing,), observability())
    assert {t.code for t in traced.triggers} <= {
        "telemetry_not_collected",
        "traces_absent",
        "propagation_broken",
    }
    context = RuleContext(IR, BASELINE, {tracing.key: tracing})
    assert generate(context, traced.triggers, default_registry()).candidates == ()  # nothing invented


def test_fixture_7_a_requirement_no_engine_evaluates_is_unsupported() -> None:
    requirement = uuid.UUID(int=41)
    goal = EvolutionGoal(G.SATISFY_REQUIREMENT, requirement_id=requirement)
    everything = (reliability(), security(), observability(), stored(S.VALIDATION))
    evaluation = run((goal,), *everything)
    assert types(evaluation) == {F.GOAL_UNSUPPORTED}
    partial = run((goal,), security())  # the others missing: it may be evaluable once they exist
    assert F.GOAL_NOT_EVALUABLE in types(partial)
    assert F.GOAL_UNSUPPORTED not in types(partial)


def test_requirement_verdicts_decide_the_goal() -> None:
    requirement = uuid.UUID(int=42)
    goal = EvolutionGoal(G.SATISFY_REQUIREMENT, requirement_id=requirement)

    def with_check(verdict: str) -> tuple[StoredAnalysis, ...]:
        check = RequirementCheck(str(requirement), verdict, "requirement.req-1", ("api.configuration.tls",))
        return (stored(S.SECURITY, checks=(check,)), stored(S.RELIABILITY), stored(S.OBSERVABILITY),
                stored(S.VALIDATION))  # fmt: skip

    assert types(run((goal,), *with_check("satisfied"))) == {F.GOAL_ALREADY_MET}
    unverifiable = run((goal,), *with_check("not_verifiable"))
    assert types(unverifiable) == {F.MISSING_EVIDENCE}
    assert unverifiable.findings[0].missing == ("api.configuration.tls",)
    assert types(run((goal,), *with_check("violated"))) == {F.NO_APPLICABLE_RULE}


def test_the_scope_limits_the_triggers() -> None:
    goal = EvolutionGoal(G.AVAILABILITY_OBJECTIVE, target=Quantity.of("0.999", "ratio"))
    scoped = run((goal,), reliability(), scope=("api",))
    assert {t.element_id for t in scoped.triggers} <= {"api"}
    analysis = security()
    transit = next(i for i in analysis.items if i.code == "unencrypted_data_in_transit")
    tls = EvolutionGoal(G.ADDRESS_FINDING, finding=FindingRef(S.SECURITY, transit.item))
    assert run((tls,), analysis, scope=("db",)).triggers  # a connection is in scope through an endpoint
    outside = run((tls,), analysis, scope=("web",))
    assert (outside.triggers, types(outside)) == ((), {F.GOAL_NOT_EVALUABLE})


@pytest.mark.parametrize("seed", [1, 2])
def test_trigger_evaluation_is_deterministic(seed: int) -> None:
    goals = (
        WORKLOAD,
        EvolutionGoal(G.AVAILABILITY_OBJECTIVE, target=Quantity.of("0.999", "ratio")),
        EvolutionGoal(G.OBSERVABILITY_COVERAGE, dimension=Coverage.LOGGING),
    )
    analyses = (capacity(), reliability(), observability())
    first = run(goals, *analyses)
    again = evaluate(IR, BASELINE, goals[::-1] if seed == 1 else goals, {a.source: a for a in analyses[::-1]})
    assert [t.to_dict() for t in again.triggers] == [t.to_dict() for t in first.triggers]
    assert [f.to_dict() for f in again.findings] == [f.to_dict() for f in first.findings]
    assert [t.key for t in first.triggers] == sorted(t.key for t in first.triggers)
