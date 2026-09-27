"""The observability analyzer contract, orchestrator and coverage (Milestone 11, phase 2)."""

import dataclasses
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.capacity.results import Certainty
from core.domain.engine_results import ModelSet
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.errors import InvalidObservabilityRequest
from core.domain.observability.results import (
    FindingCategory,
    FindingType,
    ObservabilityFinding,
    ObservabilityResult,
    ObservabilityStatus,
)
from core.domain.observability.values import CoverageState, Dimension
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import NewRequirement, Requirement
from core.domain.requirements.enums import RequirementPriority, RequirementStatus, RequirementType
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.observability.context import ObservabilityContext
from engines.observability.engine import (
    AnalyzerMeta,
    AnalyzerOutput,
    DuplicateAnalyzer,
    Progress,
    Registry,
    analyze,
)
from engines.observability.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
S = CoverageState
D = Dimension
T = FindingType


def meta(analyzer_id: str, **overrides: Any) -> AnalyzerMeta:
    fields: dict[str, Any] = {
        "id": analyzer_id,
        "version": 1,
        "name": analyzer_id.title(),
        "description": f"The {analyzer_id} analyzer.",
        "category": FindingCategory.LOGGING,
        "finding_types": (T.LOGS_NOT_MODELED,),
        "inputs": ("components",),
    }
    return AnalyzerMeta(**(fields | overrides))


@dataclass(frozen=True)
class Fake:
    meta: AnalyzerMeta
    run: Callable[[ObservabilityContext, Progress], Any]

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        output: AnalyzerOutput = self.run(context, progress)
        return output


def found(m: AnalyzerMeta, node_id: str, type_: FindingType = T.LOGS_NOT_MODELED) -> ObservabilityFinding:
    return ObservabilityFinding(
        type=type_,
        severity=Severity.LOW,
        certainty=Certainty.MODELED,
        title=f"About {node_id}",
        explanation="What was detected.",
        recommendation="What to review.",
        node_ids=(node_id,),
        analyzer_id=m.id,
        analyzer_version=m.version,
    )


def per_component(m: AnalyzerMeta) -> Fake:
    return Fake(m, lambda ctx, _: AnalyzerOutput(tuple(found(m, n.id) for n in ctx.components)))


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def carries(source: str, target: str, *signals: str, **values: Any) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol="https",
        configuration=Configuration(({"telemetry": signals} if signals else {}) | values),
    )


def context(
    nodes: Sequence[Node],
    links: Sequence[Connection] = (),
    requirements: Sequence[Requirement] = (),
    **request: Any,
) -> ObservabilityContext:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    return ObservabilityContext(
        ir,
        REVISION,
        ObservabilityAnalysisRequest(uuid.UUID(int=1), 1, **request),
        requirements=tuple(requirements),
    )


def run(
    nodes: Sequence[Node], links: Sequence[Connection] = (), registry: Registry | None = None, **request: Any
) -> ObservabilityResult:
    return analyze(context(nodes, links, **request), registry or default_registry())


def requirement(number: int) -> Requirement:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    created = NewRequirement.create(
        project_id=uuid.UUID(int=77),
        created_by_user_id=uuid.uuid7(),
        type=RequirementType.OPERATIONAL,
        category="monitoring",
        title="Monitor",
        statement="Critical services are monitored.",
        priority=RequirementPriority.HIGH,
        status=RequirementStatus.ACTIVE,
    )
    return Requirement(
        id=uuid.UUID(int=number),
        project_id=uuid.UUID(int=77),
        number=number,
        version=1,
        content=created.content,
        source=created.source,
        confidence=created.confidence,
        created_by_user_id=None,
        created_at=now,
        updated_at=now,
    )


# --- registry and requests -------------------------------------------------------------------------


def test_the_registry_refuses_duplicates_and_inconsistent_declarations() -> None:
    first = per_component(meta("logs"))
    for bad in (
        [first, per_component(meta("logs"))],
        [per_component(meta("x", inputs=("live_metrics",)))],
        [per_component(meta("x", produces=("score",)))],
        [per_component(meta("x", requires=("logs",))), first],
    ):
        with pytest.raises(DuplicateAnalyzer):
            Registry(bad)
    assert Registry([first]).analyzer_set() == ModelSet.of([("logs", 1)])


def test_selection_is_explicit_and_requests_name_only_what_exists() -> None:
    ran: list[str] = []

    def recording(analyzer_id: str, **overrides: Any) -> Fake:
        def run_it(ctx: ObservabilityContext, _: Progress) -> AnalyzerOutput:
            ran.append(analyzer_id)
            return AnalyzerOutput()

        return Fake(meta(analyzer_id, **overrides), run_it)

    registry = Registry([recording("a"), recording("b"), recording("c", requires=("a",))])
    analyze(context([component("api")], analyzers=("c", "a")), registry)
    assert ran == ["a", "c"]
    for request, reason in (
        ({"analyzers": ("ghost",)}, "unknown_analyzer"),
        ({"analyzers": ("c",)}, "requires_analyzer"),
        ({"scope": ("ghost",)}, "unknown_node"),
        ({"requirement_ids": (uuid.UUID(int=5),)}, "unknown_requirement"),  # not in force for the project
    ):
        with pytest.raises(InvalidObservabilityRequest) as error:
            analyze(context([component("api")], **request), registry)
        assert error.value.details["reason"] == reason
    analyze(context([component("api")], [], [requirement(5)], requirement_ids=(uuid.UUID(int=5),)), registry)


def test_a_failing_analyzer_is_isolated_and_logged_by_type(caplog: pytest.LogCaptureFixture) -> None:
    def crash(ctx: ObservabilityContext, _: Progress) -> AnalyzerOutput:
        raise ValueError("collector_token=abc")

    registry = Registry([Fake(meta("broken"), crash), per_component(meta("logs"))])
    with caplog.at_level(logging.ERROR, logger="architectos.observability"):
        result = analyze(context([component("api"), component("db", NodeKind.DATABASE)]), registry)
    assert [u.code for u in result.unsupported] == ["analyzer_failed"]
    assert len(result.findings) == 2
    assert "collector_token" not in caplog.text
    assert caplog.records[0].exc_info is None


@pytest.mark.parametrize(
    "bad",
    [
        lambda m: "not an output",
        lambda m: AnalyzerOutput((found(m, "api", T.TRACES_ABSENT),)),  # not declared
        lambda m: AnalyzerOutput((found(m, "ghost"),)),
        lambda m: AnalyzerOutput((dataclasses.replace(found(m, "api"), analyzer_id="other"),)),
    ],
)
def test_a_malformed_output_is_refused_and_the_rest_counts(bad: Callable[[AnalyzerMeta], Any]) -> None:
    m = meta("bad")
    result = analyze(
        context([component("api")]), Registry([Fake(m, lambda c, _: bad(m)), per_component(meta("logs"))])
    )
    assert [u.code for u in result.unsupported] == ["invalid_output"]
    assert {f.analyzer_id for f in result.findings} == {"logs"}


def test_a_scope_limits_components_and_findings() -> None:
    m = meta("all")
    every = Fake(m, lambda c, _: AnalyzerOutput(tuple(found(m, n) for n in ("api", "db"))))
    result = analyze(
        context([component("api"), component("db", NodeKind.DATABASE)], scope=("db",)), Registry([every])
    )
    assert [c.node_id for c in result.components] == ["db"]
    assert [f.node_ids for f in result.findings] == [("db",)]


# --- coverage ------------------------------------------------------------------------------------


def coverage_of(result: ObservabilityResult, node_id: str) -> dict[Dimension, CoverageState]:
    return dict(next(c for c in result.components if c.node_id == node_id).coverage)


def test_coverage_states_come_only_from_what_is_declared() -> None:
    nodes = [
        component(
            "api",
            criticality="critical",
            logs=True,
            metrics=("errors",),
            traces=False,
            health_check=True,
            alerts=(),
        ),
        component("bare"),
        component("psp", NodeKind.EXTERNAL),
        component("obs", NodeKind.OBSERVABILITY),
    ]
    result = run(nodes, [carries("api", "obs", "logs")])
    assert coverage_of(result, "api") == {
        D.LOGGING: S.MODELED,  # declared and carried to an observability component
        D.METRICS: S.PARTIAL,  # declared, no modeled collection path
        D.TRACING: S.ABSENT,  # declared off
        D.HEALTH_CHECKS: S.PARTIAL,  # exposed, nobody modeled checks it
        D.ALERTING: S.ABSENT,
    }
    assert set(coverage_of(result, "bare").values()) == {S.UNKNOWN}  # unknown, never absent
    assert set(coverage_of(result, "psp").values()) == {S.UNSUPPORTED}
    bare = next(c for c in result.components if c.node_id == "bare")
    assert bare.missing == tuple(
        sorted(
            f"configuration.{p}"
            for p in ("criticality", "logs", "metrics", "traces", "health_check", "alerts")
        )
    )


def test_collection_follows_modeled_telemetry_paths_through_collectors() -> None:
    nodes = [
        component("api", traces=True, metrics=("latency",)),
        component("agent", NodeKind.WORKER),
        component("obs", NodeKind.OBSERVABILITY),
    ]
    links = [
        carries("api", "agent", "traces"),
        carries("agent", "obs", "traces"),
        carries("api", "obs"),
    ]  # no signal declared
    result = run(nodes, links)
    assert coverage_of(result, "api")[D.TRACING] is S.MODELED  # via the agent
    assert coverage_of(result, "api")[D.METRICS] is S.PARTIAL  # the direct connection declares no telemetry
    ctx = context(nodes, links)
    assert ctx.collected("traces")["api"] == ("api-agent", "agent-obs")


def test_health_checks_are_modeled_when_something_checks_them() -> None:
    nodes = [component("lb", NodeKind.LOAD_BALANCER), component("api", health_check=True)]
    checked = carries("lb", "api", health_check=True)
    assert coverage_of(run(nodes, [checked]), "api")[D.HEALTH_CHECKS] is S.MODELED
    unchecked = carries("lb", "api")
    assert coverage_of(run(nodes, [unchecked]), "api")[D.HEALTH_CHECKS] is S.PARTIAL


def test_alerting_is_modeled_with_its_signal_and_a_delivery_path() -> None:
    def nodes(delivery: str, alerts: tuple[str, ...]) -> list[Node]:
        return [
            component("api", metrics=("errors",), alerts=alerts),
            component("obs", NodeKind.OBSERVABILITY, alert_delivery=delivery),
        ]

    links = [carries("api", "obs", "metrics")]
    assert coverage_of(run(nodes("paging", ("errors",)), links), "api")[D.ALERTING] is S.MODELED
    assert (
        coverage_of(run(nodes("none", ("errors",)), links), "api")[D.ALERTING] is S.PARTIAL
    )  # never delivered
    assert (
        coverage_of(run(nodes("paging", ("latency",)), links), "api")[D.ALERTING] is S.PARTIAL
    )  # no such signal


# --- statuses and determinism --------------------------------------------------------------------


def test_statuses_and_limitations() -> None:
    assert run([node("web", NodeKind.CLIENT)]).status is ObservabilityStatus.UNSUPPORTED
    bare = run([component("api")])
    assert bare.status is ObservabilityStatus.INSUFFICIENT_INPUT  # nothing declared: nothing claimed
    assert {x.code for x in bare.limitations} == {"configuration_only", "no_defaults"}
    assert run([component("api", logs=False)]).status is ObservabilityStatus.PARTIAL


def test_the_same_inputs_give_the_same_result_whatever_the_order() -> None:
    nodes = [
        component("api", logs=True, traces=True),
        component("agent", NodeKind.WORKER),
        component("obs", NodeKind.OBSERVABILITY),
    ]
    links = [carries("api", "agent", "logs", "traces"), carries("agent", "obs", "logs", "traces")]
    registry = Registry([per_component(meta("logs"))])
    first = run(nodes, links, registry)
    again = run(list(reversed(nodes)), list(reversed(links)), registry)
    assert first.to_dict() == again.to_dict()
    ctx = context(nodes, links)
    assert (
        ctx.fingerprint != dataclasses.replace(ctx, policy=ArchitecturePolicy(require_tls=True)).fingerprint
    )
