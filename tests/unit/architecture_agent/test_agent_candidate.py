import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from core.architecture_ir.provenance import ProvenanceSource
from core.domain.architecture_agent.proposals import (
    Claim,
    DesignDecision,
    Proposal,
    ProposedConnection,
    ProposedNode,
)
from core.domain.architecture_agent.results import Candidate, EvidenceRef
from core.domain.architecture_agent.values import Basis
from core.domain.components.entities import SupportStatus
from core.domain.components.repository import ComponentCatalog
from core.domain.requirements.planning import PlanningInputV2
from engines.architecture_agent.candidate import RULE, CandidateInput, Construction, build_candidate
from persistence.component_catalog import default_catalog

CATALOG = default_catalog()
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
SURE = Decimal("0.8")
LATENCY, AVAILABILITY, AUDIT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
LABELS = {"REQ-1": LATENCY, "REQ-2": AVAILABILITY, "REQ-3": AUDIT}
PLANNING = cast(
    PlanningInputV2,
    {
        "schema_version": 2,
        "project": {"id": str(uuid.uuid4()), "settings": {}},
        "requirements": [
            {"id": str(LATENCY), "version": 2},
            {"id": str(AVAILABILITY), "version": 1},
            {"id": str(AUDIT), "version": 4},
        ],
    },
)
RUNBOOK = EvidenceRef("kch_runbook", "ks_1", 2, "Runbook (v2): Orders > Zones (lines 3-5)")


def api(**overrides: Any) -> ProposedNode:
    fields: dict[str, Any] = {
        "id": "orders-api",
        "kind": "service",
        "name": "Orders API",
        "rationale": "Serves orders",
        "technology": "python",
        "configuration": {"replicas": 2},
        "requirement_refs": ("REQ-1",),
        "confidence": Decimal("0.9"),
    }
    return ProposedNode(**(fields | overrides))


def db(**overrides: Any) -> ProposedNode:
    fields: dict[str, Any] = {
        "id": "orders-db",
        "kind": "database",
        "name": "Orders DB",
        "rationale": "Stores orders",
        "component": "databases/postgresql",
        "requirement_refs": ("REQ-2",),
        "evidence": ("kch_runbook",),
        "confidence": SURE,
    }
    return ProposedNode(**(fields | overrides))


def link(**overrides: Any) -> ProposedConnection:
    fields: dict[str, Any] = {
        "id": "api-db",
        "source": "orders-api",
        "target": "orders-db",
        "kind": "data_access",
        "rationale": "Reads and writes orders",
        "protocol": "PostgreSQL",
        "confidence": SURE,
    }
    return ProposedConnection(**(fields | overrides))


def proposal(**overrides: Any) -> Proposal:
    storage = DesignDecision("Storage", "Relational", "Orders are relational", requirement_refs=("REQ-2",))
    peak = Claim(
        "Peak traffic is 3x the daily average", Basis.ASSUMPTION, ("REQ-1",), confidence=Decimal("0.4")
    )
    zones = Claim("Orders run in two zones", Basis.RETRIEVED, evidence=("kch_runbook",), confidence=SURE)
    fields: dict[str, Any] = {
        "name": "Orders",
        "summary": "An API in front of a relational database.",
        "nodes": (api(), db()),
        "connections": (link(),),
        "decisions": (storage,),
        "claims": (peak, zones),
        "confidence": Decimal("0.7"),
    }
    return Proposal(**(fields | overrides))


def build(
    value: Proposal | None = None, catalog: ComponentCatalog = CATALOG, **overrides: Any
) -> Construction:
    fields: dict[str, Any] = {
        "proposal": value or proposal(),
        "planning_input": PLANNING,
        "labels": LABELS,
        "passages": {"kch_runbook": RUNBOOK},
        "model": "anthropic/claude-test",
        "prompt_version": "architecture-proposal-v1",
        "recorded_at": NOW,
    }
    return build_candidate(CandidateInput(**(fields | overrides)), catalog)


def built() -> Candidate:
    construction = build()
    assert construction.rejections == ()
    assert construction.candidate is not None
    return construction.candidate


def codes(construction: Construction) -> set[str]:
    return {r.code for r in construction.rejections}


def test_a_valid_proposal_becomes_canonical_ir() -> None:
    ir = built().ir
    assert [n.id for n in ir.nodes] == ["orders-api", "orders-db"]
    database, service, connection = ir.node("orders-db"), ir.node("orders-api"), ir.connection("api-db")
    assert database is not None
    assert service is not None
    assert connection is not None
    assert database.component == "databases/postgresql"
    assert database.technology is not None
    assert database.technology.name == "postgresql"  # taken from the component
    assert service.configuration.get("replicas") == 2
    assert connection.protocol == "postgresql"
    assert RULE == "agent-candidate@1"


def test_everything_is_an_unverified_model_proposal() -> None:
    ir = built().ir
    provenances = [ir.provenance, *(n.provenance for n in ir.nodes), *(c.provenance for c in ir.connections)]
    provenances += [a.provenance for a in ir.assumptions]
    for provenance in provenances:
        assert provenance is not None
        assert provenance.source is ProvenanceSource.LLM_PROPOSAL
        assert provenance.verified is False
        assert provenance.actor == "agent:anthropic/claude-test"
        assert provenance.reference == "architecture-proposal-v1"
        assert provenance.recorded_at == NOW
    assert ir.provenance is not None
    assert ir.provenance.confidence == Decimal("0.7")


def test_requirements_are_traced_to_exact_versions() -> None:
    ir = built().ir
    [latency] = ir.nodes[0].requirement_refs
    [availability] = ir.nodes[1].requirement_refs
    assert (latency.requirement_id, latency.version) == (LATENCY, 2)
    assert (availability.requirement_id, availability.version) == (AVAILABILITY, 1)


def test_assumptions_become_ir_assumptions() -> None:
    [assumption] = built().ir.assumptions
    assert assumption.statement == "Peak traffic is 3x the daily average"
    assert assumption.provenance.confidence == Decimal("0.4")
    assert assumption.requirement_refs[0].requirement_id == LATENCY


def test_normalizations_evidence_and_gaps_are_recorded() -> None:
    candidate = built()
    assert candidate.normalizations == (
        "node:orders-db: technology postgresql taken from component databases/postgresql",
        "connection:api-db: protocol written in lower case",
    )
    assert candidate.evidence == (RUNBOOK,)
    assert candidate.uncovered_requirements == ("REQ-3",)  # nothing traces to the audit requirement


def test_the_same_proposal_builds_the_same_candidate() -> None:
    assert built().content_hash == built().content_hash


def alone(node: ProposedNode) -> Proposal:
    return proposal(nodes=(node,), connections=())


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (alone(api(kind="client")), "not_applicable"),  # replicas on a client
        (alone(api(configuration={"bogus": 1})), "unknown_property"),
        (alone(api(configuration={"replicas": -1})), "out_of_range"),
        (proposal(connections=(link(target="nowhere"),)), "dangling_reference"),
        (proposal(nodes=(api(), db(id="orders-api"))), "duplicate_id"),
        (alone(api(component="databases/nope")), "component_not_in_catalog"),
        (alone(api(component="databases/postgresql")), "component_kind_mismatch"),
        (proposal(nodes=(api(), db(technology="mysql"))), "component_technology_mismatch"),
        (alone(api(technology="Not A Name!")), "invalid_technology"),
        (alone(api(requirement_refs=("REQ-9",))), "requirement_not_given"),
        (alone(api(evidence=("kch_other",))), "unknown_passage"),
    ],
)
def test_invalid_proposals_are_rejected_whole(value: Proposal, code: str) -> None:
    construction = build(value)
    assert construction.candidate is None
    assert code in codes(construction), construction.rejections


def test_a_deprecated_component_is_not_proposed() -> None:
    spec = replace(CATALOG.get("databases/postgresql"), support_status=SupportStatus.DEPRECATED)
    construction = build(catalog=ComponentCatalog({spec.id: (spec,)}, "test"))
    assert "component_deprecated" in codes(construction)


def test_a_label_outside_the_set_is_not_traced() -> None:
    construction = build(labels={"REQ-1": uuid.uuid4()})  # a requirement the planning input does not hold
    assert construction.candidate is None
    assert "requirement_not_given" in codes(construction)


def test_every_reason_is_reported() -> None:
    value = proposal(
        nodes=(api(configuration={"bogus": 1}, requirement_refs=("REQ-9",)), db(component="x/y"))
    )
    assert {"unknown_property", "requirement_not_given", "component_not_in_catalog"} <= codes(build(value))
