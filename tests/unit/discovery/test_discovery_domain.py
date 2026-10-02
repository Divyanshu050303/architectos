"""Discovery domain contracts (Discovery Engine, phase 1): findings that keep their evidence and how
they are known, mappings that never force a match, relationships only from explicit references, a
consistent, deterministic and versioned result, safe request limits, the run lifecycle, and review
decisions recorded per item."""

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.provenance import ProvenanceSource
from core.domain.discovery.errors import (
    InvalidDiscoveryRequest,
    InvalidDiscoveryResult,
    InvalidDiscoveryTransition,
)
from core.domain.discovery.findings import (
    CandidateEntity,
    CandidateRelationship,
    ComponentMapping,
    Diagnostic,
    Finding,
    PropertyMapping,
    SourceArtifact,
    SourceLocation,
)
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    ArtifactInput,
    DiscoveryRequest,
    DiscoveryRun,
    ReviewDecision,
    RunError,
    SubjectType,
    safe_path,
)
from core.domain.discovery.values import (
    ArtifactStatus,
    Decision,
    FindingType,
    MappingStatus,
    RelationshipStatus,
    RunStatus,
    Severity,
    SourceType,
    Verification,
    provenance_for,
)

AT = datetime(2026, 10, 2, 9, tzinfo=UTC)
ADA = uuid.UUID(int=10)
PATH = "k8s/shop.yaml"
HASH = "a" * 64
API, DB = "kubernetes:shop/deployment/api", "kubernetes:shop/statefulset/db"
RULE = "discovery-catalog@1"
OBSERVED = Verification.OBSERVED
EXTRACTOR = "kubernetes@1"


def where(pointer: str | None = None, document: int = 0) -> SourceLocation:
    return SourceLocation(PATH, document, pointer)


def artifact(status: ArtifactStatus = ArtifactStatus.PARSED) -> SourceArtifact:
    return SourceArtifact(PATH, HASH, 120, status, SourceType.KUBERNETES, documents=2)


def entity_finding(entity: str = API, document: int = 0) -> Finding:
    return Finding(FindingType.ENTITY, entity, where("metadata.name", document), OBSERVED, EXTRACTOR)


def replicas() -> Finding:
    return Finding(
        FindingType.PROPERTY, API, where("spec.replicas"), OBSERVED, EXTRACTOR,
        property="replicas", source_property="spec.replicas", value=3,
    )  # fmt: skip


def reference(target: str = "db") -> Finding:
    location = where("spec.template.spec.containers[0].env[0].value")
    return Finding(FindingType.REFERENCE, API, location, OBSERVED, EXTRACTOR, target=target)


def ambiguous() -> ComponentMapping:
    candidates = ("databases/postgresql", "databases/mysql")
    return ComponentMapping(MappingStatus.AMBIGUOUS, RULE, candidates=candidates, reason="Two fit.")


def entity(key: str = API, mapping: ComponentMapping | None = None, **fields: Any) -> CandidateEntity:
    finding = entity_finding(key, 0 if key == API else 1)
    mapped = mapping or ComponentMapping(MappingStatus.MAPPED, RULE, "compute/kubernetes")
    defaults: dict[str, Any] = {"kind": NodeKind.SERVICE, "kind_rule": "test@1", "finding_ids": (finding.id,)}
    name = key.rsplit("/", 1)[-1]
    return CandidateEntity(
        key, name, SourceType.KUBERNETES, "Deployment", finding.location, mapped, **(defaults | fields)
    )


def result(**fields: Any) -> DiscoveryResult:
    defaults: dict[str, Any] = {
        "artifacts": (artifact(),),
        "findings": (entity_finding(), entity_finding(DB, 1), replicas(), reference()),
        "entities": (entity(), entity(DB, kind=NodeKind.DATABASE)),
        "extractors": {"kubernetes": 1},
    }
    return DiscoveryResult(**(defaults | fields))


# --- provenance and findings -----------------------------------------------------------------------


def test_how_a_value_is_known_becomes_ir_provenance_never_verification() -> None:
    observed = provenance_for(OBSERVED, SourceType.KUBERNETES, "k8s/shop.yaml#0:spec.replicas")
    assert observed is not None
    assert (observed.source, observed.verified, observed.inferred) == (
        ProvenanceSource.KUBERNETES,
        False,
        False,
    )
    inferred = provenance_for(Verification.INFERRED, SourceType.TERRAFORM_JSON, None)
    assert inferred is not None
    assert (inferred.source, inferred.inferred) == (ProvenanceSource.TERRAFORM, True)
    confirmed = provenance_for(Verification.USER_PROVIDED, SourceType.DOCKER_COMPOSE, None)
    assert confirmed is not None
    assert (confirmed.source, confirmed.verified) == (ProvenanceSource.USER_EDIT, True)
    for unknown in (Verification.UNKNOWN, Verification.UNSUPPORTED):
        assert provenance_for(unknown, SourceType.KUBERNETES, None) is None  # never enters the IR


def test_a_finding_keeps_its_evidence_and_never_a_secret() -> None:
    finding = replicas()
    assert finding.id == replicas().id  # stable
    assert finding.location.reference == "k8s/shop.yaml#0:spec.replicas"
    secret = Finding(
        FindingType.PROPERTY, API, where("env[1]"), OBSERVED, EXTRACTOR, property="environment", redacted=True
    )
    assert secret.to_dict()["value"] is None
    with pytest.raises(InvalidDiscoveryResult):
        dataclasses.replace(secret, value="s3cr3t")  # a redacted value is never kept
    with pytest.raises(InvalidDiscoveryResult):
        Finding(FindingType.PROPERTY, API, where(), OBSERVED, EXTRACTOR)  # which property?
    with pytest.raises(InvalidDiscoveryResult):
        Finding(FindingType.REFERENCE, API, where(), OBSERVED, EXTRACTOR)  # to what?


def test_a_mapping_is_never_forced() -> None:
    with pytest.raises(InvalidDiscoveryResult):
        ComponentMapping(MappingStatus.MAPPED, RULE)  # mapped to what?
    with pytest.raises(InvalidDiscoveryResult):
        ComponentMapping(MappingStatus.AMBIGUOUS, RULE, candidates=("databases/mysql",), reason="Two fit.")
    with pytest.raises(InvalidDiscoveryResult):
        ComponentMapping(MappingStatus.UNMAPPED, RULE)  # says why not
    assert ambiguous().candidates == ("databases/mysql", "databases/postgresql")
    with pytest.raises(InvalidDiscoveryResult):  # why is it invalid?
        PropertyMapping("spec.replicas", "replicas", RULE, OBSERVED, "f", valid=False)


def test_entities_rest_on_evidence_and_their_keys_are_ir_ids() -> None:
    assert entity().key == API
    with pytest.raises(InvalidDiscoveryResult):
        entity(finding_ids=())
    with pytest.raises(InvalidDiscoveryResult):
        entity("../escape")
    assert entity(kind=None, kind_rule=None).kind is None  # what it is, the source does not say: unknown


def test_a_relationship_is_resolved_only_to_a_discovered_entity() -> None:
    found = reference()
    evidence = (found.id,)
    resolved = CandidateRelationship(
        API, "db", found.location, RelationshipStatus.RESOLVED, DB, finding_ids=evidence
    )
    assert resolved.kind is None  # a reference alone establishes no traffic or kind
    with pytest.raises(InvalidDiscoveryResult):
        CandidateRelationship(API, "db", found.location, RelationshipStatus.RESOLVED, finding_ids=evidence)
    with pytest.raises(InvalidDiscoveryResult):  # unresolved says why
        CandidateRelationship(
            API, "payments", found.location, RelationshipStatus.UNRESOLVED, finding_ids=evidence
        )
    with pytest.raises(InvalidDiscoveryResult):
        CandidateRelationship(
            API, "api", found.location, RelationshipStatus.RESOLVED, API, finding_ids=evidence
        )
    dependency = dataclasses.replace(resolved, kind=ConnectionKind.DEPENDENCY, reason="depends_on")
    assert dependency.id == resolved.id  # stable: the reference and where


# --- the result ------------------------------------------------------------------------------------


def test_a_result_is_consistent() -> None:
    with pytest.raises(InvalidDiscoveryResult) as error:
        result(findings=(replicas(),))  # the entities cite findings that are not there
    assert "findings" in error.value.details["fields"]
    absent = CandidateRelationship(
        API, "cache", where(), RelationshipStatus.RESOLVED, "kubernetes:shop/deployment/cache",
        finding_ids=(reference().id,),
    )  # fmt: skip
    with pytest.raises(InvalidDiscoveryResult):
        result(relationships=(absent,))
    stray = dataclasses.replace(replicas(), location=SourceLocation("other.yaml"))
    with pytest.raises(InvalidDiscoveryResult):
        result(findings=(entity_finding(), entity_finding(DB, 1), stray))  # from no supplied artifact


def test_a_result_is_canonical_and_deterministic() -> None:
    first = result()
    shuffled = result(findings=tuple(reversed(first.findings)), entities=tuple(reversed(first.entities)))
    assert first.to_dict() == shuffled.to_dict()
    assert first.fingerprint == shuffled.fingerprint
    assert [e.key for e in first.entities] == sorted(e.key for e in first.entities)
    assert not {"score", "confidence"} & set(first.summary())


def test_what_is_unresolved_or_unsupported_makes_warnings() -> None:
    assert not result().has_warnings
    assert result(entities=(entity(), entity(DB, ambiguous()))).unresolved == (DB,)
    assert result(artifacts=(artifact(ArtifactStatus.PARTIAL),)).has_warnings
    noted = result(
        diagnostics=(Diagnostic("unsupported_kind", Severity.WARNING, "CronJob is not read.", where()),)
    )
    assert noted.has_warnings
    assert noted.summary()["diagnostics"] == {"warning": 1}


# --- requests --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["", "/etc/passwd", "../secrets.yaml", "k8s/../../x", "a\\b.yaml", "./a.yaml", "a/./b", "x\x00y", "a//b"],
)
def test_unsafe_artifact_paths_are_refused(path: str) -> None:
    assert not safe_path(path)
    with pytest.raises(InvalidDiscoveryRequest) as error:
        ArtifactInput(path, "kind: Service")
    assert error.value.details == {"field": "artifacts.path", "reason": "unsafe_path"}


def test_requests_are_bounded() -> None:
    with pytest.raises(InvalidDiscoveryRequest) as error:
        ArtifactInput("big.yaml", "x" * (MAX_ARTIFACT_BYTES + 1))
    assert error.value.details["reason"] == "too_large"
    many = tuple(ArtifactInput(f"f{i}.yaml", "a: 1") for i in range(MAX_ARTIFACTS + 1))
    with pytest.raises(InvalidDiscoveryRequest):
        DiscoveryRequest(many)
    with pytest.raises(InvalidDiscoveryRequest):
        DiscoveryRequest((ArtifactInput("a.yaml", "a"), ArtifactInput("a.yaml", "b")))  # duplicate paths
    with pytest.raises(InvalidDiscoveryRequest):
        DiscoveryRequest(())
    request = DiscoveryRequest((ArtifactInput("b.yaml", "b: 1"), ArtifactInput("a.yaml", "a: 1")))
    assert [a.path for a in request.artifacts] == ["a.yaml", "b.yaml"]
    assert request.artifacts[0].content_hash == ArtifactInput("x.yaml", "a: 1").content_hash  # by content


# --- runs and review -------------------------------------------------------------------------------


def run() -> DiscoveryRun:
    return DiscoveryRun(uuid.UUID(int=1), uuid.UUID(int=2), RunStatus.PENDING, ADA, AT)


def test_the_run_lifecycle() -> None:
    finished = run().start(AT).finish(result(), AT)
    assert finished.status is RunStatus.COMPLETED
    noted = run().start(AT).finish(result(artifacts=(artifact(ArtifactStatus.PARTIAL),)), AT)
    assert noted.status is RunStatus.COMPLETED_WITH_WARNINGS
    error = RunError("malformed_input", "No artifact could be read.")
    failed = run().start(AT).fail(error, AT)
    assert (failed.status, failed.error) == (RunStatus.FAILED, error)
    assert run().cancel(AT).status is RunStatus.CANCELLED
    with pytest.raises(InvalidDiscoveryTransition):
        finished.cancel(AT)  # a finished run stays finished
    with pytest.raises(InvalidDiscoveryTransition):
        run().finish(result(), AT)  # it starts first


def decision(**fields: Any) -> ReviewDecision:
    base: dict[str, Any] = {
        "subject_type": SubjectType.ENTITY, "subject": DB, "decision": Decision.ACCEPTED,
        "user_id": ADA, "at": AT,
    }  # fmt: skip
    return ReviewDecision(**(base | fields))


def test_review_is_recorded_per_item_and_the_latest_applies() -> None:
    finished = run().start(AT).finish(result(entities=(entity(), entity(DB, ambiguous()))), AT)
    with pytest.raises(InvalidDiscoveryRequest) as error:
        finished.decide(decision(component_id="databases/redis"))
    assert error.value.details["reason"] == "not_a_candidate"
    with pytest.raises(InvalidDiscoveryRequest):
        finished.decide(decision(subject="kubernetes:shop/deployment/ghost"))
    with pytest.raises(InvalidDiscoveryRequest):
        decision(
            decision=Decision.REJECTED, component_id="databases/mysql"
        )  # a component only when accepting
    reviewed = finished.decide(decision(component_id="databases/mysql")).decide(
        decision(decision=Decision.REJECTED)
    )
    latest = reviewed.decision_for(DB)
    assert latest is not None
    assert latest.decision is Decision.REJECTED
    assert len(reviewed.decisions) == 2  # history kept
    assert reviewed.decision_for(API) is None  # still pending
    with pytest.raises(InvalidDiscoveryTransition):
        run().decide(decision())  # only a finished, successful run is reviewed
