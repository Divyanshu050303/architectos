"""The ten architecture fixtures of the milestone (phase 10), each through every shipped analyzer:
what is found, what is not, and that unknown is never reported as secure. Also: the whole registry is
deterministic, the architecture is never modified, and the engine stays apart from ArchitectOS's own
authentication."""

import ast
import json
import random
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
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.results import FindingBasis, FindingType, SecurityResult, SecurityStatus
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Verdict
from engines.security.context import SecurityContext
from engines.security.engine import analyze
from engines.security.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
K = ConnectionKind
ROOT = Path(__file__).resolve().parents[3]
REVISION = RevisionInfo("arch-1", 1, "c" * 64)


def component(
    node_id: str, kind: NodeKind = NodeKind.SERVICE, parent: str | None = None, **values: Any
) -> Node:
    return node(node_id, kind, parent_id=parent, configuration=Configuration(values))


def zone(zone_id: str, level: str) -> Node:
    values = {"boundary_type": "trust_zone", "trust_level": level}
    return node(zone_id, NodeKind.BOUNDARY, configuration=Configuration(values))


def link(
    source: str, target: str, kind: ConnectionKind = K.REQUEST, protocol: str = "https", **values: Any
) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=kind,
        protocol=protocol,
        configuration=Configuration(values),
    )


def run(nodes: Sequence[Node], links: Sequence[Connection] = (), **context: Any) -> SecurityResult:
    ir = ArchitectureIR("Fixture", nodes=tuple(nodes), connections=tuple(links))
    request = SecurityAnalysisRequest(uuid.UUID(int=1), 1)
    return analyze(SecurityContext(ir, REVISION, request, **context), default_registry())


def types(result: SecurityResult, node_id: str | None = None) -> set[FindingType]:
    return {f.type for f in result.findings if node_id is None or node_id in f.node_ids}


WEB = node("web", NodeKind.CLIENT)
ENCRYPTION = {T.UNENCRYPTED_DATA_AT_REST, T.ENCRYPTION_NOT_MODELED, T.UNENCRYPTED_DATA_IN_TRANSIT}
SECRETS = {T.HARDCODED_SECRET, T.SECRET_SOURCE_NOT_MODELED, T.SECRET_IN_CONFIGURATION}


def test_1_public_api_with_explicit_authentication() -> None:
    api = component(
        "api",
        exposure="public",
        authentication="oauth2",
        authorization="rbac",
        data_classification="internal",
    )
    result = run([WEB, api], [link("web", "api")])
    assert not types(result) & {T.MISSING_AUTHENTICATION, T.AUTHENTICATION_NOT_MODELED}


def test_2_public_api_with_missing_authentication_evidence() -> None:
    result = run([WEB, component("api", exposure="public")], [link("web", "api")])
    assert T.AUTHENTICATION_NOT_MODELED in types(result, "api")
    assert T.MISSING_AUTHENTICATION not in types(result)  # absent is not "none"


def test_3_sensitive_datastore_with_explicit_encryption() -> None:
    db = component("db", NodeKind.DATABASE, data_classification="restricted", encryption_at_rest=True)
    result = run([component("api"), db], [link("api", "db", K.DATA_ACCESS, "postgresql", tls=True)])
    assert not types(result, "db") & ENCRYPTION


def test_4_sensitive_datastore_with_unknown_encryption() -> None:
    configuration = Configuration({"personal_data": True}, unknown={"encryption_at_rest"})
    result = run([node("db", NodeKind.DATABASE, configuration=configuration)])
    [finding] = [f for f in result.findings if f.type is T.ENCRYPTION_NOT_MODELED]
    assert finding.basis is FindingBasis.NOT_EVALUABLE
    assert T.UNENCRYPTED_DATA_AT_REST not in types(result)  # unknown is neither encrypted nor not


def test_5_service_flow_crossing_a_trust_boundary() -> None:
    nodes = [
        zone("app", "internal"),
        zone("data", "restricted"),
        component("orders", parent="app"),
        component("ledger", parent="data", sensitive_operations=True),
    ]
    result = run(nodes, [link("orders", "ledger", K.REQUEST, "http", tls=False, authentication="none")])
    [crossing] = [f for f in result.findings if f.type is T.UNPROTECTED_BOUNDARY_CROSSING]
    assert crossing.boundary_ids == ("app", "data")
    assert T.THREAT_CANDIDATE in types(result, "ledger")


def test_6_explicit_secret_manager_reference() -> None:
    api = component("api", secrets_required=True, secret_source="secret_manager")
    assert not types(run([api])) & SECRETS


def test_7_hardcoded_secret_indicator_without_exposing_the_value() -> None:
    api = node(
        "api",
        configuration=Configuration(
            {"secret_source": "hardcoded", "secrets_required": True},
            extra={"db_password": "correct-horse-battery"},
        ),
    )
    result = run([api])
    assert {T.HARDCODED_SECRET, T.SECRET_IN_CONFIGURATION} <= types(result, "api")
    assert "correct-horse-battery" not in json.dumps(result.to_dict())


def test_8_publicly_exposed_management_interface() -> None:
    admin = component("admin", exposure="public", management_interface=True, authentication="password")
    result = run([WEB, admin], [link("web", "admin")])
    [gap] = [f for f in result.findings if f.type is T.PUBLIC_MANAGEMENT_INTERFACE]
    assert gap.basis is FindingBasis.CONTROL_GAP


def test_9_missing_authorization_evidence_for_a_sensitive_operation() -> None:
    payments = component("payments", exposure="internal", authentication="mtls", sensitive_operations=True)
    result = run([payments])
    assert T.AUTHORIZATION_NOT_MODELED in types(result, "payments")
    assert T.MISSING_AUTHORIZATION not in types(result)


def test_10_an_incomplete_architecture_yields_partial_findings_never_secure() -> None:
    nodes = [
        WEB,
        component("api", exposure="public", authentication="oauth2"),
        component("db", NodeKind.DATABASE),
    ]
    links = [link("web", "api"), link("api", "db", K.DATA_ACCESS, "postgresql")]
    result = run(nodes, links, policy=ArchitecturePolicy(require_encryption_at_rest=True))
    assert result.status is SecurityStatus.PARTIAL
    assert T.DATA_CLASSIFICATION_NOT_MODELED in types(result, "db")
    [check] = result.checks
    assert check.verdict is Verdict.NOT_VERIFIABLE  # cannot evaluate, not "secure"
    empty = run([WEB, component("api"), component("db", NodeKind.DATABASE)])
    assert empty.status is SecurityStatus.INSUFFICIENT_INPUT


# --- the whole registry --------------------------------------------------------------------------


def _everything() -> tuple[list[Node], list[Connection]]:
    nodes = [
        WEB,
        zone("dmz", "untrusted"),
        zone("core", "internal"),
        component("api", parent="dmz", exposure="public", authentication="none", secrets_required=True),
        component("admin", parent="dmz", exposure="public", management_interface=True),
        component("pay", parent="core", sensitive_operations=True, authorization="none", audit_logging=False),
        component("db", NodeKind.DATABASE, parent="core", personal_data=True, encryption_at_rest=False),
        component("psp", NodeKind.EXTERNAL, data_classification="restricted"),
    ]
    links = [
        link("web", "api"),
        link("api", "pay", K.REQUEST, "http", tls=False),
        link("pay", "db", K.DATA_ACCESS, "postgresql", authentication="none"),
        link("pay", "psp", K.REQUEST, "https", authentication="api_key"),
    ]
    return nodes, links


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_whole_registry_is_deterministic_whatever_the_order(seed: int) -> None:
    nodes, links = _everything()
    policy = ArchitecturePolicy(require_encryption_at_rest=True, require_audit_logging=True)
    first = run(nodes, links, policy=policy)
    shuffled_nodes, shuffled_links = nodes[:], links[:]
    random.Random(seed).shuffle(shuffled_nodes)  # noqa: S311 -- a reproducible test order, not a secret
    random.Random(seed).shuffle(shuffled_links)  # noqa: S311 -- a reproducible test order, not a secret
    again = run(shuffled_nodes, shuffled_links, policy=policy)
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    assert [f.id for f in first.findings] == [f.id for f in again.findings]
    assert [c.to_dict() for c in first.checks] == [c.to_dict() for c in again.checks]
    assert {f.basis for f in first.findings} == set(FindingBasis)  # the four kinds, all present, kept apart


def test_the_architecture_is_never_modified() -> None:
    nodes, links = _everything()
    ir = ArchitectureIR("Fixture", nodes=tuple(nodes), connections=tuple(links))
    before = json.dumps(to_dict(ir), sort_keys=True)
    analyze(SecurityContext(ir, REVISION, SecurityAnalysisRequest(uuid.UUID(int=1), 1)), default_registry())
    assert json.dumps(to_dict(ir), sort_keys=True) == before


def test_the_engine_is_apart_from_platform_authentication() -> None:
    """Architecture-level analysis only: nothing under engines/security or core/domain/security reads
    ArchitectOS's users, sessions or credentials, or the API and database adapters (the service
    checks permissions through the shared access helper)."""
    forbidden = ("core.domain.identity", "apps", "persistence", "core.domain.organizations")
    allowed = {"core.domain.organizations.permissions"}  # the permission names the service checks
    for package in ("engines/security", "core/domain/security"):
        for path in sorted((ROOT / package).rglob("*.py")):
            tree = ast.parse(path.read_text())
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            leaks = {m for m in imported if m.startswith(forbidden)} - allowed
            assert not leaks, (path, leaks)
