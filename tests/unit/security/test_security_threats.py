"""STRIDE threat modeling (Milestone 10, phase 7): candidates grounded in findings and declared facts,
with traceable evidence, trust zones, assumptions and mitigations; no likelihood, score or CVE."""

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
from core.domain.engine_results import Evidence
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.results import (
    FindingBasis,
    FindingType,
    SecurityFinding,
    SecurityResult,
    StrideCategory,
)
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.security.context import SecurityContext
from engines.security.engine import analyze
from engines.security.registry import default_registry
from engines.security.threat_model import STRIDE, ThreatModel
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
S = StrideCategory
REVISION = RevisionInfo("arch-1", 1, "c" * 64)


def component(
    node_id: str, kind: NodeKind = NodeKind.SERVICE, parent: str | None = None, **values: Any
) -> Node:
    return node(node_id, kind, parent_id=parent, configuration=Configuration(values))


def zone(zone_id: str, level: str) -> Node:
    values = {"boundary_type": "trust_zone", "trust_level": level}
    return node(zone_id, NodeKind.BOUNDARY, configuration=Configuration(values))


def call(source: str, target: str, protocol: str = "http", **values: Any) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol=protocol,
        configuration=Configuration(values),
    )


def run(nodes: Sequence[Node], links: Sequence[Connection] = (), **request: Any) -> SecurityResult:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    context = SecurityContext(ir, REVISION, SecurityAnalysisRequest(uuid.UUID(int=1), 1, **request))
    return analyze(context, default_registry())


def threats(result: SecurityResult, category: StrideCategory | None = None) -> list[SecurityFinding]:
    return [
        f
        for f in result.findings
        if f.type is T.THREAT_CANDIDATE and (category is None or f.threat is category)
    ]


def source(result: SecurityResult, type_: FindingType) -> SecurityFinding:
    [found] = [f for f in result.findings if f.type is type_]
    return found


# --- candidates from findings --------------------------------------------------------------------


def test_a_missing_authentication_is_a_spoofing_candidate_traceable_to_its_finding() -> None:
    api = component("api", exposure="public", authentication="none", data_classification="internal")
    result = run([api])
    gap = source(result, T.MISSING_AUTHENTICATION)
    [spoofing] = threats(result, S.SPOOFING)
    assert (spoofing.basis, spoofing.severity, spoofing.certainty) == (
        FindingBasis.POTENTIAL_RISK,
        gap.severity,
        Certainty.MODELED,
    )
    assert spoofing.evidence[0] == Evidence("threat.derived_from", gap.id)
    assert set(gap.evidence) <= set(spoofing.evidence)
    assert spoofing.node_ids == ("api",)
    assert "Options for review" in spoofing.recommendation
    assert any("not a demonstrated or likely attack" in a for a in spoofing.assumptions)


def test_an_unprotected_crossing_maps_by_what_it_declares() -> None:
    nodes = [
        zone("dmz", "untrusted"),
        zone("core", "internal"),
        component("api", parent="dmz"),
        component("ledger", parent="core"),
    ]
    unencrypted = run(nodes, [call("api", "ledger", tls=False, authentication="mtls")])
    assert {t.threat for t in threats(unencrypted)} == {S.TAMPERING, S.INFORMATION_DISCLOSURE}
    anonymous = run(nodes, [call("api", "ledger", protocol="https", authentication="none")])
    [spoofing] = threats(anonymous)
    assert spoofing.threat is S.SPOOFING
    assert set(spoofing.boundary_ids) == {"core", "dmz"}  # the trust zones it concerns


def test_reachable_sensitive_components_map_to_disclosure_or_privilege() -> None:
    nodes = [
        component("api", exposure="public", authentication="oauth2", data_classification="internal"),
        component("db", NodeKind.DATABASE, exposure="private", personal_data=True, encryption_at_rest=True),
        component("admin", exposure="internal", management_interface=True),
    ]
    links = [
        connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="rediss"),
        call("api", "admin", tls=True, authentication="mtls"),
    ]
    result = run(nodes, links)
    assert [t for t in threats(result, S.INFORMATION_DISCLOSURE) if "db" in t.node_ids]
    assert [t for t in threats(result, S.ELEVATION_OF_PRIVILEGE) if "admin" in t.node_ids]
    assert not [t for t in threats(result, S.ELEVATION_OF_PRIVILEGE) if "db" in t.node_ids]


def test_findings_about_the_same_elements_form_one_candidate() -> None:
    api = component(
        "api", exposure="public", authentication="none", secret_source="hardcoded", secrets_required=True
    )
    result = run([api])
    [spoofing] = threats(result, S.SPOOFING)  # missing authentication and a hardcoded secret
    derived = set(spoofing.evidence[0].value.split(", "))
    assert derived == {source(result, T.MISSING_AUTHENTICATION).id, source(result, T.HARDCODED_SECRET).id}
    assert spoofing.severity is Severity.HIGH  # the worst of its sources


def test_a_candidate_is_as_sure_as_its_least_sure_source() -> None:
    proposal = Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal("0.5"))
    api = node(
        "api",
        configuration=Configuration({"exposure": "public", "authentication": "none"}),
        field_provenance={"configuration.authentication": proposal},
    )
    [spoofing] = threats(run([api]), S.SPOOFING)
    assert spoofing.certainty is Certainty.CANDIDATE


# --- repudiation ---------------------------------------------------------------------------------


def test_sensitive_operations_without_audit_logging() -> None:
    base: dict[str, Any] = {"sensitive_operations": True, "authentication": "mtls", "authorization": "rbac"}
    [declared] = threats(run([component("payments", **base, audit_logging=False)]), S.REPUDIATION)
    assert (declared.severity, declared.certainty, declared.missing) == (
        Severity.MEDIUM,
        Certainty.MODELED,
        (),
    )
    [unknown] = threats(run([component("payments", **base)]), S.REPUDIATION)
    assert (unknown.severity, unknown.certainty) == (Severity.LOW, Certainty.CANDIDATE)
    assert unknown.missing == ("payments.configuration.audit_logging",)
    assert not threats(run([component("payments", **base, audit_logging=True)]), S.REPUDIATION)


# --- selection, claims, determinism --------------------------------------------------------------


def test_selected_alone_it_says_it_has_nothing_to_build_on() -> None:
    api = component("api", exposure="public", authentication="none")
    alone = run([api], analyzers=("threat-model",))
    assert threats(alone) == []
    assert [u.code for u in alone.unsupported] == ["no_source_findings"]
    with_source = run([api], analyzers=("authentication", "threat-model"))
    assert threats(with_source, S.SPOOFING)


def test_no_likelihood_score_or_vulnerability_identifier_is_claimed() -> None:
    api = component(
        "api", exposure="public", authentication="none", secret_source="hardcoded", secrets_required=True
    )
    shown = json.dumps([t.to_dict() for t in threats(run([api]))]).lower()
    for claim in ("cve-", "cwe-", '"likelihood"', "score", "exploitable", "will be"):
        assert claim not in shown


def test_the_mapping_is_documented_in_the_analyzer_rules() -> None:
    rules = " ".join(ThreatModel.meta.rules)
    for type_, categories in STRIDE.items():
        assert f"{type_.value} → {', '.join(c.value for c in categories)}" in rules
    assert any("denial" in u.lower() for u in ThreatModel.meta.unsupported)  # what it does not derive


def test_candidates_are_reproducible() -> None:
    nodes = [
        component("api", exposure="public", authentication="none"),
        component("payments", sensitive_operations=True, authorization="none"),
    ]
    links = [call("api", "payments", authentication="none")]
    first = run(nodes, links)
    again = run(list(reversed(nodes)), list(reversed(links)))
    assert [t.to_dict() for t in threats(first)] == [t.to_dict() for t in threats(again)]
