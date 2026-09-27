"""Secrets and exposure analysis (Milestone 10, phase 6): secret sources, secret-looking settings
reported by name only, public management interfaces, and sensitive components reachable from a
declared public entry point by an exact path."""

import dataclasses
import json
import uuid
from collections.abc import Sequence
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.engine_results import Evidence
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.results import FindingBasis, FindingType, SecurityFinding, SecurityResult
from core.domain.security.values import REDACTED
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.security.context import SecurityContext
from engines.security.engine import Registry, analyze
from engines.security.exposure import Exposure
from engines.security.registry import default_registry
from engines.security.secrets import Secrets, secret_settings
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)
SECRETS = Registry([Secrets()])
EXPOSURE = Registry([Exposure()])


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: Any) -> Node:
    return node(node_id, kind, configuration=Configuration(values))


def call(
    source: str, target: str, kind: ConnectionKind = ConnectionKind.REQUEST, **fields: Any
) -> Connection:
    return connection(f"{source}-{target}", source, target, kind=kind, protocol="https", **fields)


def run(
    nodes: Sequence[Node], links: Sequence[Connection] = (), registry: Registry = SECRETS
) -> SecurityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    return analyze(SecurityContext(ir, REVISION, SecurityAnalysisRequest(uuid.UUID(int=1), 1)), registry)


def of(result: SecurityResult, type_: FindingType) -> list[SecurityFinding]:
    return [f for f in result.findings if f.type is type_]


# --- secret sources ------------------------------------------------------------------------------


def test_a_secret_manager_reference_raises_nothing() -> None:
    assert run([component("api", secrets_required=True, secret_source="secret_manager")]).findings == ()


def test_a_declared_hardcoded_secret_is_a_modeled_gap() -> None:
    [gap] = run([component("api", secrets_required=True, secret_source="hardcoded")]).findings
    assert (gap.type, gap.basis, gap.severity) == (
        T.HARDCODED_SECRET,
        FindingBasis.CONTROL_GAP,
        Severity.HIGH,
    )
    assert Evidence("api.configuration.secret_source", "hardcoded") in gap.evidence  # a choice, not a secret


def test_a_component_needing_secrets_from_nowhere_cannot_be_evaluated() -> None:
    [unknown] = run([component("api", secrets_required=True)]).findings
    assert (unknown.type, unknown.missing) == (
        T.SECRET_SOURCE_NOT_MODELED,
        ("api.configuration.secret_source",),
    )
    assert run([component("api")]).findings == ()  # nothing says it needs any


# --- secret-looking settings ---------------------------------------------------------------------


def test_a_secret_looking_setting_is_reported_by_name_and_never_shown() -> None:
    api = node(
        "api",
        configuration=Configuration(
            {"authorization": "rbac", "secret_source": "environment"},
            extra={
                "db_password": "hunter2",
                "pool": ({"host": "db", "api_key": "sk_live_123"},),
                "password_auth": True,  # a boolean is not a secret
                "client_secret": "",  # nor is an empty value
                "hostname": "db.internal",
            },
        ),
        metadata={"deploy_token": "ghp_abc"},
    )
    [risk] = run([api]).findings
    assert (risk.type, risk.basis) == (T.SECRET_IN_CONFIGURATION, FindingBasis.POTENTIAL_RISK)
    assert [e.label for e in risk.evidence] == [
        "api.configuration.extra.db_password",
        "api.configuration.extra.pool[0].api_key",
        "api.metadata.deploy_token",
    ]
    assert {e.value for e in risk.evidence} == {REDACTED}
    shown = json.dumps(run([api], registry=default_registry()).to_dict())
    for secret in ("hunter2", "sk_live_123", "ghp_abc"):
        assert secret not in shown


def test_settings_on_connections_are_examined_too() -> None:
    link = call("api", "db", configuration=Configuration(extra={"connection_password": "x"}))
    [risk] = run([component("api"), component("db", NodeKind.DATABASE)], [link]).findings
    assert (risk.connection_ids, risk.node_ids) == (("api-db",), ())


def test_the_search_only_tests_values() -> None:
    assert list(secret_settings("a", {"b": {"token": "t", "tokens_enabled": False}})) == ["a.b.token"]
    assert list(secret_settings("a", {"secret": None})) == []


# --- exposure ------------------------------------------------------------------------------------


def test_a_publicly_exposed_management_interface() -> None:
    admin = component("admin", exposure="public", management_interface=True)
    [gap] = run([admin], registry=EXPOSURE).findings
    assert (gap.type, gap.basis, gap.severity) == (
        T.PUBLIC_MANAGEMENT_INTERFACE,
        FindingBasis.CONTROL_GAP,
        Severity.HIGH,
    )
    [unknown] = run([component("admin", management_interface=True)], registry=EXPOSURE).findings
    assert (unknown.type, unknown.severity) == (T.EXPOSURE_NOT_MODELED, Severity.MEDIUM)
    internal = component("admin", exposure="internal", management_interface=True)
    assert run([internal], registry=EXPOSURE).findings == ()


def test_a_sensitive_component_reachable_from_a_public_entry_names_the_path() -> None:
    nodes = [
        component("api", exposure="public"),
        component("orders", exposure="internal"),
        component("db", NodeKind.DATABASE, exposure="private", data_classification="restricted"),
    ]
    links = [call("api", "orders"), call("orders", "db", ConnectionKind.DATA_ACCESS)]
    [risk] = run(nodes, links, registry=EXPOSURE).findings
    assert (risk.type, risk.basis) == (
        T.SENSITIVE_COMPONENT_REACHABLE_FROM_PUBLIC,
        FindingBasis.POTENTIAL_RISK,
    )
    assert (risk.node_ids, risk.connection_ids) == (("api", "db", "orders"), ("api-orders", "orders-db"))
    assert Evidence("db.path", "api → orders → db") in risk.evidence
    assert Evidence("api.configuration.exposure", "public") in risk.evidence


def test_the_nearest_public_entry_is_named() -> None:
    nodes = [
        component("a", exposure="public"),
        component("b", exposure="public"),
        component("hop"),
        component("vault", exposure="private", sensitive_operations=True),
    ]
    links = [call("a", "hop"), call("hop", "vault"), call("b", "vault")]
    [risk] = run(nodes, links, registry=EXPOSURE).findings
    assert Evidence("vault.path", "b → vault") in risk.evidence


def test_reachability_is_never_inferred() -> None:
    nodes = [
        component("internet-gateway"),  # a name says nothing
        component("db", NodeKind.DATABASE, personal_data=True),
    ]
    assert run(nodes, [call("internet-gateway", "db")], registry=EXPOSURE).findings == ()
    dependency = connection("api-vault", "api", "vault", kind=ConnectionKind.DEPENDENCY, protocol=None)
    nodes = [component("api", exposure="public"), component("vault", personal_data=True, exposure="private")]
    assert run(nodes, [dependency], registry=EXPOSURE).findings == ()  # no traffic, no path


def test_a_bidirectional_flow_reaches_both_ways() -> None:
    nodes = [component("hub", personal_data=True, exposure="private"), component("api", exposure="public")]
    link = dataclasses.replace(call("hub", "api"), bidirectional=True)
    [risk] = run(nodes, [link], registry=EXPOSURE).findings
    assert Evidence("hub.path", "api → hub") in risk.evidence


def test_a_component_clients_call_without_declared_exposure() -> None:
    nodes = [node("web", NodeKind.CLIENT), component("api")]
    [unknown] = run(nodes, [call("web", "api")], registry=EXPOSURE).findings
    assert (unknown.type, unknown.severity, unknown.missing) == (
        T.EXPOSURE_NOT_MODELED,
        Severity.LOW,
        ("api.configuration.exposure",),
    )


def test_the_analysis_is_deterministic() -> None:
    nodes = [
        component("a", exposure="public"),
        component("b", exposure="public"),
        component("vault", exposure="private", sensitive_operations=True),
    ]
    links = [call("a", "vault"), call("b", "vault")]
    first = run(nodes, links, registry=default_registry())
    again = run(list(reversed(nodes)), list(reversed(links)), registry=default_registry())
    assert first.to_dict() == again.to_dict()
