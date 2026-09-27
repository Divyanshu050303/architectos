"""Observability policy and SLO / monitoring requirement traceability (Milestone 11, phase 7): fixed
conditions judged from declared facts, missing evidence never success, objectives traced to a
measurable and alerted indicator — never reported as met — and unsupported requirements identified."""

import dataclasses
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.traceability import RequirementRef
from core.domain.checks import CheckSource
from core.domain.engine_results import FindingBasis
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.results import (
    CheckResult,
    Condition,
    FindingType,
    ObservabilityFinding,
    ObservabilityResult,
)
from core.domain.observability.values import Dimension
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementStatus,
    RequirementType,
)
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity, Verdict
from engines.observability.context import ObservabilityContext
from engines.observability.engine import Registry, analyze
from engines.observability.policy import RULES, Policy
from engines.observability.slo_monitoring import SloMonitoring
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
V = Verdict
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
PROJECT = uuid.UUID(int=77)
NOW = datetime(2026, 9, 27, tzinfo=UTC)
CHECKS = Registry([Policy(), SloMonitoring()])
DEP = ConnectionKind.DEPENDENCY


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def link(
    source: str, target: str, kind: ConnectionKind = ConnectionKind.REQUEST, **values: Any
) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=kind,
        protocol="https" if kind is ConnectionKind.REQUEST else None,
        configuration=Configuration(values),
    )


def requirement(
    number: int,
    type_: RequirementType,
    category: str,
    statement: str,
    *,
    structured: dict[str, Any] | None = None,
    priority: RequirementPriority = RequirementPriority.HIGH,
    scope: RequirementScope = RequirementScope.SYSTEM,
    status: RequirementStatus = RequirementStatus.ACTIVE,
) -> Requirement:
    created = NewRequirement.create(
        project_id=PROJECT,
        created_by_user_id=uuid.uuid7(),
        type=type_,
        category=category,
        title=statement[:40],
        statement=statement,
        priority=priority,
        status=RequirementStatus.ACTIVE,
        structured_data=structured,
    )
    content = dataclasses.replace(created.content, scope=scope, status=status)
    return Requirement(
        id=uuid.UUID(int=number),
        project_id=PROJECT,
        number=number,
        version=1,
        content=content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def availability(number: int = 1, **fields: Any) -> Requirement:
    structured = {"metric": "availability", "operator": ">=", "value": "99.9", "unit": "%"}
    statement = "Checkout is available 99.9% of the time"
    return requirement(
        number, RequirementType.AVAILABILITY, "availability", statement, structured=structured, **fields
    )


def monitoring(number: int, statement: str, **fields: Any) -> Requirement:
    return requirement(number, RequirementType.OPERATIONAL, "monitoring", statement, **fields)


def run(
    nodes: Sequence[Node],
    links: Sequence[Connection] = (),
    *,
    policy: ArchitecturePolicy | None = None,
    requirements: Sequence[Requirement] = (),
) -> ObservabilityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    context = ObservabilityContext(
        ir,
        REVISION,
        ObservabilityAnalysisRequest(uuid.UUID(int=1), 1),
        policy=policy or ArchitecturePolicy(),
        requirements=tuple(requirements),
    )
    return analyze(context, CHECKS)


def check(result: ObservabilityResult, key: str) -> CheckResult:
    [found] = [c for c in result.checks if c.key == key]
    return found


def findings(result: ObservabilityResult, type_: FindingType) -> list[ObservabilityFinding]:
    return [f for f in result.findings if f.type is type_]


OBS = component("obs", NodeKind.OBSERVABILITY, alert_delivery="paging", retention_seconds=604_800)
METRICS_TO_OBS = link("api", "obs", DEP, telemetry=("metrics",))


# --- the policy ----------------------------------------------------------------------------------


def test_the_empty_policy_checks_nothing() -> None:
    result = run([component("api", criticality="critical")])
    assert result.checks == ()
    assert result.findings == ()


def test_every_observability_field_is_a_rule() -> None:
    assert set(RULES) == {
        "require_logs_on_critical",
        "required_metric_kinds_on_critical",
        "require_traces_on_critical",
        "require_trace_propagation",
        "require_health_checks_on_critical",
        "require_alerting_on_critical",
        "require_structured_logs",
        "require_correlation_ids",
        "require_ownership",
        "require_telemetry_collection",
        "min_telemetry_retention_seconds",
    }


def test_logs_on_critical_concerns_only_critical_components() -> None:
    policy = ArchitecturePolicy(require_logs_on_critical=True)
    nodes = [
        component("api", criticality="critical", logs=True),
        component("batch", criticality="standard", logs=False),
        component("stripe", NodeKind.EXTERNAL),
    ]
    satisfied = check(run(nodes, policy=policy), "policy.require_logs_on_critical")
    assert (satisfied.verdict, satisfied.node_ids, satisfied.source) == (
        V.SATISFIED,
        ("api",),
        CheckSource.POLICY,
    )

    nodes[0] = component("api", criticality="critical", logs=False)
    result = run(nodes, policy=policy)
    assert check(result, "policy.require_logs_on_critical").verdict is V.VIOLATED
    [violated] = findings(result, T.POLICY_VIOLATED)
    assert (violated.severity, violated.basis, violated.node_ids, violated.dimension) == (
        Severity.HIGH,
        FindingBasis.VIOLATION,
        ("api",),
        Dimension.LOGGING,
    )
    assert (violated.policy_rule, violated.check_key) == (
        "require_logs_on_critical",
        "policy.require_logs_on_critical",
    )


def test_an_undeclared_criticality_is_never_a_pass() -> None:
    result = run([component("api", logs=True)], policy=ArchitecturePolicy(require_logs_on_critical=True))
    rule = check(result, "policy.require_logs_on_critical")
    assert (rule.verdict, rule.missing) == (V.NOT_VERIFIABLE, ("api.configuration.criticality",))
    [unknown] = findings(result, T.POLICY_NOT_EVALUABLE)
    assert unknown.severity is Severity.MEDIUM  # one step below the rule's
    assert "not reported as met" in unknown.explanation


def test_nothing_critical_is_not_applicable() -> None:
    policy = ArchitecturePolicy(require_health_checks_on_critical=True)
    result = run([component("api", criticality="standard")], policy=policy)
    assert check(result, "policy.require_health_checks_on_critical").verdict is V.NOT_APPLICABLE


@pytest.mark.parametrize(
    ("metrics", "verdict"),
    [
        (("errors", "latency", "throughput"), V.SATISFIED),
        (("errors",), V.VIOLATED),
        (None, V.NOT_VERIFIABLE),
    ],
)
def test_required_metric_kinds(metrics: tuple[str, ...] | None, verdict: Verdict) -> None:
    values: dict[str, Any] = {"criticality": "critical"} | ({} if metrics is None else {"metrics": metrics})
    policy = ArchitecturePolicy(required_metric_kinds_on_critical=frozenset({"errors", "latency"}))
    result = run([component("api", **values)], policy=policy)
    assert check(result, "policy.required_metric_kinds_on_critical").verdict is verdict


def test_ownership_is_violated_when_no_owner_is_modeled() -> None:
    policy = ArchitecturePolicy(require_ownership=True)
    unowned = run([component("api", criticality="critical")], policy=policy)
    assert check(unowned, "policy.require_ownership").verdict is V.VIOLATED
    owned = run([component("api", criticality="critical", owner="team-payments")], policy=policy)
    assert check(owned, "policy.require_ownership").verdict is V.SATISFIED


def test_structured_logs_and_correlation_ids_concern_components_that_log() -> None:
    policy = ArchitecturePolicy(require_structured_logs=True, require_correlation_ids=True)
    nodes = [
        component("api", logs=True, structured_logs=True),
        component("worker", NodeKind.WORKER, logs=False),
        component("cron", NodeKind.WORKER),
    ]
    result = run(nodes, policy=policy)
    structured = check(result, "policy.require_structured_logs")
    assert (structured.verdict, structured.missing) == (V.NOT_VERIFIABLE, ("cron.configuration.logs",))
    correlation = check(result, "policy.require_correlation_ids")
    assert correlation.verdict is V.NOT_VERIFIABLE
    assert set(correlation.missing) == {"api.configuration.correlation_ids", "cron.configuration.logs"}


def test_telemetry_retention_on_observability_components() -> None:
    policy = ArchitecturePolicy(min_telemetry_retention_seconds=2_592_000)  # 30 days
    key = "policy.min_telemetry_retention_seconds"
    assert check(run([OBS], policy=policy), key).verdict is V.VIOLATED
    kept = component("obs", NodeKind.OBSERVABILITY, retention_seconds=2_592_000)
    assert check(run([kept], policy=policy), key).verdict is V.SATISFIED
    unknown = check(run([component("obs", NodeKind.OBSERVABILITY)], policy=policy), key)
    assert (unknown.verdict, unknown.missing) == (V.NOT_VERIFIABLE, ("obs.configuration.retention_seconds",))
    assert check(run([component("api")], policy=policy), key).verdict is V.NOT_APPLICABLE


def test_telemetry_collection_needs_a_modeled_path() -> None:
    policy = ArchitecturePolicy(require_telemetry_collection=True)
    api = component("api", criticality="critical", logs=True, metrics=("errors",), traces=False)
    alone = run([api, OBS], policy=policy)
    assert check(alone, "policy.require_telemetry_collection").verdict is V.VIOLATED
    links = [link("api", "obs", DEP, telemetry=("logs", "metrics"))]
    collected = run([api, OBS], links, policy=policy)
    assert check(collected, "policy.require_telemetry_collection").verdict is V.SATISFIED


def test_trace_propagation_on_flows_touching_critical_components() -> None:
    policy = ArchitecturePolicy(require_trace_propagation=True)
    key = "policy.require_trace_propagation"
    nodes = [
        component("api", criticality="critical", traces=True),
        component("orders", criticality="standard", traces=True),
    ]
    rule = check(run(nodes, [link("api", "orders")], policy=policy), key)
    assert (rule.verdict, rule.connection_ids, rule.missing) == (
        V.NOT_VERIFIABLE,
        ("api-orders",),
        ("api-orders.configuration.trace_propagation",),
    )
    broken = run(nodes, [link("api", "orders", trace_propagation=False)], policy=policy)
    [violated] = findings(broken, T.POLICY_VIOLATED)
    assert (violated.connection_ids, violated.dimension) == (("api-orders",), Dimension.TRACING)
    propagated = run(nodes, [link("api", "orders", trace_propagation=True)], policy=policy)
    assert check(propagated, key).verdict is V.SATISFIED


# --- SLO objectives ------------------------------------------------------------------------------


def test_fixture_9_an_objective_without_a_measurable_indicator() -> None:
    """An availability objective, but the critical component measures only latency."""
    api = component("api", criticality="critical", metrics=("latency",))
    result = run([api, OBS], [METRICS_TO_OBS], requirements=[availability()])
    measurable = check(result, "requirement.req-1.objective_measurable")
    assert (measurable.verdict, measurable.condition, measurable.source) == (
        V.VIOLATED,
        Condition.OBJECTIVE_MEASURABLE,
        CheckSource.REQUIREMENT,
    )
    assert measurable.mapping == "availability objective on availability → metric kinds availability, errors"
    assert measurable.requirement_id == str(uuid.UUID(int=1))
    [violated] = findings(result, T.REQUIREMENT_VIOLATED)
    assert (violated.severity, violated.node_ids, violated.dimension, violated.check_key) == (
        Severity.HIGH,  # the requirement's priority
        ("api",),
        Dimension.METRICS,
        "requirement.req-1.objective_measurable",
    )
    alerted = check(result, "requirement.req-1.objective_alerted")
    assert (alerted.verdict, alerted.missing) == (V.NOT_VERIFIABLE, ("api.configuration.alerts",))


def test_a_measured_and_alerted_objective_is_traceable_but_never_met() -> None:
    api = component("api", criticality="critical", metrics=("errors",), alerts=("errors",))
    result = run([api, OBS], [METRICS_TO_OBS], requirements=[availability()])
    for key in ("requirement.req-1.objective_measurable", "requirement.req-1.objective_alerted"):
        traced = check(result, key)
        assert traced.verdict is V.SATISFIED
        assert "met" not in traced.explanation.split()
    assert result.findings == ()


def test_a_health_alert_counts_for_availability() -> None:
    api = component(
        "api", criticality="critical", metrics=("availability",), health_check=True, alerts=("health",)
    )
    result = run([api, OBS], [METRICS_TO_OBS], requirements=[availability()])
    assert check(result, "requirement.req-1.objective_alerted").verdict is V.SATISFIED


def test_an_uncollected_indicator_or_undelivered_alert_is_not_verifiable() -> None:
    api = component("api", criticality="critical", metrics=("errors",), alerts=("errors",))
    result = run([api], requirements=[availability()])
    measurable = check(result, "requirement.req-1.objective_measurable")
    assert (measurable.verdict, measurable.missing) == (V.NOT_VERIFIABLE, ("api.collection",))
    alerted = check(result, "requirement.req-1.objective_alerted")
    assert (alerted.verdict, alerted.missing) == (V.NOT_VERIFIABLE, ("api.alert_delivery",))
    assert {f.severity for f in findings(result, T.REQUIREMENT_NOT_EVALUABLE)} == {Severity.MEDIUM}


def test_latency_and_throughput_objectives_map_to_their_kinds() -> None:
    latency = requirement(
        2,
        RequirementType.PERFORMANCE,
        "latency",
        "p95 under 300 ms",
        structured={"metric": "latency", "operator": "<=", "value": 300, "unit": "ms", "percentile": 95},
    )
    throughput = requirement(
        3,
        RequirementType.PERFORMANCE,
        "throughput",
        "500 requests per second",
        structured={"metric": "requests_per_second", "operator": ">=", "value": 500, "unit": "rps"},
    )
    api = component("api", criticality="critical", metrics=("latency",))
    result = run([api, OBS], [METRICS_TO_OBS], requirements=[latency, throughput])
    assert check(result, "requirement.req-2.objective_measurable").verdict is V.SATISFIED
    assert check(result, "requirement.req-3.objective_measurable").verdict is V.VIOLATED


def test_an_objective_no_metric_kind_measures_is_unsupported() -> None:
    rto = requirement(
        4,
        RequirementType.RELIABILITY,
        "rto",
        "Recover within 15 minutes",
        structured={"metric": "rto", "operator": "<=", "value": 15, "unit": "minutes"},
    )
    result = run([component("api", criticality="critical")], requirements=[rto])
    unsupported = check(result, "requirement.req-4")
    assert (unsupported.condition, unsupported.verdict) == (Condition.UNSUPPORTED, V.NOT_VERIFIABLE)
    assert "never reported as met" in unsupported.explanation
    assert result.findings == ()


def test_a_requirement_names_its_components() -> None:
    referenced = availability()
    api = dataclasses.replace(  # no criticality needed: the requirement names it
        component("api", metrics=("errors",)), requirement_refs=(RequirementRef(referenced.id, 1),)
    )
    result = run([api, component("other")], requirements=[referenced])
    measurable = check(result, "requirement.req-1.objective_measurable")
    assert (measurable.node_ids, measurable.verdict) == (("api",), V.NOT_VERIFIABLE)


def test_requirements_not_in_force_or_in_unmodeled_scopes() -> None:
    draft = availability(status=RequirementStatus.DRAFT)
    assert run([component("api", criticality="critical")], requirements=[draft]).checks == ()
    regional = availability(scope=RequirementScope.REGION)
    unsupported = check(run([component("api")], requirements=[regional]), "requirement.req-1")
    assert unsupported.condition is Condition.UNSUPPORTED
    assert "scope (region)" in unsupported.explanation


# --- monitoring requirements ---------------------------------------------------------------------


def test_monitoring_words_map_by_the_documented_table() -> None:
    wanted = monitoring(5, "Critical services expose health checks and page the on-call owner.")
    api = component("api", criticality="critical", health_check=True, alerts=("health",), owner="team-a")
    result = run([api], requirements=[wanted])
    keys = {c.key: (c.condition, c.verdict, c.mapping) for c in result.checks}
    assert keys == {
        "requirement.req-5.health_checks_on_critical": (
            Condition.HEALTH_CHECKS_ON_CRITICAL,
            V.SATISFIED,
            "monitoring + 'health checks'",
        ),
        "requirement.req-5.alerting_on_critical": (
            Condition.ALERTING_ON_CRITICAL,
            V.SATISFIED,
            "monitoring + 'page'",
        ),
        "requirement.req-5.ownership": (Condition.OWNERSHIP, V.SATISFIED, "monitoring + 'on-call'"),
    }


def test_a_single_monitoring_condition_keeps_the_requirement_key() -> None:
    wanted = monitoring(6, "Every call is traced.")
    result = run([component("api", criticality="critical", traces=False)], requirements=[wanted])
    traced = check(result, "requirement.req-6")
    assert (traced.condition, traced.verdict) == (Condition.TRACES_ON_CRITICAL, V.VIOLATED)
    [violated] = findings(result, T.REQUIREMENT_VIOLATED)
    assert violated.dimension is Dimension.TRACING


def test_monitoring_words_matching_nothing_are_unsupported() -> None:
    wanted = monitoring(7, "Dashboards for every team.")
    unsupported = check(
        run([component("api", criticality="critical")], requirements=[wanted]), "requirement.req-7"
    )
    assert (unsupported.condition, unsupported.verdict, unsupported.mapping) == (
        Condition.UNSUPPORTED,
        V.NOT_VERIFIABLE,
        None,
    )


def test_checks_and_findings_are_deterministic() -> None:
    nodes = [
        component("api", criticality="critical", metrics=("latency",)),
        component("db", NodeKind.DATABASE),
    ]
    policy = ArchitecturePolicy(require_ownership=True, require_logs_on_critical=True)
    first = run(nodes, policy=policy, requirements=[availability()])
    second = run(list(reversed(nodes)), policy=policy, requirements=[availability()])
    assert [f.id for f in first.findings] == [f.id for f in second.findings]
    assert first.checks == second.checks
