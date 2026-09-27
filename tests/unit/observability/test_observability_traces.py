"""Tracing and context propagation (Milestone 11, phase 5): along modeled request flows only, with
the exact connections named, and never a claim of end-to-end completeness."""

import uuid
from collections.abc import Sequence
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.engine_results import FindingBasis
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.results import FindingType, ObservabilityFinding, ObservabilityResult
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.observability.context import ObservabilityContext
from engines.observability.engine import Registry, analyze
from engines.observability.traces import Traces
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
TRACES = Registry([Traces()])
OBS = node("obs", NodeKind.OBSERVABILITY)


def service(node_id: str, **values: Any) -> Node:
    return node(node_id, configuration=Configuration({"traces": True} | values))


def flow(
    source: str, target: str, kind: ConnectionKind = ConnectionKind.REQUEST, **values: Any
) -> Connection:
    protocol = None if kind is ConnectionKind.DEPENDENCY else "https"
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=kind,
        protocol=protocol,
        configuration=Configuration(values),
    )


def export(source: str) -> Connection:
    return flow(source, "obs", telemetry=("traces",))


def run(nodes: Sequence[Node], links: Sequence[Connection]) -> ObservabilityResult:
    ir = ArchitectureIR("Shop", nodes=(*nodes, OBS), connections=tuple(links))
    return analyze(
        ObservabilityContext(ir, REVISION, ObservabilityAnalysisRequest(uuid.UUID(int=1), 1)), TRACES
    )


def of(result: ObservabilityResult, type_: FindingType) -> list[ObservabilityFinding]:
    return [f for f in result.findings if f.type is type_]


PATH = [service("gw"), service("orders"), service("payments")]
EXPORTS = [export("gw"), export("orders"), export("payments")]


def test_a_path_with_propagation_represented_raises_nothing() -> None:
    links = [flow("gw", "orders", trace_propagation=True), flow("orders", "payments", trace_propagation=True)]
    assert run(PATH, [*links, *EXPORTS]).findings == ()


def test_a_path_with_incomplete_propagation_evidence() -> None:
    links = [flow("gw", "orders", trace_propagation=True), flow("orders", "payments")]
    [unknown] = run(PATH, [*links, *EXPORTS]).findings
    assert (unknown.type, unknown.basis, unknown.connection_ids) == (
        T.PROPAGATION_NOT_MODELED,
        FindingBasis.NOT_EVALUABLE,
        ("orders-payments",),
    )
    assert unknown.missing == ("orders-payments.configuration.trace_propagation",)
    assert "does not establish it" in unknown.explanation  # tracing on both ends is not propagation


def test_declared_non_propagation_breaks_the_trace() -> None:
    nodes = [service("gw", criticality="critical"), service("orders")]
    [broken] = run(
        nodes, [flow("gw", "orders", trace_propagation=False), export("gw"), export("orders")]
    ).findings
    assert (broken.type, broken.basis, broken.severity) == (
        T.PROPAGATION_BROKEN,
        FindingBasis.CONTROL_GAP,
        Severity.HIGH,
    )
    assert (broken.node_ids, broken.connection_ids) == (("gw", "orders"), ("gw-orders",))


def test_a_component_that_terminates_the_context_ends_its_callers_traces() -> None:
    nodes = [service("gw"), service("legacy", trace_context="terminate"), service("db-api")]
    links = [
        flow("gw", "legacy", trace_propagation=True),
        flow("legacy", "db-api", trace_propagation=True),
        *(export(n) for n in ("gw", "legacy", "db-api")),
    ]
    [ended] = of(run(nodes, links), T.PROPAGATION_BROKEN)
    assert (ended.node_ids, ended.connection_ids) == (("legacy",), ("legacy-db-api",))


def test_only_request_flows_between_traced_components_are_judged() -> None:
    nodes = [
        service("api"),
        node("db", NodeKind.DATABASE, configuration=Configuration({"traces": True})),
        service("untraced", traces=False),
        service("bus"),
    ]
    links = [
        flow("api", "db", ConnectionKind.DATA_ACCESS),  # data access: not a request flow
        flow("api", "bus", ConnectionKind.PUBLISH),  # a message flow: judged
        flow("api", "untraced"),  # one end does not trace: nothing to propagate
        *(export(n) for n in ("api", "db", "bus")),
    ]
    assert [f.connection_ids for f in run(nodes, links).findings] == [("api-bus",)]


def test_critical_components_must_model_tracing_and_its_collection() -> None:
    result = run([node("api", configuration=Configuration({"criticality": "critical"}))], [])
    assert [f.type for f in result.findings] == [T.TRACES_NOT_MODELED]
    result = run([node("api", configuration=Configuration({"criticality": "critical", "traces": False}))], [])
    assert [f.type for f in result.findings] == [T.TRACES_ABSENT]
    [uncollected] = run([service("api", criticality="critical")], []).findings
    assert (uncollected.type, uncollected.severity) == (T.TELEMETRY_NOT_COLLECTED, Severity.MEDIUM)


def test_no_completeness_or_sampling_is_claimed_and_the_analysis_is_deterministic() -> None:
    links = [flow("gw", "orders", trace_propagation=True), flow("orders", "payments")]
    first = run(PATH, [*links, *EXPORTS])
    again = run(list(reversed(PATH)), list(reversed([*links, *EXPORTS])))
    assert first.to_dict() == again.to_dict()
    text = str(first.to_dict()).lower()
    assert "sampl" not in text
    assert "complete trace" not in text
