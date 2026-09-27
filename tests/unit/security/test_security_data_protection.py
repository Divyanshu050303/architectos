"""Encryption and data-protection analysis (Milestone 10, phase 5): sensitive data at rest and in
transit, unknown encryption kept unknown, and unmodeled sensitivity."""

import json
import uuid
from collections.abc import Sequence
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.engine_results import Evidence
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.results import FindingBasis, FindingType, SecurityFinding, SecurityResult
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.security.context import SecurityContext
from engines.security.encryption import Encryption
from engines.security.engine import Registry, analyze
from engines.security.pii import DataProtection
from engines.security.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
DATA = Registry([Encryption(), DataProtection()])


def store(node_id: str = "db", kind: NodeKind = NodeKind.DATABASE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def worker(node_id: str, **values: Any) -> Node:
    return node(node_id, NodeKind.WORKER, configuration=Configuration(values))


def access(
    source: str = "api",
    target: str = "db",
    kind: ConnectionKind = ConnectionKind.DATA_ACCESS,
    protocol: str = "postgresql",
    **values: Any,
) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=kind,
        protocol=protocol,
        configuration=Configuration(values),
    )


def request(source: str, target: str, protocol: str = "http", **values: Any) -> Connection:
    return access(source, target, ConnectionKind.REQUEST, protocol, **values)


def run(nodes: Sequence[Node], links: Sequence[Connection] = (), registry: Registry = DATA) -> SecurityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    return analyze(SecurityContext(ir, REVISION, SecurityAnalysisRequest(uuid.UUID(int=1), 1)), registry)


def of(result: SecurityResult, type_: FindingType) -> list[SecurityFinding]:
    return [f for f in result.findings if f.type is type_]


# --- at rest -------------------------------------------------------------------------------------


def test_a_sensitive_datastore_with_explicit_encryption_raises_nothing() -> None:
    assert run([store(data_classification="restricted", encryption_at_rest=True)]).findings == ()


def test_a_sensitive_datastore_with_unknown_encryption_stays_unknown() -> None:
    [unknown] = run([store(personal_data=True)]).findings
    assert (unknown.type, unknown.basis, unknown.severity) == (
        T.ENCRYPTION_NOT_MODELED,
        FindingBasis.NOT_EVALUABLE,
        Severity.MEDIUM,
    )
    assert unknown.missing == ("db.configuration.encryption_at_rest",)
    explicitly_unknown = node(
        "db",
        NodeKind.DATABASE,
        configuration=Configuration({"personal_data": True}, unknown={"encryption_at_rest"}),
    )
    [same] = run([explicitly_unknown]).findings
    assert same.type is T.ENCRYPTION_NOT_MODELED
    assert Evidence("db.configuration.encryption_at_rest", "unknown") in same.evidence


@pytest.mark.parametrize(
    ("values", "severity"),
    [
        ({"data_classification": "restricted"}, Severity.HIGH),
        ({"personal_data": True}, Severity.HIGH),
        ({"data_classification": "confidential"}, Severity.MEDIUM),
    ],
)
def test_sensitive_data_stored_unencrypted_is_a_modeled_gap(
    values: dict[str, ConfigValue], severity: Severity
) -> None:
    db = node("db", NodeKind.DATABASE, configuration=Configuration({"encryption_at_rest": False} | values))
    [gap] = run([db]).findings
    assert (gap.type, gap.basis, gap.severity) == (
        T.UNENCRYPTED_DATA_AT_REST,
        FindingBasis.CONTROL_GAP,
        severity,
    )
    assert Evidence("db.configuration.encryption_at_rest", "false") in gap.evidence
    assert any("algorithm" in a for a in gap.assumptions)  # nothing assumed about how it would encrypt


def test_a_technology_is_never_taken_as_encrypting() -> None:
    rds = node(
        "db",
        NodeKind.DATABASE,
        technology=Technology("aws-rds"),
        configuration=Configuration({"personal_data": True}),
    )
    [unknown] = run([rds]).findings
    assert unknown.type is T.ENCRYPTION_NOT_MODELED


def test_non_sensitive_stores_are_not_flagged_and_unclassified_ones_are_reported_as_such() -> None:
    assert run([store(data_classification="internal", encryption_at_rest=False)]).findings == ()
    [unclassified] = run([store(encryption_at_rest=False)]).findings
    assert (unclassified.type, unclassified.basis) == (
        T.DATA_CLASSIFICATION_NOT_MODELED,
        FindingBasis.NOT_EVALUABLE,
    )
    assert unclassified.missing == ("db.configuration.data_classification",)
    assert run([store("q", NodeKind.QUEUE, data_classification="public")]).findings == ()
    assert run([node("api")]).findings == ()  # a service is not a data store


# --- in transit ----------------------------------------------------------------------------------


def test_sensitive_data_in_transit() -> None:
    nodes = [node("api"), store(data_classification="restricted", encryption_at_rest=True)]
    [gap] = run(nodes, [access(tls=False)]).findings
    assert (gap.type, gap.severity, gap.connection_ids) == (
        T.UNENCRYPTED_DATA_IN_TRANSIT,
        Severity.HIGH,
        ("api-db",),
    )
    assert Evidence("api-db.protocol", "postgresql") in gap.evidence
    assert Evidence("db.configuration.data_classification", "restricted") in gap.evidence
    [unknown] = run(nodes, [access()]).findings
    assert (unknown.type, unknown.missing) == (T.ENCRYPTION_NOT_MODELED, ("api-db.configuration.tls",))
    assert run(nodes, [access(tls=True)]).findings == ()
    assert run(nodes, [access(protocol="rediss")]).findings == ()  # encrypted by definition


def test_what_a_flow_declares_about_itself_comes_first() -> None:
    [gap] = run(
        [node("api"), node("reports")], [request("api", "reports", tls=False, personal_data=True)]
    ).findings
    assert gap.type is T.UNENCRYPTED_DATA_IN_TRANSIT  # the flow says it carries personal data
    restricted = [node("api"), store(data_classification="restricted", encryption_at_rest=True)]
    public = access(tls=False, data_classification="public")
    assert run(restricted, [public]).findings == ()  # the flow declares it carries only public data


def test_a_plain_request_to_a_sensitive_service_is_not_assumed_to_carry_its_data() -> None:
    nodes = [node("web", NodeKind.CLIENT), node("api", configuration=Configuration({"personal_data": True}))]
    assert run(nodes, [request("web", "api", tls=False)]).findings == ()


def test_crossings_already_reported_are_not_repeated() -> None:
    zone = Configuration({"boundary_type": "trust_zone", "trust_level": "internal"})
    db = Configuration({"personal_data": True, "encryption_at_rest": True})
    nodes = [
        node("app", NodeKind.BOUNDARY, configuration=zone),
        node("data", NodeKind.BOUNDARY, configuration=zone),
        node("api", parent_id="app"),
        node("db", NodeKind.DATABASE, parent_id="data", configuration=db),
    ]
    result = run(nodes, [access(tls=False)], registry=default_registry())
    assert of(result, T.UNPROTECTED_BOUNDARY_CROSSING)
    assert not of(result, T.UNENCRYPTED_DATA_IN_TRANSIT)


# --- unmodeled sensitivity of crossing flows ------------------------------------------------------


def test_a_crossing_flow_of_unknown_sensitivity() -> None:
    nodes = [
        node("api", configuration=Configuration({"exposure": "public"})),
        worker("jobs", exposure="private"),
    ]
    result = run(nodes, [request("api", "jobs", "https")])
    [unknown] = of(result, T.DATA_CLASSIFICATION_NOT_MODELED)
    assert unknown.missing == ("api-jobs.configuration.data_classification",)
    classified = [
        node("api", configuration=Configuration({"exposure": "public", "data_classification": "public"})),
        worker("jobs", exposure="private", data_classification="internal"),
    ]
    assert run(classified, [request("api", "jobs", "https")]).findings == ()


def test_the_analysis_shows_no_secret_and_is_deterministic() -> None:
    db = node(
        "db",
        NodeKind.DATABASE,
        configuration=Configuration({"personal_data": True}, extra={"master_password": "hunter2"}),
    )
    nodes = [node("api"), db]
    first = run(nodes, [access()], registry=default_registry())
    assert "hunter2" not in json.dumps(first.to_dict())
    assert first.to_dict() == run(list(reversed(nodes)), [access()], registry=default_registry()).to_dict()
