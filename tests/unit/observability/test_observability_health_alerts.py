"""Health checks and alerting paths (Milestone 11, phase 6): what the model says consumes a health
check, what an alert rule watches and how it leaves — never a threshold, never a claim it fires."""

import uuid
from collections.abc import Sequence
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.engine_results import Evidence, FindingBasis
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.results import FindingType, ObservabilityFinding, ObservabilityResult
from core.domain.observability.values import CoverageState, Dimension
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.observability.alerts import Alerts
from engines.observability.context import ObservabilityContext
from engines.observability.engine import Registry, analyze
from engines.observability.health import HealthChecks
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
REGISTRY = Registry([HealthChecks(), Alerts()])


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def link(source: str, target: str, **values: Any) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol="https",
        configuration=Configuration(values),
    )


def run(nodes: Sequence[Node], links: Sequence[Connection] = ()) -> ObservabilityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    return analyze(
        ObservabilityContext(ir, REVISION, ObservabilityAnalysisRequest(uuid.UUID(int=1), 1)), REGISTRY
    )


def of(result: ObservabilityResult, type_: FindingType) -> list[ObservabilityFinding]:
    return [f for f in result.findings if f.type is type_]


LB = component("lb", NodeKind.LOAD_BALANCER)


# --- health checks -------------------------------------------------------------------------------


def test_a_health_check_with_an_explicit_consumer_raises_nothing() -> None:
    api = component("api", criticality="critical", health_check=True, alerts=("health",))
    obs = component("obs", NodeKind.OBSERVABILITY, alert_delivery="paging")
    links = [link("lb", "api", health_check=True), link("api", "obs", telemetry=("metrics",))]
    assert of(run([LB, api, obs], links), T.HEALTH_CHECK_UNCONSUMED) == []
    assert run([LB, api, obs], links).findings == ()


def test_a_health_check_with_no_modeled_consumer() -> None:
    result = run([LB, component("api", criticality="critical", health_check=True)], [link("lb", "api")])
    [unconsumed] = of(result, T.HEALTH_CHECK_UNCONSUMED)
    assert (unconsumed.type, unconsumed.basis, unconsumed.severity, unconsumed.dimension) == (
        T.HEALTH_CHECK_UNCONSUMED,
        FindingBasis.NOT_EVALUABLE,
        Severity.MEDIUM,
        Dimension.HEALTH_CHECKS,
    )


def test_critical_components_without_a_health_check() -> None:
    [gap] = of(run([component("api", criticality="critical", health_check=False)]), T.HEALTH_CHECK_ABSENT)
    assert (gap.type, gap.basis, gap.severity) == (
        T.HEALTH_CHECK_ABSENT,
        FindingBasis.CONTROL_GAP,
        Severity.HIGH,
    )
    [unknown] = of(run([component("api", criticality="critical")]), T.HEALTH_CHECK_NOT_MODELED)
    assert unknown.missing == ("api.configuration.health_check",)
    assert run([component("jobs", criticality="standard", health_check=False)]).findings == ()


# --- alerting ------------------------------------------------------------------------------------


def monitored(delivery: str | None = "paging", **values: Any) -> tuple[list[Node], list[Connection]]:
    api = component("api", criticality="critical", health_check=True, metrics=("errors",), **values)
    obs_values = {} if delivery is None else {"alert_delivery": delivery}
    obs = component("obs", NodeKind.OBSERVABILITY, **obs_values)
    return [LB, api, obs], [link("lb", "api", health_check=True), link("api", "obs", telemetry=("metrics",))]


def test_an_alert_rule_with_a_modeled_signal_source_and_delivery_raises_nothing() -> None:
    nodes, links = monitored(alerts=("errors",))
    assert run(nodes, links).findings == ()


def test_critical_components_without_alert_rules() -> None:
    nodes, links = monitored(alerts=())
    [gap] = run(nodes, links).findings
    assert (gap.type, gap.basis, gap.severity) == (T.ALERTS_ABSENT, FindingBasis.CONTROL_GAP, Severity.HIGH)
    nodes, links = monitored()
    [unknown] = run(nodes, links).findings
    assert (unknown.type, unknown.missing) == (T.ALERTS_NOT_MODELED, ("api.configuration.alerts",))


def test_an_alert_rule_on_a_signal_that_is_not_emitted() -> None:
    nodes, links = monitored(alerts=("errors", "latency"))  # latency is not among its metrics
    [unsourced] = run(nodes, links).findings
    assert (unsourced.type, unsourced.basis) == (T.ALERT_WITHOUT_SIGNAL, FindingBasis.CONTROL_GAP)
    assert "latency" in unsourced.title
    assert Evidence("api.configuration.metrics", "errors") in unsourced.evidence
    api = component("api", criticality="standard", alerts=("logs",))  # logs not declared: no gap claimed
    assert not of(run([api]), T.ALERT_WITHOUT_SIGNAL)


def test_alerts_without_a_modeled_delivery_path() -> None:
    for delivery in (None, "none"):
        nodes, links = monitored(delivery, alerts=("errors",))
        [undelivered] = run(nodes, links).findings
        assert (undelivered.type, undelivered.basis) == (
            T.ALERT_DELIVERY_NOT_MODELED,
            FindingBasis.NOT_EVALUABLE,
        )
        assert undelivered.node_ids == ("api", "obs")
        assert undelivered.missing == ("obs.configuration.alert_delivery",)
    uncollected = component("api", criticality="standard", metrics=("errors",), alerts=("errors",))
    [lost] = run([uncollected]).findings
    assert lost.missing == ("api.collection",)


def test_every_reached_backend_counts_for_delivery() -> None:
    """Regression (phase 10 review): a node whose metrics reach two backends, only the second of which
    delivers alerts, has a modeled delivery path — whichever backend a path search finds first — and
    so does one whose collector forwards to a delivering backend."""
    api = component("api", criticality="critical", metrics=("errors",), alerts=("errors",))
    quiet = component("obs-a", NodeKind.OBSERVABILITY)
    paging = component("obs-b", NodeKind.OBSERVABILITY, alert_delivery="paging")
    both = [link("api", "obs-a", telemetry=("metrics",)), link("api", "obs-b", telemetry=("metrics",))]
    result = run([api, quiet, paging], both)
    assert not of(result, T.ALERT_DELIVERY_NOT_MODELED)
    [components] = [c for c in result.components if c.node_id == "api"]
    assert components.coverage[Dimension.ALERTING] is CoverageState.MODELED
    forwarded = [link("api", "obs-a", telemetry=("metrics",)), link("obs-a", "obs-b", telemetry=("metrics",))]
    assert not of(run([api, quiet, paging], forwarded), T.ALERT_DELIVERY_NOT_MODELED)
    [undelivered] = of(
        run([api, quiet, component("obs-b", NodeKind.OBSERVABILITY)], both), T.ALERT_DELIVERY_NOT_MODELED
    )
    assert undelivered.missing == ("obs-a.configuration.alert_delivery", "obs-b.configuration.alert_delivery")


def test_no_threshold_or_firing_is_claimed_and_the_analysis_is_deterministic() -> None:
    nodes, links = monitored(delivery=None, alerts=("errors", "latency"))
    first, again = run(nodes, links), run(list(reversed(nodes)), list(reversed(links)))
    assert first.to_dict() == again.to_dict()
    text = str(first.to_dict()).lower()
    for claim in ('threshold":', "fired", "was delivered", "acknowledged"):
        assert claim not in text
