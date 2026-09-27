"""Logging coverage (Milestone 11, phase 3): modeled gaps apart from what is not modeled, collection
paths, sensitive data, criticality — and nothing inferred."""

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
from core.domain.observability.results import FindingType, ObservabilityFinding, ObservabilityResult
from core.domain.observability.values import Dimension
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.observability.context import ObservabilityContext
from engines.observability.criticality import Criticality
from engines.observability.engine import Registry, analyze
from engines.observability.logs import Logs
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
LOGGING = Registry([Criticality(), Logs()])


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def ships(source: str, target: str, *signals: str) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol="https",
        configuration=Configuration({"telemetry": signals}),
    )


OBS = component("obs", NodeKind.OBSERVABILITY, criticality="standard")


def run(nodes: Sequence[Node], links: Sequence[Connection] = ()) -> ObservabilityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    return analyze(
        ObservabilityContext(ir, REVISION, ObservabilityAnalysisRequest(uuid.UUID(int=1), 1)), LOGGING
    )


def of(result: ObservabilityResult, type_: FindingType) -> list[ObservabilityFinding]:
    return [f for f in result.findings if f.type is type_]


def test_a_critical_service_with_modeled_collected_logs_raises_nothing() -> None:
    api = component("api", criticality="critical", logs=True, metrics=("errors",))
    assert run([api, OBS], [ships("api", "obs", "logs", "metrics")]).findings == ()


def test_a_critical_service_with_missing_logging_configuration() -> None:
    [unknown] = run([component("api", criticality="critical"), OBS]).findings
    assert (unknown.type, unknown.basis, unknown.severity) == (
        T.LOGS_NOT_MODELED,
        FindingBasis.NOT_EVALUABLE,
        Severity.MEDIUM,
    )
    assert (unknown.missing, unknown.dimension) == (("api.configuration.logs",), Dimension.LOGGING)
    [gap] = run([component("api", criticality="critical", logs=False), OBS]).findings
    assert (gap.type, gap.basis, gap.severity) == (T.LOGS_ABSENT, FindingBasis.CONTROL_GAP, Severity.HIGH)
    assert Evidence("api.configuration.logs", "false") in gap.evidence


def test_a_standard_or_unclassified_component_without_logging_is_not_a_finding() -> None:
    assert run([component("jobs", criticality="standard"), OBS]).findings == ()
    assert run([component("jobs", criticality="standard", logs=False), OBS]).findings == ()


def test_declared_logs_without_a_collection_path() -> None:
    [critical] = run([component("api", criticality="critical", logs=True), OBS]).findings
    assert (critical.type, critical.severity, critical.dimension) == (
        T.TELEMETRY_NOT_COLLECTED,
        Severity.MEDIUM,
        Dimension.LOGGING,
    )
    [standard] = run(
        [component("api", criticality="standard", logs=True), OBS], [ships("api", "obs", "metrics")]
    ).findings
    assert standard.severity is Severity.LOW  # a metrics path does not carry logs
    collector = component("agent", NodeKind.WORKER, criticality="standard")
    via = [ships("api", "agent", "logs"), ships("agent", "obs", "logs")]
    assert run([component("api", criticality="standard", logs=True), collector, OBS], via).findings == ()


def test_sensitive_data_in_logs_is_a_potential_risk() -> None:
    api = component("api", criticality="standard", logs=True, personal_data=True)
    [risk] = run([api, OBS], [ships("api", "obs", "logs")]).findings
    assert (risk.type, risk.basis) == (T.SENSITIVE_DATA_IN_LOGS, FindingBasis.POTENTIAL_RISK)
    assert Evidence("api.configuration.personal_data", "true") in risk.evidence
    assert "security engine" in risk.explanation


def test_undeclared_criticality_is_listed_once_and_never_assumed() -> None:
    result = run([component("api", logs=False), component("db", NodeKind.DATABASE), OBS])
    [unknown] = of(result, T.CRITICALITY_NOT_MODELED)
    assert unknown.node_ids == ("api", "db")
    assert not of(result, T.LOGS_ABSENT)  # not known to be critical: no critical-component finding


def test_third_parties_are_not_ours_to_log() -> None:
    assert run([component("psp", NodeKind.EXTERNAL, criticality="critical"), OBS]).findings == ()


def test_proposed_facts_make_a_candidate_and_no_secret_is_shown() -> None:
    proposal = Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal("0.5"))
    api = node(
        "api",
        configuration=Configuration(
            {"criticality": "critical", "logs": False}, extra={"log_api_key": "k-123"}
        ),
        field_provenance={"configuration.logs": proposal},
    )
    result = run([api, OBS])
    [gap] = of(result, T.LOGS_ABSENT)
    assert gap.certainty is Certainty.CANDIDATE
    assert "k-123" not in json.dumps(result.to_dict())


def test_the_analysis_is_deterministic() -> None:
    nodes = [component("a", criticality="critical"), component("b", logs=True, personal_data=True), OBS]
    assert run(nodes).to_dict() == run(list(reversed(nodes))).to_dict()
