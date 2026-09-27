"""The ten architecture fixtures of the milestone (phase 10), each through every shipped analyzer:
what is found, what is not, and that unknown is never reported as configured. Also: the whole
registry is deterministic, the architecture is never modified, the engine reads no telemetry and
stays apart from ArchitectOS's own runtime, and its cost stays bounded on a large graph."""

import ast
import json
import random
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import to_dict
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.results import (
    FindingBasis,
    FindingType,
    ObservabilityResult,
    ObservabilityStatus,
)
from core.domain.observability.values import CoverageState, Dimension
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Verdict
from engines.observability.context import ObservabilityContext
from engines.observability.engine import analyze
from engines.observability.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.observability.test_observability_policy_requirements import availability

T = FindingType
S = CoverageState
D = Dimension
K = ConnectionKind
ROOT = Path(__file__).resolve().parents[3]
REVISION = RevisionInfo("arch-1", 1, "c" * 64)


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def link(source: str, target: str, kind: ConnectionKind = K.REQUEST, **values: Any) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=kind,
        protocol="https" if kind is K.REQUEST else None,
        configuration=Configuration(values),
    )


def ship(source: str, *signals: str) -> Connection:
    """Telemetry exported from ``source`` to the observability backend."""
    return link(source, "obs", K.DEPENDENCY, telemetry=signals)


def run(
    nodes: Sequence[Node],
    links: Sequence[Connection] = (),
    *,
    policy: ArchitecturePolicy | None = None,
    requirements: Sequence[Requirement] = (),
) -> ObservabilityResult:
    ir = ArchitectureIR("Fixture", nodes=tuple(nodes), connections=tuple(links))
    context = ObservabilityContext(
        ir,
        REVISION,
        ObservabilityAnalysisRequest(uuid.UUID(int=1), 1),
        policy=policy or ArchitecturePolicy(),
        requirements=tuple(requirements),
    )
    return analyze(context, default_registry())


def types(result: ObservabilityResult, node_id: str | None = None) -> set[FindingType]:
    return {f.type for f in result.findings if node_id is None or node_id in f.node_ids}


def coverage(result: ObservabilityResult, node_id: str) -> dict[Dimension, CoverageState]:
    [found] = [c for c in result.components if c.node_id == node_id]
    return dict(found.coverage)


OBS = component("obs", NodeKind.OBSERVABILITY, alert_delivery="paging")
LOGGING = {T.LOGS_ABSENT, T.LOGS_NOT_MODELED}
METRICS = {T.METRICS_ABSENT, T.METRICS_NOT_MODELED}


def test_1_critical_service_with_explicitly_modeled_logs_and_metrics() -> None:
    api = component("api", criticality="critical", logs=True, metrics=("errors", "latency"))
    result = run([api, OBS], [ship("api", "logs", "metrics")])
    assert not (LOGGING | METRICS | {T.TELEMETRY_NOT_COLLECTED}) & types(result, "api")
    states = coverage(result, "api")
    assert (states[D.LOGGING], states[D.METRICS]) == (S.MODELED, S.MODELED)
    assert states[D.TRACING] is S.UNKNOWN  # not declared: unknown, never assumed
    assert T.TRACES_NOT_MODELED in types(result, "api")


def test_2_critical_service_with_missing_logging_configuration() -> None:
    result = run(
        [component("api", criticality="critical", metrics=("errors",)), OBS], [ship("api", "metrics")]
    )
    [missing] = [f for f in result.findings if f.type is T.LOGS_NOT_MODELED]
    assert (missing.basis, missing.dimension, missing.missing) == (
        FindingBasis.NOT_EVALUABLE,
        D.LOGGING,
        ("api.configuration.logs",),
    )
    assert T.LOGS_ABSENT not in types(result)  # unknown is not absent
    assert coverage(result, "api")[D.LOGGING] is S.UNKNOWN


def test_3_metrics_configured_without_a_modeled_collection_path() -> None:
    result = run([component("api", criticality="critical", metrics=("latency",)), OBS])
    [uncollected] = [f for f in result.findings if f.type is T.TELEMETRY_NOT_COLLECTED]
    assert (uncollected.dimension, uncollected.basis) == (D.METRICS, FindingBasis.NOT_EVALUABLE)
    assert coverage(result, "api")[D.METRICS] is S.PARTIAL
    assert result.summary()["collection"]["metrics"] == {"declared": 1, "collected": 0, "not_collected": 1}


def _path(propagation: tuple[bool | None, bool | None]) -> ObservabilityResult:
    first, second = (dict[str, Any]() if p is None else {"trace_propagation": p} for p in propagation)
    nodes = [
        component("gw", NodeKind.GATEWAY, criticality="critical", traces=True),
        component("api", criticality="critical", traces=True),
        component("orders", criticality="standard", traces=True),
        OBS,
    ]
    links = [
        link("gw", "api", **first),
        link("api", "orders", **second),
        *(ship(n, "traces") for n in ("gw", "api", "orders")),
    ]
    return run(nodes, links)


def test_4_multi_service_request_path_with_propagation_represented() -> None:
    result = _path((True, True))
    assert not {T.PROPAGATION_BROKEN, T.PROPAGATION_NOT_MODELED, T.TELEMETRY_NOT_COLLECTED} & types(result)
    assert all(coverage(result, n)[D.TRACING] is S.MODELED for n in ("gw", "api", "orders"))


def test_5_multi_service_path_with_incomplete_propagation_evidence() -> None:
    result = _path((True, None))
    [gap] = [f for f in result.findings if f.type is T.PROPAGATION_NOT_MODELED]
    assert (gap.connection_ids, gap.missing) == (
        ("api-orders",),
        ("api-orders.configuration.trace_propagation",),
    )
    assert T.PROPAGATION_BROKEN not in types(result)  # missing evidence is not a broken trace
    assert gap.basis is FindingBasis.NOT_EVALUABLE


def test_6_health_check_with_an_explicit_consumer() -> None:
    nodes = [
        component("lb", NodeKind.LOAD_BALANCER),
        component("api", criticality="critical", health_check=True),
    ]
    result = run(nodes, [link("lb", "api", health_check=True)])
    assert not {T.HEALTH_CHECK_UNCONSUMED, T.HEALTH_CHECK_ABSENT, T.HEALTH_CHECK_NOT_MODELED} & types(result)
    assert coverage(result, "api")[D.HEALTH_CHECKS] is S.MODELED


def test_7_health_check_with_no_modeled_consumer() -> None:
    nodes = [
        component("lb", NodeKind.LOAD_BALANCER),
        component("api", criticality="critical", health_check=True),
    ]
    result = run(nodes, [link("lb", "api")])
    [unconsumed] = [f for f in result.findings if f.type is T.HEALTH_CHECK_UNCONSUMED]
    assert (unconsumed.node_ids, unconsumed.dimension) == (("api",), D.HEALTH_CHECKS)
    assert coverage(result, "api")[D.HEALTH_CHECKS] is S.PARTIAL


def test_8_alert_rule_with_a_modeled_signal_source() -> None:
    api = component("api", criticality="critical", metrics=("errors",), alerts=("errors",))
    result = run([api, OBS], [ship("api", "metrics")])
    alerting = {T.ALERTS_ABSENT, T.ALERTS_NOT_MODELED, T.ALERT_WITHOUT_SIGNAL, T.ALERT_DELIVERY_NOT_MODELED}
    assert not alerting & types(result)
    assert coverage(result, "api")[D.ALERTING] is S.MODELED


def test_9_slo_objective_without_a_measurable_indicator() -> None:
    api = component("api", criticality="critical", metrics=("latency",), alerts=("latency",))
    result = run([api, OBS], [ship("api", "metrics")], requirements=[availability()])
    verdicts = {c.condition.value: c.verdict for c in result.checks}
    assert verdicts == {"objective_measurable": Verdict.VIOLATED, "objective_alerted": Verdict.VIOLATED}
    assert {f.type for f in result.findings if f.requirement_id} == {T.REQUIREMENT_VIOLATED}
    assert result.summary()["objectives"]["measurable"]["violated"] == 1


def test_10_an_incomplete_architecture_yields_partial_results_never_configured() -> None:
    nodes = [
        node("web", NodeKind.CLIENT),
        component("api", criticality="critical", logs=True),
        component("worker", NodeKind.WORKER),
        component("db", NodeKind.DATABASE),
        component("psp", NodeKind.EXTERNAL),
    ]
    links = [link("web", "api"), link("api", "worker", K.PUBLISH), link("api", "psp")]
    result = run(nodes, links, policy=ArchitecturePolicy(require_alerting_on_critical=True))
    assert result.status is ObservabilityStatus.PARTIAL
    assert {c.verdict for c in result.checks} == {Verdict.NOT_VERIFIABLE}  # never a pass
    assert T.CRITICALITY_NOT_MODELED in types(result)
    assert coverage(result, "psp") == dict.fromkeys(Dimension, S.UNSUPPORTED)
    summary = result.summary()
    assert summary["coverage"]["logging"]["modeled"] == 0  # logs declared, but no collection path
    assert summary["coverage"]["metrics"]["unknown"] == 3
    assert {f.basis for f in result.findings} <= {FindingBasis.NOT_EVALUABLE, FindingBasis.VIOLATION}


def _everything() -> tuple[list[Node], list[Connection]]:
    nodes = [
        node("web", NodeKind.CLIENT),
        component("gw", NodeKind.GATEWAY, criticality="critical", traces=True, logs=False),
        component(
            "api",
            criticality="critical",
            logs=True,
            structured_logs=False,
            metrics=("latency",),
            traces=True,
            trace_context="terminate",
            health_check=True,
            alerts=("errors", "health"),
            data_classification="restricted",
        ),
        component("orders", criticality="standard", traces=True, metrics=()),
        component("worker", NodeKind.WORKER),
        component("psp", NodeKind.EXTERNAL),
        component("obs", NodeKind.OBSERVABILITY, retention_seconds=3600),
    ]
    links = [
        link("web", "gw"),
        link("gw", "api", trace_propagation=False),
        link("api", "orders"),
        link("api", "psp"),
        link("orders", "worker", K.PUBLISH),
        ship("api", "logs"),
    ]
    return nodes, links


POLICY = ArchitecturePolicy(
    require_logs_on_critical=True,
    require_ownership=True,
    require_trace_propagation=True,
    min_telemetry_retention_seconds=86_400,
)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_whole_registry_is_deterministic_whatever_the_order(seed: int) -> None:
    nodes, links = _everything()
    first = run(nodes, links, policy=POLICY, requirements=[availability()])
    shuffled_nodes, shuffled_links = nodes[:], links[:]
    random.Random(seed).shuffle(shuffled_nodes)  # noqa: S311 -- a reproducible test order, not a secret
    random.Random(seed).shuffle(shuffled_links)  # noqa: S311 -- a reproducible test order, not a secret
    again = run(shuffled_nodes, shuffled_links, policy=POLICY, requirements=[availability()])
    assert first.to_dict() == again.to_dict()  # the normalized result
    assert first.fingerprint == again.fingerprint
    assert [f.id for f in first.findings] == [f.id for f in again.findings]  # ids and order
    assert first.summary() == again.summary()
    assert [f.evidence for f in first.findings] == [f.evidence for f in again.findings]
    assert {f.basis for f in first.findings} == set(FindingBasis)  # the four kinds, all present, kept apart


def test_the_architecture_is_never_modified() -> None:
    nodes, links = _everything()
    ir = ArchitectureIR("Fixture", nodes=tuple(nodes), connections=tuple(links))
    before = json.dumps(to_dict(ir), sort_keys=True)
    context = ObservabilityContext(
        ir, REVISION, ObservabilityAnalysisRequest(uuid.UUID(int=1), 1), policy=POLICY
    )
    analyze(context, default_registry())
    assert json.dumps(to_dict(ir), sort_keys=True) == before


def test_the_engine_reads_no_telemetry_and_stays_apart_from_the_platform() -> None:
    """Architecture-level analysis only: nothing under engines/observability or
    core/domain/observability opens a network connection, queries a telemetry backend, or reads
    ArchitectOS's users, sessions, API or database adapters (the service checks permissions through
    the shared access helper)."""
    forbidden = (
        "core.domain.identity",
        "apps",
        "persistence",
        "core.domain.organizations",
        "socket",
        "http",
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "prometheus_client",
        "opentelemetry",
        "subprocess",
    )
    allowed = {"core.domain.organizations.permissions"}  # the permission names the service checks
    for package in ("engines/observability", "core/domain/observability"):
        for path in sorted((ROOT / package).rglob("*.py")):
            tree = ast.parse(path.read_text())
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            leaks = {m for m in imported if m.startswith(forbidden)} - allowed
            assert not leaks, (path, leaks)


def test_the_cost_is_bounded_on_a_large_graph() -> None:
    """A long traced chain with fan-out, every dimension declared and one collector: the collection
    search runs once per signal (not per component), so the analysis stays well within a second."""
    size = 400
    nodes = [OBS] + [
        component(
            f"svc-{i:03}",
            criticality="critical" if i % 3 == 0 else "standard",
            logs=True,
            metrics=("errors", "latency"),
            traces=True,
            health_check=True,
            alerts=("errors",),
        )
        for i in range(size)
    ]
    links = [link(f"svc-{i:03}", f"svc-{i + 1:03}", trace_propagation=i % 2 == 0) for i in range(size - 1)]
    links += [
        link(f"svc-{i:03}", f"svc-{(i * 7) % size:03}") for i in range(0, size, 5) if (i * 7) % size != i
    ]
    links += [ship(f"svc-{i:03}", "logs", "metrics", "traces") for i in range(0, size, 2)]
    links += [
        link(f"svc-{i + 1:03}", f"svc-{i:03}", K.DEPENDENCY, telemetry=("logs",))
        for i in range(0, size - 1, 2)
    ]
    started = time.perf_counter()
    result = run(nodes, links, policy=POLICY, requirements=[availability()])
    elapsed = time.perf_counter() - started
    assert len(result.components) == size + 1
    assert elapsed < 5, elapsed  # measured well under a second; generous for slow CI machines
