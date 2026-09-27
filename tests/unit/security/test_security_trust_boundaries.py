"""Trust-boundary analysis (Milestone 10, phase 3): crossings of declared trust zones and exposure
boundaries, their modeled protection, and what is not modeled."""

import dataclasses
import uuid
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.results import FindingBasis, FindingType, SecurityFinding, SecurityResult
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity
from engines.security.context import SecurityContext
from engines.security.engine import Registry, analyze
from engines.security.trust_boundaries import TrustBoundaries, crossings
from tests.unit.architecture_ir.builders import connection, node

T = FindingType
REVISION = RevisionInfo("arch-1", 1, "c" * 64)


def zone(zone_id: str, level: str | None = None, parent: str | None = None) -> Node:
    values: dict[str, ConfigValue] = {"boundary_type": "trust_zone"}
    if level is not None:
        values["trust_level"] = level
    return node(zone_id, NodeKind.BOUNDARY, parent_id=parent, configuration=Configuration(values))


def service(
    node_id: str, parent: str | None = None, kind: NodeKind = NodeKind.SERVICE, **values: Any
) -> Node:
    return node(node_id, kind, parent_id=parent, configuration=Configuration(values))


def link(source: str, target: str, protocol: str | None = "http", **values: Any) -> Connection:
    return connection(
        f"{source}-{target}",
        source,
        target,
        kind=ConnectionKind.REQUEST,
        protocol=protocol,
        configuration=Configuration(values),
    )


def context(nodes: list[Node], links: list[Connection], **request: Any) -> SecurityContext:
    ir = ArchitectureIR("Shop", nodes=tuple(nodes), connections=tuple(links))
    return SecurityContext(ir, REVISION, SecurityAnalysisRequest(uuid.UUID(int=1), 1, **request))


def run(nodes: list[Node], links: list[Connection], **request: Any) -> SecurityResult:
    return analyze(context(nodes, links, **request), Registry([TrustBoundaries()]))


def of(result: SecurityResult, type_: FindingType) -> list[SecurityFinding]:
    return [f for f in result.findings if f.type is type_]


ZONES = [zone("dmz", "untrusted"), zone("core", "internal")]


# --- crossings -----------------------------------------------------------------------------------


def test_a_protected_crossing_raises_nothing() -> None:
    result = run(
        [*ZONES, service("api", "dmz"), service("orders", "core")],
        [link("api", "orders", tls=True, authentication="mtls")],
    )
    crossing_types = {T.UNPROTECTED_BOUNDARY_CROSSING, T.CROSSING_CONTROLS_NOT_MODELED}
    assert not [f for f in result.findings if f.type in crossing_types]


def test_an_encrypted_protocol_counts_as_encrypted() -> None:
    result = run(
        [*ZONES, service("api", "dmz"), service("orders", "core")],
        [link("api", "orders", protocol="https", authentication="token")],
    )
    assert not of(result, T.CROSSING_CONTROLS_NOT_MODELED)
    assert not of(result, T.UNPROTECTED_BOUNDARY_CROSSING)


def test_a_crossing_that_declares_no_protection_is_a_control_gap() -> None:
    result = run(
        [*ZONES, service("api", "dmz"), service("orders", "core")],
        [link("api", "orders", tls=False)],
    )
    [gap] = of(result, T.UNPROTECTED_BOUNDARY_CROSSING)
    assert (gap.basis, gap.severity, gap.certainty) == (
        FindingBasis.CONTROL_GAP,
        Severity.MEDIUM,
        Certainty.MODELED,
    )
    assert (gap.node_ids, gap.connection_ids, gap.boundary_ids) == (
        ("api", "orders"),
        ("api-orders",),
        ("core", "dmz"),
    )
    assert Evidence("api-orders.configuration.tls", "false") in gap.evidence
    assert Evidence("api-orders.crosses", "trust zones dmz (untrusted) → core (internal)") in gap.evidence
    assert gap.missing == ("api-orders.configuration.authentication",)  # what else is not modeled
    assert "is not encrypted in transit" in gap.title


def test_authentication_none_across_a_boundary_is_a_control_gap() -> None:
    result = run(
        [*ZONES, service("api", "dmz"), service("orders", "core", data_classification="restricted")],
        [link("api", "orders", protocol="https", authentication="none")],
    )
    [gap] = of(result, T.UNPROTECTED_BOUNDARY_CROSSING)
    assert gap.severity is Severity.HIGH  # it reaches restricted data
    assert "does not authenticate" in gap.title


def test_unmodeled_controls_are_not_evaluable_never_secure_nor_insecure() -> None:
    result = run([*ZONES, service("api", "dmz"), service("orders", "core")], [link("api", "orders")])
    [unknown] = of(result, T.CROSSING_CONTROLS_NOT_MODELED)
    assert unknown.basis is FindingBasis.NOT_EVALUABLE
    assert unknown.missing == ("api-orders.configuration.authentication", "api-orders.configuration.tls")
    assert not of(result, T.UNPROTECTED_BOUNDARY_CROSSING)


def test_flows_inside_one_zone_do_not_cross() -> None:
    result = run([*ZONES, service("api", "core"), service("orders", "core")], [link("api", "orders")])
    assert not [f for f in result.findings if f.connection_ids]


def test_nested_zones_cross_at_their_edge() -> None:
    nodes = [
        zone("core", "internal"),
        zone("vault", "restricted", parent="core"),
        service("api", "core"),
        service("keys", "vault"),
    ]
    crossing = crossings(context(nodes, [link("api", "keys")]))["api-keys"]
    assert (crossing.source_zone, crossing.target_zone, crossing.zoned) == ("core", "vault", True)


def test_one_end_outside_every_zone_crosses_the_zone_edge() -> None:
    result = run(
        [zone("core", "internal"), node("web", NodeKind.CLIENT), service("api", "core")],
        [link("web", "api")],
    )
    [unknown] = of(result, T.CROSSING_CONTROLS_NOT_MODELED)
    expected = Evidence("web-api.crosses", "trust zones outside every trust zone → core (internal)")
    assert expected in unknown.evidence


def test_an_exposure_boundary_is_a_boundary_without_zones() -> None:
    result = run(
        [service("api", exposure="public"), service("db", kind=NodeKind.DATABASE, exposure="private")],
        [link("api", "db", tls=False)],
    )
    [gap] = of(result, T.UNPROTECTED_BOUNDARY_CROSSING)
    assert Evidence("api-db.crosses", "exposure public → private") in gap.evidence
    assert gap.boundary_ids == ()  # no trust zone involved


def test_boundaries_are_never_inferred_from_names() -> None:
    nodes = [
        node("dmz", NodeKind.BOUNDARY, configuration=Configuration({"boundary_type": "network"})),
        node("internal", NodeKind.BOUNDARY),
        service("public-api", "dmz"),
        service("private-db", "internal", kind=NodeKind.DATABASE),
        node("internet", NodeKind.EXTERNAL),
    ]
    result = run(nodes, [link("public-api", "private-db"), link("internet", "public-api")])
    assert result.findings == ()  # nothing declared crosses anything


# --- sensitive data ------------------------------------------------------------------------------


def test_sensitive_data_crossing_is_a_potential_risk_even_when_protected() -> None:
    protected = run(
        [*ZONES, service("api", "dmz"), service("orders", "core")],
        [link("api", "orders", tls=True, authentication="mtls", personal_data=True)],
    )
    [risk] = of(protected, T.SENSITIVE_DATA_CROSSES_BOUNDARY)
    assert (risk.basis, risk.severity) == (FindingBasis.POTENTIAL_RISK, Severity.LOW)
    assert Evidence("api-orders.configuration.personal_data", "true") in risk.evidence
    assert risk.assumptions  # the declared controls are not verified
    exposed = run(
        [*ZONES, service("api", "dmz"), service("orders", "core", data_classification="confidential")],
        [link("api", "orders", tls=False)],
    )
    [risk] = of(exposed, T.SENSITIVE_DATA_CROSSES_BOUNDARY)
    assert risk.severity is Severity.MEDIUM


def test_unknown_sensitivity_is_not_sensitive_nor_harmless() -> None:
    result = run(
        [*ZONES, service("api", "dmz"), service("orders", "core")],
        [link("api", "orders", tls=True, authentication="mtls")],
    )
    assert not of(result, T.SENSITIVE_DATA_CROSSES_BOUNDARY)  # no classification: nothing claimed


# --- zones and semantics -------------------------------------------------------------------------


def test_trust_levels_and_elements_outside_zones_that_are_not_modeled() -> None:
    nodes = [
        zone("dmz"),
        zone("core", "internal"),
        service("api", "dmz"),
        service("orders"),
        node("web", NodeKind.CLIENT),
    ]
    unmodeled = of(run(nodes, []), T.TRUST_LEVEL_NOT_MODELED)
    assert {(f.boundary_ids, f.node_ids) for f in unmodeled} == {(("dmz",), ()), ((), ("orders", "web"))}
    level = next(f for f in unmodeled if f.boundary_ids)
    assert level.missing == ("dmz.configuration.trust_level",)


def test_a_trust_level_on_a_boundary_that_is_not_a_trust_zone() -> None:
    values: dict[str, ConfigValue] = {"boundary_type": "network", "trust_level": "internal"}
    boundary = node("net", NodeKind.BOUNDARY, configuration=Configuration(values))
    [finding] = of(run([boundary, service("api", "net")], []), T.INCONSISTENT_TRUST_BOUNDARY)
    assert (finding.boundary_ids, finding.basis) == (("net",), FindingBasis.NOT_EVALUABLE)


def test_thin_crossing_semantics_are_reported() -> None:
    dependency = connection("api-vault", "api", "vault", kind=ConnectionKind.DEPENDENCY, protocol=None)
    result = run([*ZONES, service("api", "dmz"), service("vault", "core")], [dependency])
    [thin] = of(result, T.INSUFFICIENT_FLOW_SEMANTICS)
    assert thin.connection_ids == ("api-vault",)
    assert not of(result, T.CROSSING_CONTROLS_NOT_MODELED)  # nothing described to protect
    socket = dataclasses.replace(link("api", "orders", tls=True, authentication="token"), bidirectional=True)
    result = run([*ZONES, service("api", "dmz"), service("orders", "core")], [socket])
    [thin] = of(result, T.INSUFFICIENT_FLOW_SEMANTICS)
    assert "reverse direction" in thin.explanation


# --- provenance, scope, determinism --------------------------------------------------------------


def test_proposed_facts_make_a_finding_a_candidate() -> None:
    proposal = Provenance(ProvenanceSource.LLM_PROPOSAL, confidence=Decimal("0.7"))
    proposed = dataclasses.replace(
        link("api", "orders", tls=False), field_provenance={"configuration.tls": proposal}
    )
    result = run([*ZONES, service("api", "dmz"), service("orders", "core")], [proposed])
    [gap] = of(result, T.UNPROTECTED_BOUNDARY_CROSSING)
    assert gap.certainty is Certainty.CANDIDATE
    assert Evidence("api-orders.configuration.tls", "false (llm_proposal)") in gap.evidence


def test_a_scope_keeps_only_the_crossings_touching_it() -> None:
    nodes = [
        *ZONES,
        service("api", "dmz"),
        service("orders", "core"),
        service("billing", "dmz"),
        service("ledger", "core"),
    ]
    result = run(nodes, [link("api", "orders"), link("billing", "ledger")], scope=("orders",))
    assert {c for f in result.findings for c in f.connection_ids} == {"api-orders"}


def test_the_analysis_is_deterministic_and_leaves_the_architecture_unchanged() -> None:
    nodes = [*ZONES, service("api", "dmz", exposure="public"), service("orders", "core", personal_data=True)]
    links = [link("api", "orders", tls=False), link("orders", "api")]
    first = run(nodes, links)
    again = run(list(reversed(nodes)), list(reversed(links)))
    assert first.to_dict() == again.to_dict()
    assert [f.id for f in first.findings] == [f.id for f in again.findings]
    ctx = context(nodes, links)
    before = dataclasses.replace(ctx.ir)
    analyze(ctx, Registry([TrustBoundaries()]))
    assert ctx.ir == before
