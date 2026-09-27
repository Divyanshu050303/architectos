"""Metrics coverage (Milestone 11, phase 4): modeled gaps apart from what is not modeled, missing
collection in the model apart from missing telemetry, and no metric value ever computed."""

import json
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence, FindingBasis
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.results import FindingType, ObservabilityResult
from core.domain.observability.values import Dimension
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.observability.context import ObservabilityContext
from engines.observability.engine import Registry, analyze
from engines.observability.metrics import Metrics
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
METRICS = Registry([Metrics()])
OBS = node("obs", NodeKind.OBSERVABILITY)


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def ships(source: str, target: str, *signals: str) -> Connection:
    configuration = Configuration({"telemetry": signals})
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol="https",
        configuration=configuration,
    )


def run(nodes: Sequence[Node], links: Sequence[Connection] = ()) -> ObservabilityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    return analyze(
        ObservabilityContext(ir, REVISION, ObservabilityAnalysisRequest(uuid.UUID(int=1), 1)), METRICS
    )


def test_a_critical_service_with_collected_metrics_raises_nothing() -> None:
    api = component("api", criticality="critical", metrics=("errors", "latency"))
    assert run([api, OBS], [ships("api", "obs", "metrics")]).findings == ()


def test_a_critical_service_without_metrics() -> None:
    [gap] = run([component("api", criticality="critical", metrics=()), OBS]).findings
    assert (gap.type, gap.basis, gap.severity, gap.dimension) == (
        T.METRICS_ABSENT,
        FindingBasis.CONTROL_GAP,
        Severity.HIGH,
        Dimension.METRICS,
    )
    [unknown] = run([component("api", criticality="critical"), OBS]).findings
    assert (unknown.type, unknown.basis, unknown.missing) == (
        T.METRICS_NOT_MODELED,
        FindingBasis.NOT_EVALUABLE,
        ("api.configuration.metrics",),
    )
    assert run([component("jobs", criticality="standard", metrics=()), OBS]).findings == ()


def test_metrics_configured_without_a_collection_path() -> None:
    api = component("api", criticality="critical", metrics=("latency",))
    [missing] = run([api, OBS], [ships("api", "obs", "logs")]).findings  # a logs path does not carry metrics
    assert (missing.type, missing.basis, missing.severity) == (
        T.TELEMETRY_NOT_COLLECTED,
        FindingBasis.NOT_EVALUABLE,
        Severity.MEDIUM,
    )
    assert (
        "not about what runs" in missing.explanation
    )  # missing collection in the model, not missing telemetry
    assert Evidence("api.configuration.metrics", "latency") in missing.evidence
    exporter = component("exporter", NodeKind.WORKER)
    via = [ships("api", "exporter", "metrics"), ships("exporter", "obs", "metrics")]
    assert run([api, exporter, OBS], via).findings == ()


def test_no_metric_value_is_ever_computed() -> None:
    api = component("api", criticality="critical", metrics=("latency", "errors", "throughput"))
    shown = json.dumps(run([api, OBS]).to_dict()).lower()
    for invented in ("p99", 'ms"', 'rate":', "requests_per_second", "error_rate"):
        assert invented not in shown


def test_proposed_metrics_make_a_candidate_and_no_secret_is_shown() -> None:
    proposal = Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal("0.5"))
    api = node(
        "api",
        configuration=Configuration(
            {"criticality": "critical", "metrics": ()}, extra={"scrape_token": "s-1"}
        ),
        field_provenance={"configuration.metrics": proposal},
    )
    result = run([api, OBS])
    [gap] = result.findings
    assert gap.certainty is Certainty.CANDIDATE
    assert "s-1" not in json.dumps(result.to_dict())


def test_the_analysis_is_deterministic() -> None:
    nodes = [component("a", criticality="critical"), component("b", metrics=("errors",)), OBS]
    assert run(nodes).to_dict() == run(list(reversed(nodes))).to_dict()
