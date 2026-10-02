"""Proposed Architecture IR, review and acceptance readiness (Discovery Engine, phase 6): the candidates
as a canonical, structurally validated IR; what the source does not establish left for review, never
guessed; reviewer statements recorded as user-provided; inferences marked; supporting resources and
unresolved references kept outside the graph; reproducible from the stored result; nothing written."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.provenance import ProvenanceSource
from core.architecture_ir.serialization import content_hash, from_dict, to_dict
from core.domain.discovery.errors import InvalidDiscoveryRequest
from core.domain.discovery.results import DiscoveryResult, Proposal, ProposedElement
from core.domain.discovery.runs import (
    ArtifactInput,
    DiscoveryRequest,
    DiscoveryRun,
    ReviewDecision,
    SubjectType,
)
from core.domain.discovery.values import (
    Decision,
    ElementKind,
    ElementStatus,
    RunStatus,
    Severity,
    Verification,
)
from engines.discovery import engine as engine_module
from engines.discovery.engine import DeterministicDiscoveryEngine
from persistence.component_catalog import default_catalog
from tests.unit.discovery.test_discovery_normalize import JOBS
from tests.unit.discovery.test_discovery_sources import COMPOSE, DEPLOYMENT, TERRAFORM

ENGINE = DeterministicDiscoveryEngine(default_catalog())
ADA = uuid.UUID(int=7)
AT = datetime(2026, 10, 2, tzinfo=UTC)
API, INGRESS = "kubernetes:shop/deployment/api", "kubernetes:shop/ingress/web"
SHOP = (("k8s/shop.yaml", DEPLOYMENT), ("k8s/jobs.yaml", JOBS))
POD = """\
apiVersion: v1
kind: Pod
metadata: {name: both, namespace: x}
spec: {containers: [{name: a, image: "redis:7"}, {name: b, image: "postgres:16"}]}
"""


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return ENGINE.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def element(found: DiscoveryResult | Proposal, subject: str) -> ProposedElement:
    return next(e for e in found.elements if e.subject == subject)


def relationship_id(result: DiscoveryResult, source: str, reference: str) -> str:
    return next(r.id for r in result.relationships if r.source == source and r.reference == reference)


def accept(subject_type: SubjectType, subject: str, **chosen: object) -> ReviewDecision:
    return ReviewDecision(subject_type, subject, Decision.ACCEPTED, ADA, AT, **chosen)  # type: ignore[arg-type]


def finished(result: DiscoveryResult) -> DiscoveryRun:
    run = DiscoveryRun(uuid.UUID(int=1), uuid.UUID(int=2), RunStatus.PENDING, ADA, AT)
    return run.start(AT).finish(result, AT)


def test_the_default_proposal_holds_only_what_the_sources_establish() -> None:
    result = discover(*SHOP)
    assert result.proposed is not None
    assert {n.id for n in result.proposed.nodes} == {INGRESS, "kubernetes:shop/cronjob/nightly"}
    api = element(result, API)  # a Deployment does not say what it serves
    assert api.status is ElementStatus.NEEDS_REVIEW
    assert api.reason == "Its node kind is not established by the source; a reviewer can state it."
    assert element(result, "kubernetes:shop/service/api").status is ElementStatus.EXCLUDED  # routing
    assert element(result, "kubernetes:shop/secret/api-secrets").status is ElementStatus.EXCLUDED
    assert API in result.unresolved
    assert result.proposed.connections == ()  # no connection kind or endpoint is guessed


def test_provenance_is_never_verified_and_inferences_are_marked() -> None:
    result = discover(("compose.yaml", COMPOSE))
    assert result.proposed is not None
    db = result.proposed.node("compose:shop/service/db")
    assert db is not None
    assert (db.kind, db.component) == (NodeKind.DATABASE, "databases/postgresql")
    assert db.provenance is not None
    assert (db.provenance.source, db.provenance.verified) == (ProvenanceSource.FILE_IMPORT, False)
    assert db.field_provenance["component"].inferred  # from the image
    assert db.field_provenance["kind"].inferred  # the catalog component's only kind
    assert element(result, db.id).origin is Verification.INFERRED


def test_configuration_enters_the_ir_with_its_provenance() -> None:
    body = {
        "resource": {"aws_db_instance": {"main": {"engine": "postgres", "instance_class": "db.t3.micro"}}}
    }
    result = discover(("db.tf.json", json.dumps(body)))
    assert result.proposed is not None
    node = result.proposed.node("terraform:aws_db_instance.main")
    assert node is not None
    assert node.configuration.values["instance_class"] == "db.t3.micro"
    provenance = node.field_provenance["configuration.instance_class"]
    assert (provenance.source, provenance.inferred, provenance.verified) == (
        ProvenanceSource.TERRAFORM,
        False,
        False,
    )
    assert provenance.reference == "db.tf.json#0:resource.aws_db_instance.main"
    assert element(result, node.id).origin is Verification.OBSERVED  # type and engine are declared


def test_a_reviewer_states_what_the_source_does_not() -> None:
    result = discover(*SHOP)
    ingress_route = relationship_id(result, INGRESS, "service/api")
    assert element(result, ingress_route).status is ElementStatus.NEEDS_REVIEW  # waits for the api's kind
    reviewed = finished(result).decide(accept(SubjectType.ENTITY, API, node_kind=NodeKind.SERVICE))
    proposal = ENGINE.propose(result, reviewed.decisions)
    assert proposal.architecture is not None
    api = proposal.architecture.node(API)
    assert api is not None
    assert (api.kind, api.field_provenance["kind"].source) == (NodeKind.SERVICE, ProvenanceSource.USER_EDIT)
    assert element(proposal, API).origin is Verification.USER_PROVIDED
    route = proposal.architecture.connection(ingress_route)
    assert route is not None
    assert (route.source_id, route.target_id, route.kind) == (INGRESS, API, ConnectionKind.REQUEST)
    assert (
        element(proposal, ingress_route).via == "kubernetes:shop/service/api"
    )  # followed through the Service
    assert element(proposal, ingress_route).origin is Verification.INFERRED


def test_a_connection_kind_is_stated_by_a_reviewer_never_guessed() -> None:
    result = discover(("compose.yaml", COMPOSE))
    host = relationship_id(result, "compose:shop/service/api", "host/db")
    api = accept(SubjectType.ENTITY, "compose:shop/service/api", node_kind=NodeKind.SERVICE)
    run = finished(result).decide(api)
    pending = ENGINE.propose(result, run.decisions)
    assert element(pending, host).status is ElementStatus.NEEDS_REVIEW  # the kind of access is not stated
    run = run.decide(accept(SubjectType.RELATIONSHIP, host, connection_kind=ConnectionKind.DATA_ACCESS))
    proposal = ENGINE.propose(result, run.decisions)
    assert proposal.architecture is not None
    connection = proposal.architecture.connection(host)
    assert connection is not None
    assert connection.kind is ConnectionKind.DATA_ACCESS
    assert connection.field_provenance["kind"].source is ProvenanceSource.USER_EDIT


def test_review_never_overrides_the_source_and_resolves_ambiguity_among_candidates() -> None:
    run = finished(discover(*SHOP))
    with pytest.raises(InvalidDiscoveryRequest):  # the Ingress's kind is stated by the source
        run.decide(accept(SubjectType.ENTITY, INGRESS, node_kind=NodeKind.SERVICE))
    ambiguous = discover(("pod.yaml", POD))
    both = "kubernetes:x/pod/both"
    with pytest.raises(InvalidDiscoveryRequest):
        finished(ambiguous).decide(accept(SubjectType.ENTITY, both, component_id="messaging/kafka"))
    chosen = accept(SubjectType.ENTITY, both, component_id="databases/redis", node_kind=NodeKind.CACHE)
    proposal = ENGINE.propose(ambiguous, finished(ambiguous).decide(chosen).decisions)
    assert proposal.architecture is not None
    node = proposal.architecture.node(both)
    assert node is not None
    assert node.component == "databases/redis"
    assert node.field_provenance["component"].source is ProvenanceSource.USER_EDIT


def test_rejected_entities_and_their_connections_stay_out() -> None:
    result = discover(("compose.yaml", COMPOSE))
    run = finished(result)
    for key in ("compose:shop/service/api", "compose:shop/service/web"):
        run = run.decide(accept(SubjectType.ENTITY, key, node_kind=NodeKind.SERVICE))
    depends = relationship_id(result, "compose:shop/service/web", "service/api")
    included = ENGINE.propose(result, run.decisions)
    assert included.architecture is not None
    assert included.architecture.connection(depends) is not None  # depends_on: a dependency
    rejected = ReviewDecision(SubjectType.ENTITY, "compose:shop/service/api", Decision.REJECTED, ADA, AT)
    proposal = ENGINE.propose(result, run.decide(rejected).decisions)  # the latest decision applies
    assert proposal.architecture is not None
    assert proposal.architecture.node("compose:shop/service/api") is None
    assert element(proposal, "compose:shop/service/api").reason == "Rejected by a reviewer."
    assert element(proposal, depends).status is ElementStatus.EXCLUDED


def test_the_proposal_is_validated_by_the_ir_and_the_catalog() -> None:
    result = discover(("compose.yaml", COMPOSE))
    issues = {(v.code, v.element_id) for v in result.validation}
    assert ("constraint_cannot_evaluate", "compose:shop/service/db") in issues  # a required value unknown
    assert all(v.severity is not Severity.ERROR for v in result.validation)
    assert ENGINE.propose(result, ()).acceptance_problem is None
    service_only = discover(("k8s/svc.yaml", DEPLOYMENT.split("---")[1]))
    nothing = ENGINE.propose(service_only, ())
    assert (nothing.architecture, nothing.acceptance_problem) == (None, "nothing_to_accept")


def test_the_proposal_is_reproduced_from_the_stored_result() -> None:
    result = discover(*SHOP, ("compose.yaml", COMPOSE))
    again = ENGINE.propose(result, ())
    assert again.architecture is not None
    assert result.proposed is not None
    assert content_hash(again.architecture) == content_hash(result.proposed)
    assert from_dict(to_dict(result.proposed)) == result.proposed  # canonical IR, serializable
    reordered = discover(("compose.yaml", COMPOSE), *reversed(SHOP))
    assert reordered.fingerprint == result.fingerprint
    assert json.dumps(result.to_dict(), default=str)  # list-valued findings included


def test_versions_of_every_extractor_and_rule_are_recorded() -> None:
    result = discover(*SHOP)
    assert result.extractors["kubernetes"] == 1
    for rule in (
        "discovery-catalog",
        "discovery-configuration",
        "discovery-relationships",
        "discovery-proposal",
    ):
        assert result.extractors[rule] == 1
    assert set(ENGINE.versions()) >= set(result.extractors)


def test_a_discovery_beyond_the_result_limits_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "MAX_ENTITIES", 2)
    with pytest.raises(InvalidDiscoveryRequest) as error:
        discover(*SHOP)
    assert error.value.details["reason"] == "too_many_entities"


def test_every_candidate_has_an_element_and_proposing_writes_nothing() -> None:
    result = discover(*SHOP, ("main.tf.json", json.dumps(TERRAFORM)))
    subjects = {(e.kind, e.subject) for e in result.elements}
    assert subjects == {(ElementKind.NODE, e.key) for e in result.entities} | {
        (ElementKind.CONNECTION, r.id) for r in result.relationships
    }
    before = result.to_dict()
    ENGINE.propose(result, ())
    assert result.to_dict() == before
