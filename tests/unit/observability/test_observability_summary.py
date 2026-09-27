"""The coverage summary and finding prioritization (Milestone 11, phase 8): counts only, every
aggregation rule tested, reproducible from the detailed result, unsupported components counted apart,
severity kept apart from coverage — no percentage, no score."""

import uuid
from collections import Counter
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.capacity.results import Certainty
from core.domain.engine_results import FindingBasis, ModelSet
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.results import (
    PRIORITY_SHOWN,
    ComponentResult,
    FindingType,
    ObservabilityFinding,
    ObservabilityResult,
)
from core.domain.observability.values import CoverageState, Dimension
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity, Verdict
from engines.observability.context import ObservabilityContext
from engines.observability.engine import analyze
from engines.observability.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node
from tests.unit.observability.test_observability_policy_requirements import availability

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
S = CoverageState
T = FindingType
EMPTY_SET = ModelSet.of([("logs", 1)])


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def shop() -> ObservabilityResult:
    """A critical API whose logs are collected and metrics are not, a standard worker declaring
    nothing, a third party, and an observability backend."""
    nodes = [
        component("api", criticality="critical", logs=True, metrics=("latency",), traces=False),
        component("worker", NodeKind.WORKER, criticality="standard"),
        component("stripe", NodeKind.EXTERNAL),
        component("obs", NodeKind.OBSERVABILITY, alert_delivery="email"),
    ]
    links = [
        connection(
            "api-obs",
            "api",
            "obs",
            kind=ConnectionKind.DEPENDENCY,
            protocol=None,
            configuration=Configuration({"telemetry": ("logs",)}),
        ),
        connection("api-stripe", "api", "stripe", protocol="https"),
    ]
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    context = ObservabilityContext(
        ir,
        REVISION,
        ObservabilityAnalysisRequest(uuid.UUID(int=1), 1),
        policy=ArchitecturePolicy(require_ownership=True),
        requirements=(availability(),),
    )
    return analyze(context, default_registry())


def test_the_scope_counts_unsupported_components_apart() -> None:
    summary = shop().summary()
    assert summary["scope"] == {"components": 4, "eligible": 3, "unsupported": 1}
    assert summary["criticality"] == {"critical": 1, "standard": 1, "not_modeled": 2}
    for dimension in Dimension:  # the third party is its own state in every dimension
        assert summary["coverage"][dimension.value]["unsupported"] == 1


def test_coverage_counts_each_component_once_per_dimension() -> None:
    result = shop()
    coverage = result.summary()["coverage"]
    for dimension in Dimension:
        assert set(coverage[dimension.value]) == {s.value for s in CoverageState}  # zeros included
        assert sum(coverage[dimension.value].values()) == len(result.components)
    assert coverage["logging"] == {"modeled": 1, "partial": 0, "absent": 0, "unknown": 2, "unsupported": 1}
    critical = result.summary()["critical_coverage"]
    assert (
        critical["logging"]["modeled"],
        critical["metrics"]["partial"],
        critical["tracing"]["absent"],
    ) == (
        1,
        1,
        1,
    )


def test_collection_paths_per_signal() -> None:
    assert shop().summary()["collection"] == {
        "logs": {"declared": 1, "collected": 1, "not_collected": 0},
        "metrics": {"declared": 1, "collected": 0, "not_collected": 1},
        "traces": {"declared": 0, "collected": 0, "not_collected": 0},
    }


def test_objective_measurability_counts_verdicts_never_attainment() -> None:
    objectives = shop().summary()["objectives"]
    assert objectives["measurable"]["violated"] == 1  # latency does not measure availability
    assert objectives["alerted"]["not_verifiable"] == 1
    assert objectives["unsupported_requirements"] == 0
    assert set(objectives) == {"measurable", "alerted", "unsupported_requirements"}


def test_the_summary_is_reproducible_from_the_detailed_result() -> None:
    result = shop()
    summary = result.summary()
    assert ObservabilityResult.from_dict(result.to_dict()).summary() == summary
    assert sum(summary["findings"].values()) == len(result.findings)
    severities = Counter(f.severity for f in result.findings)
    assert summary["findings"] == {s.value: severities.get(s, 0) for s in Severity}
    assert summary["checks"]["violated"] == sum(1 for c in result.checks if c.verdict is Verdict.VIOLATED)
    for signal, dimension in (("logs", "logging"), ("metrics", "metrics"), ("traces", "tracing")):
        states = Counter(c.coverage[Dimension(dimension)] for c in result.components)
        assert summary["collection"][signal]["collected"] == states[S.MODELED]
        assert summary["collection"][signal]["not_collected"] == states[S.PARTIAL]


def _leaves(document: object) -> list[object]:
    """Every key and value of a JSON-like document."""
    if isinstance(document, dict):
        return [*document, *(v for x in document.values() for v in _leaves(x))]
    if isinstance(document, list):
        return [v for x in document for v in _leaves(x)]
    return [document]


def test_no_percentage_score_or_maturity() -> None:
    leaves = _leaves(shop().summary())
    assert not any(isinstance(v, float) for v in leaves)
    text = " ".join(str(v).lower() for v in leaves)
    assert not any(word in text for word in ("percent", "score", "maturity", "ratio"))


def test_severity_is_kept_apart_from_coverage() -> None:
    summary = shop().summary()
    for key in ("coverage", "critical_coverage"):
        assert all(set(states) == {s.value for s in CoverageState} for states in summary[key].values())


def _finding(
    type_: FindingType, severity: Severity, node_id: str = "api", **extra: Any
) -> ObservabilityFinding:
    return ObservabilityFinding(
        type=type_,
        severity=severity,
        certainty=Certainty.MODELED,
        title="t",
        explanation="e",
        recommendation="r",
        node_ids=(node_id,),
        **extra,
    )


def test_priority_order_is_severity_then_basis_then_type() -> None:
    findings = [
        _finding(T.LOGS_NOT_MODELED, Severity.HIGH),  # not evaluable
        _finding(T.LOGS_ABSENT, Severity.HIGH),  # control gap
        _finding(
            T.POLICY_VIOLATED, Severity.HIGH, policy_rule="require_logs_on_critical", check_key="policy.x"
        ),
        _finding(T.SENSITIVE_DATA_IN_LOGS, Severity.CRITICAL),  # a potential risk, but more severe
        _finding(T.METRICS_NOT_MODELED, Severity.LOW),
    ]
    result = ObservabilityResult(EMPTY_SET, "f" * 64, findings=tuple(reversed(findings)))
    assert [(f.severity, f.basis) for f in result.findings] == [
        (Severity.CRITICAL, FindingBasis.POTENTIAL_RISK),
        (Severity.HIGH, FindingBasis.VIOLATION),
        (Severity.HIGH, FindingBasis.CONTROL_GAP),
        (Severity.HIGH, FindingBasis.NOT_EVALUABLE),
        (Severity.LOW, FindingBasis.NOT_EVALUABLE),
    ]
    assert result.summary()["priorities"] == [f.id for f in result.findings]


def test_priorities_are_bounded_and_a_prefix_of_the_findings() -> None:
    findings = tuple(_finding(T.LOGS_NOT_MODELED, Severity.MEDIUM, f"svc-{i}") for i in range(15))
    components = tuple(
        ComponentResult(f"svc-{i}", None, dict.fromkeys(Dimension, S.UNKNOWN)) for i in range(15)
    )
    result = ObservabilityResult(EMPTY_SET, "f" * 64, components, findings)
    priorities = result.summary()["priorities"]
    assert len(priorities) == PRIORITY_SHOWN
    assert priorities == [f.id for f in result.findings[:PRIORITY_SHOWN]]


def test_an_empty_scope_summarizes_to_zeros() -> None:
    summary = ObservabilityResult(EMPTY_SET, "f" * 64).summary()
    assert summary["scope"] == {"components": 0, "eligible": 0, "unsupported": 0}
    assert summary["priorities"] == []
    assert all(v == 0 for counts in summary["collection"].values() for v in counts.values())
