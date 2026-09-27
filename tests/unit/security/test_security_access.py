"""Authentication and authorization analysis (Milestone 10, phase 4): modeled gaps apart from what is
not modeled, service identity, consistency across paths, and nothing read from secrets."""

import json
import uuid
from collections.abc import Sequence
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
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
from engines.security.authentication import Authentication
from engines.security.authorization import Authorization
from engines.security.context import SecurityContext
from engines.security.engine import Registry, analyze
from engines.security.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
ACCESS = Registry([Authentication(), Authorization()])


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def zoned(node_id: str, zone: str, **values: Any) -> Node:
    return node(node_id, parent_id=zone, configuration=Configuration(values))


def call(source: str, target: str, **values: Any) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol="https",
        configuration=Configuration(values),
    )


def run(
    nodes: Sequence[Node], links: Sequence[Connection] = (), registry: Registry = ACCESS, **request: Any
) -> SecurityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    context = SecurityContext(ir, REVISION, SecurityAnalysisRequest(uuid.UUID(int=1), 1, **request))
    return analyze(context, registry)


def of(result: SecurityResult, type_: FindingType) -> list[SecurityFinding]:
    return [f for f in result.findings if f.type is type_]


# --- components: authentication ------------------------------------------------------------------


def test_a_public_api_with_explicit_authentication_raises_nothing() -> None:
    result = run([component("api", exposure="public", authentication="oauth2", authorization="rbac")])
    assert result.findings == ()


def test_a_public_api_without_authentication_evidence_cannot_be_evaluated() -> None:
    [unknown] = run([component("api", exposure="public")]).findings
    assert (unknown.type, unknown.basis, unknown.severity) == (
        T.AUTHENTICATION_NOT_MODELED,
        FindingBasis.NOT_EVALUABLE,
        Severity.MEDIUM,
    )
    assert unknown.missing == ("api.configuration.authentication",)
    assert Evidence("api.configuration.exposure", "public") in unknown.evidence


@pytest.mark.parametrize(
    ("values", "severity"),
    [
        ({"exposure": "public"}, Severity.MEDIUM),
        ({"exposure": "public", "personal_data": True}, Severity.HIGH),
        ({"exposure": "public", "data_classification": "public"}, Severity.LOW),  # public content
        ({"exposure": "internal", "sensitive_operations": True}, Severity.HIGH),
        ({"exposure": "internal", "authorization": "rbac"}, Severity.MEDIUM),  # roles need an identity
    ],
)
def test_authentication_none_where_it_is_needed_is_a_modeled_gap(
    values: dict[str, ConfigValue], severity: Severity
) -> None:
    findings = run([node("api", configuration=Configuration({"authentication": "none"} | values))]).findings
    [gap] = [f for f in findings if f.type is T.MISSING_AUTHENTICATION]
    assert (gap.basis, gap.severity) == (FindingBasis.CONTROL_GAP, severity)
    assert Evidence("api.configuration.authentication", "none") in gap.evidence


def test_a_component_nothing_says_needs_authentication_is_not_flagged() -> None:
    assert run([component("worker", NodeKind.WORKER, exposure="private")]).findings == ()
    assert run([component("worker", NodeKind.WORKER)]).findings == ()  # absent is never "none"


# --- components: authorization -------------------------------------------------------------------


def test_a_sensitive_operation_without_authorization_evidence() -> None:
    payments: dict[str, Any] = {
        "exposure": "internal",
        "authentication": "mtls",
        "sensitive_operations": True,
    }
    [unknown] = of(run([component("payments", **payments)]), T.AUTHORIZATION_NOT_MODELED)
    assert (unknown.severity, unknown.missing) == (Severity.MEDIUM, ("payments.configuration.authorization",))
    [gap] = of(run([component("payments", **payments, authorization="none")]), T.MISSING_AUTHORIZATION)
    assert (gap.basis, gap.severity) == (FindingBasis.CONTROL_GAP, Severity.HIGH)
    assert run([component("payments", **payments, authorization="abac")]).findings == ()


def test_a_sensitive_resource_needs_authorization_too() -> None:
    restricted = component("db", NodeKind.DATABASE, data_classification="restricted", authorization="none")
    [gap] = of(run([restricted]), T.MISSING_AUTHORIZATION)
    assert gap.severity is Severity.MEDIUM
    [unknown] = of(run([component("db", NodeKind.DATABASE, personal_data=True)]), T.AUTHORIZATION_NOT_MODELED)
    assert unknown.severity is Severity.LOW
    assert run([component("db", NodeKind.DATABASE, data_classification="internal")]).findings == ()


def test_a_third_partys_authorization_is_not_ours_to_model() -> None:
    psp = component("psp", NodeKind.EXTERNAL, data_classification="restricted")
    assert not of(run([psp]), T.AUTHORIZATION_NOT_MODELED)


# --- service identity ----------------------------------------------------------------------------


def test_a_service_connection_declaring_no_identity_is_a_gap() -> None:
    ledger = component("ledger", sensitive_operations=True, authentication="mtls", authorization="rbac")
    nodes = [component("orders", authentication="mtls"), ledger]
    [gap] = of(run(nodes, [call("orders", "ledger", authentication="none")]), T.UNAUTHENTICATED_CONNECTION)
    assert (gap.severity, gap.connection_ids, gap.node_ids) == (
        Severity.HIGH,
        ("orders-ledger",),
        ("ledger", "orders"),
    )


def test_a_service_connection_without_identity_semantics() -> None:
    nodes = [component("orders"), component("ledger", authentication="mtls")]
    [unknown] = of(run(nodes, [call("orders", "ledger")]), T.AUTHENTICATION_NOT_MODELED)
    assert unknown.connection_ids == ("orders-ledger",)
    assert unknown.missing == ("orders-ledger.configuration.authentication",)
    plain = [component("orders"), component("cache", NodeKind.CACHE)]
    assert run(plain, [call("orders", "cache")]).findings == ()  # nothing says it matters: not flagged


def test_clients_are_judged_by_their_target_not_as_services() -> None:
    nodes = [node("web", NodeKind.CLIENT), component("api", exposure="public", authentication="oauth2")]
    assert run(nodes, [call("web", "api")]).findings == ()  # the api authenticates its callers
    contradicted = run(nodes, [call("web", "api", authentication="none")])
    assert not of(contradicted, T.UNAUTHENTICATED_CONNECTION)  # no service identity for a client
    [risk] = of(contradicted, T.INCONSISTENT_AUTHENTICATION)  # but the model contradicts itself
    assert risk.connection_ids == ("web-api",)


def test_a_crossing_already_reported_is_not_reported_again() -> None:
    zones = [
        node(
            z,
            NodeKind.BOUNDARY,
            configuration=Configuration({"boundary_type": "trust_zone", "trust_level": level}),
        )
        for z, level in (("dmz", "untrusted"), ("core", "internal"))
    ]
    nodes = [*zones, zoned("api", "dmz"), zoned("ledger", "core", authentication="mtls")]
    links = [call("api", "ledger", authentication="none", tls=True)]
    everything = run(nodes, links, registry=default_registry())
    assert of(everything, T.UNPROTECTED_BOUNDARY_CROSSING)
    assert not of(everything, T.UNAUTHENTICATED_CONNECTION)
    alone = run(nodes, links, analyzers=("authentication",), registry=default_registry())
    assert of(alone, T.UNAUTHENTICATED_CONNECTION)  # without the crossing analysis, it is reported here


# --- consistency ---------------------------------------------------------------------------------


def test_paths_that_authenticate_differently_are_a_potential_risk() -> None:
    nodes = [component("a"), component("b"), component("ledger", authentication="mtls")]
    links = [call("a", "ledger", authentication="mtls"), call("b", "ledger", authentication="none")]
    [risk] = of(run(nodes, links), T.INCONSISTENT_AUTHENTICATION)
    assert (risk.basis, risk.node_ids, risk.connection_ids) == (
        FindingBasis.POTENTIAL_RISK,
        ("ledger",),
        ("a-ledger", "b-ledger"),
    )
    assert "b-ledger declares none" in risk.explanation
    same = [call("a", "ledger", authentication="mtls"), call("b", "ledger", authentication="mtls")]
    assert not of(run(nodes, same), T.INCONSISTENT_AUTHENTICATION)


# --- secrets -------------------------------------------------------------------------------------


def test_no_secret_value_is_read_or_shown() -> None:
    api = node(
        "api",
        configuration=Configuration(
            {"exposure": "public", "authentication": "api_key"}, extra={"api_key": "sk_live_123"}
        ),
        metadata={"admin_password": "hunter2"},
    )
    shown = json.dumps(run([api], registry=default_registry()).to_dict())
    assert "sk_live_123" not in shown
    assert "hunter2" not in shown


def test_the_analysis_is_deterministic() -> None:
    ledger = component("ledger", authentication="mtls", sensitive_operations=True)
    nodes = [component("a"), component("b"), ledger]
    links = [call("a", "ledger", authentication="mtls"), call("b", "ledger", authentication="none")]
    first, again = run(nodes, links), run(list(reversed(nodes)), list(reversed(links)))
    assert first.to_dict() == again.to_dict()
