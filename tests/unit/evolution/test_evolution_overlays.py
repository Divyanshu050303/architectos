"""Candidate overlays (Milestone 13, phase 4): each candidate applied to an in-memory copy of its
exact baseline — the baseline unchanged, element ids preserved, provenance on every proposed value,
invalid transformations refused with what to act on, deterministic, serializable, reconstructible
and structurally diffable."""

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import ProvenanceSource
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.evolution.candidates import BaselineRef, Candidate, EvidenceRef, RuleRef
from core.domain.evolution.errors import InvalidCandidate
from core.domain.evolution.overlays import apply_candidate, reconstruct
from core.domain.evolution.values import CandidateCategory, EvidenceSource, EvidenceState
from core.domain.simulations.scenarios import ConfigurationChange
from tests.unit.architecture_ir.builders import connection, node

C = ConfigurationChange


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: ConfigValue) -> Any:
    return node(node_id, kind, configuration=Configuration(values))


def shop() -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            component("api", replicas=2, cpu_request_cores=Decimal(1), cpu_limit_cores=Decimal(2)),
            component("db", NodeKind.DATABASE, replicas=1),
        ),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https"),
            connection(
                "api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql",
                configuration=Configuration({"tls": False}),
            ),
        ),
    )  # fmt: skip


IR = shop()
BASELINE = BaselineRef(uuid.UUID(int=7), 3, content_hash(IR))


def candidate(*changes: ConfigurationChange, baseline: BaselineRef = BASELINE) -> Candidate:
    return Candidate(
        rule=RuleRef("scale-replicas", 1),
        baseline=baseline,
        category=CandidateCategory.SCALING,
        title="A proposal",
        description="Proposed configuration changes.",
        changes=changes,
        goals=("increase_workload:200 requests/second",),
        rationale="The evidence states it.",
        evidence=(
            EvidenceRef(EvidenceSource.CAPACITY, "a1", EvidenceState.CURRENT, "x", 3, BASELINE.content_hash),
        ),
    )


def test_fixture_12_a_candidate_leaves_the_baseline_unchanged() -> None:
    before = json.dumps(to_dict(IR), sort_keys=True)
    overlay = apply_candidate(IR, candidate(C("api", "replicas", 4), C("api-db", "tls", True)))
    assert json.dumps(to_dict(IR), sort_keys=True) == before
    assert content_hash(IR) == BASELINE.content_hash
    assert overlay.baseline is IR
    assert overlay.content_hash != BASELINE.content_hash
    api = overlay.architecture.node("api")
    link = overlay.architecture.connection("api-db")
    assert api is not None
    assert link is not None
    assert (api.configuration.get("replicas"), link.configuration.get("tls")) == (4, True)
    assert [n.id for n in overlay.architecture.nodes] == [n.id for n in IR.nodes]  # ids preserved
    assert [c.id for c in overlay.architecture.connections] == [c.id for c in IR.connections]


def test_every_proposed_value_carries_the_candidates_provenance() -> None:
    proposed = candidate(C("api", "replicas", 4))
    overlay = apply_candidate(IR, proposed)
    api = overlay.architecture.node("api")
    assert api is not None
    provenance = api.field_provenance["configuration.replicas"]
    assert (provenance.source, provenance.reference) == (
        ProvenanceSource.SYSTEM_DEFAULT,
        f"candidate:{proposed.id}",
    )
    assert (provenance.actor, provenance.inferred, provenance.verified) == (
        "evolution:scale-replicas@1",
        True,
        False,
    )
    db = overlay.architecture.node("db")
    assert db is not None
    assert "configuration.replicas" not in db.field_provenance  # unchanged elements untouched


def test_the_overlay_is_structurally_diffable() -> None:
    overlay = apply_candidate(IR, candidate(C("api", "replicas", 4), C("api-db", "tls", True)))
    [api] = overlay.diff.nodes
    assert (api.element_id, api.change) == ("api", ChangeKind.MODIFIED)
    assert "configuration.replicas" in {f.field for f in api.fields}
    [link] = overlay.diff.connections
    assert link.element_id == "api-db"
    assert overlay.diff.changed_ids() == frozenset({"api", "api-db"})
    assert [(c.element_id, c.property, c.evidence().value) for c in overlay.changes] == [
        ("api", "replicas", "2 -> 4"),
        ("api-db", "tls", "false -> true"),
    ]
    assert overlay.to_dict()["summary"] == overlay.diff.summary()


@pytest.mark.parametrize(
    ("changes", "reason", "element", "prop"),
    [
        ((C("cache", "replicas", 2),), "unknown_element", "cache", "replicas"),  # fixture 8
        ((C("api", "tls", True),), "not_applicable", "api", "tls"),  # fixture 9: a connection's, on a node
        ((C("web-api", "replicas", 3),), "not_applicable", "web-api", "replicas"),  # a node's on a connection
        ((C("web", "replicas", 2),), "not_applicable", "web", "replicas"),  # clients run no replicas
    ],
)  # fmt: skip
def test_fixtures_8_and_9_invalid_transformations_are_refused_with_what_to_act_on(
    changes: tuple[ConfigurationChange, ...], reason: str, element: str, prop: str
) -> None:
    proposed = candidate(*changes)
    with pytest.raises(InvalidCandidate) as refused:
        apply_candidate(IR, proposed)
    assert refused.value.details == {
        "candidate_id": proposed.id, "reason": reason, "element_id": element, "property": prop,
    }  # fmt: skip


def test_a_change_that_breaks_an_ir_rule_is_refused() -> None:
    proposed = candidate(C("api", "cpu_limit_cores", Decimal("0.5")))  # below the declared request of 1
    with pytest.raises(InvalidCandidate) as refused:
        apply_candidate(IR, proposed)
    details = refused.value.details
    assert details["candidate_id"] == proposed.id
    assert details["reason"] not in ("", "unknown_element", "not_applicable")
    assert details["element_id"] == "api"


def test_a_candidate_applies_only_to_its_own_baseline() -> None:
    other = BaselineRef(BASELINE.architecture_id, 4, "f" * 64)
    with pytest.raises(InvalidCandidate) as refused:
        apply_candidate(IR, candidate(C("api", "replicas", 4), baseline=other))
    assert refused.value.details["reason"] == "baseline_mismatch"


def test_the_overlay_is_deterministic_serializable_and_reconstructible() -> None:
    proposed = candidate(C("api-db", "tls", True), C("api", "replicas", 4))
    first, second = apply_candidate(IR, proposed), apply_candidate(IR, proposed)
    stored = json.loads(json.dumps(first.to_dict()))
    assert stored == second.to_dict()
    assert "nodes" not in stored  # the architecture itself is never stored
    assert stored["baseline"] == BASELINE.to_dict()
    assert reconstruct(IR, proposed, stored).content_hash == first.content_hash
    tampered = stored | {"content_hash": "0" * 64}
    with pytest.raises(InvalidCandidate) as refused:
        reconstruct(IR, proposed, tampered)
    assert refused.value.details["reason"] == "not_reproducible"


def test_the_overlay_is_a_simulation_scenario_for_its_impact() -> None:
    proposed = candidate(C("api", "replicas", 4))
    scenario = apply_candidate(IR, proposed).scenario()
    assert scenario.name == proposed.id
    assert scenario.changes == proposed.changes
    assert scenario.failures == ()
