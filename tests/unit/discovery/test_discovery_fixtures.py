"""Discovery fixtures (Discovery Engine, phase 9): the fifteen fixture kinds the requirements name, each
run end to end through the engine — parsing, normalization, catalog mapping, relationships, proposal
and validation — with what it must and must not produce."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.serialization import content_hash, to_dict
from core.domain.discovery.errors import InvalidDiscoveryRequest, InvalidDiscoveryResult
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import MAX_ARTIFACT_BYTES, ArtifactInput, DiscoveryRequest, DiscoveryRun
from core.domain.discovery.serialization import result_from_dict
from core.domain.discovery.values import (
    ArtifactStatus,
    ElementStatus,
    MappingStatus,
    RelationshipStatus,
    RunStatus,
    Severity,
    Verification,
)
from engines.discovery import engine as engine_module
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.discovery.loading import MAX_DOCUMENTS
from persistence.component_catalog import default_catalog
from tests.unit.discovery.test_discovery_sources import COMPOSE, DEPLOYMENT, TERRAFORM
from tests.unit.migration.test_migration_changes import SOURCE_IR

ENGINE = DeterministicDiscoveryEngine(default_catalog())
AT = datetime(2026, 10, 2, tzinfo=UTC)

SHOP = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: orders, namespace: shop}
spec:
  replicas: 2
  template:
    metadata: {labels: {app: orders}}
    spec:
      containers:
        - name: orders
          image: ghcr.io/acme/orders:3.1
          env: [{name: CACHE_URL, value: "redis://cache.shop.svc:6379"}]
---
apiVersion: v1
kind: Service
metadata: {name: orders, namespace: shop}
spec: {selector: {app: orders}, ports: [{port: 80}]}
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: cache, namespace: shop}
spec:
  replicas: 1
  template:
    metadata: {labels: {app: cache}}
    spec: {containers: [{name: cache, image: "redis:7.2"}]}
---
apiVersion: v1
kind: Service
metadata: {name: cache, namespace: shop}
spec: {selector: {app: cache}, ports: [{port: 6379}]}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: edge, namespace: shop}
spec: {rules: [{host: shop.example.com, http: {paths: [{path: /, backend: {service: {name: orders}}}]}}]}
"""
QUEUE = {
    "resource": {"aws_sqs_queue": {"jobs": {"name": "jobs"}}, "aws_s3_bucket": {"media": {"bucket": "m"}}}
}
POD = """\
apiVersion: v1
kind: Pod
metadata: {name: both}
spec: {containers: [{name: a, image: redis}, {name: b, image: postgres}]}
"""


def discover(*artifacts: tuple[str, str]) -> DiscoveryResult:
    return ENGINE.discover(DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts)))


def status_of(result: DiscoveryResult) -> RunStatus:
    run = DiscoveryRun(uuid.UUID(int=1), uuid.UUID(int=2), RunStatus.PENDING, uuid.UUID(int=3), AT)
    return run.start(AT).finish(result, AT).status


def entity(result: DiscoveryResult, key: str) -> Any:
    return next(e for e in result.entities if e.key == key)


def codes(result: DiscoveryResult) -> set[str]:
    return {d.code for d in result.diagnostics}


def test_1_a_valid_source_with_several_component_types() -> None:
    result = discover(
        ("k8s/shop.yaml", SHOP), ("infra.tf.json", json.dumps(QUEUE)), ("compose.yaml", COMPOSE)
    )
    kinds = {e.key: e.kind for e in result.entities if e.kind is not None}
    assert kinds["terraform:aws_sqs_queue.jobs"] is NodeKind.QUEUE
    assert kinds["terraform:aws_s3_bucket.media"] is NodeKind.STORAGE
    assert kinds["compose:shop/service/db"] is NodeKind.DATABASE
    assert kinds["kubernetes:shop/ingress/edge"] is NodeKind.GATEWAY
    assert all(a.status is ArtifactStatus.PARSED for a in result.artifacts)


def test_2_explicit_references_become_relationships_with_evidence() -> None:
    result = discover(("k8s/shop.yaml", SHOP))
    resolved = {(r.source, r.target) for r in result.relationships if r.status is RelationshipStatus.RESOLVED}
    assert ("kubernetes:shop/deployment/orders", "kubernetes:shop/service/cache") in resolved  # cluster DNS
    assert ("kubernetes:shop/service/orders", "kubernetes:shop/deployment/orders") in resolved  # selector
    assert ("kubernetes:shop/ingress/edge", "kubernetes:shop/service/orders") in resolved
    findings = {f.id for f in result.findings}
    assert all(r.finding_ids and set(r.finding_ids) <= findings for r in result.relationships)


def test_3_unresolved_references_stay_explicit() -> None:
    result = discover(("k8s/shop.yaml", DEPLOYMENT))
    unresolved = [r for r in result.relationships if r.status is RelationshipStatus.UNRESOLVED]
    assert {r.reference for r in unresolved} >= {"configmap/api-config", "host/db.shop.svc"}
    assert all(r.reason and r.target is None for r in unresolved)
    assert {r.id for r in unresolved} <= set(result.unresolved)
    assert result.proposed is None or not result.proposed.connections  # never an IR connection


def test_4_an_ambiguous_mapping_waits_for_a_person() -> None:
    found = entity(discover(("pod.yaml", POD)), "kubernetes:pod/both")
    assert found.mapping.status is MappingStatus.AMBIGUOUS
    assert (found.mapping.component_id, found.kind) == (None, None)  # nothing is chosen


def test_5_unsupported_resource_types_are_reported_not_fabricated() -> None:
    crd = "apiVersion: example.com/v1\nkind: Widget\nmetadata: {name: w}\nspec: {size: 3}\n"
    cloudflare = {"resource": {"cloudflare_record": {"www": {"name": "www"}}}}
    result = discover(
        ("crd.yaml", crd), ("dns.tf.json", json.dumps(cloudflare)), ("main.tf", 'resource "x" "y" {}')
    )
    assert {"unsupported_kind", "hcl_not_supported"} <= codes(result)
    assert not any(e.key.endswith("/widget/w") for e in result.entities)  # never an entity
    assert entity(result, "terraform:cloudflare_record.www").mapping.status is MappingStatus.UNSUPPORTED
    assert {a.path: a.status for a in result.artifacts}["main.tf"] is ArtifactStatus.UNSUPPORTED


def test_6_missing_configuration_stays_unknown() -> None:
    result = discover(("compose.yaml", "name: shop\nservices:\n  db:\n    image: postgres:16\n"))
    assert result.proposed is not None
    db = result.proposed.node("compose:shop/service/db")
    assert db is not None
    assert dict(db.configuration.values) == {}  # no default is filled in
    unknown = [
        v for v in result.validation if v.element_id == db.id and v.code == "constraint_cannot_evaluate"
    ]
    assert unknown
    assert all(v.severity is Severity.INFO for v in unknown)


def test_7_malformed_syntax_fails_safely_and_spares_the_rest() -> None:
    result = discover(("broken.yaml", "kind: [unclosed\n"), ("compose.yaml", COMPOSE))
    statuses = {a.path: a.status for a in result.artifacts}
    assert (statuses["broken.yaml"], statuses["compose.yaml"]) == (
        ArtifactStatus.FAILED,
        ArtifactStatus.PARSED,
    )
    assert "malformed_yaml" in codes(result)
    assert any(e.key.startswith("compose:") for e in result.entities)
    assert status_of(result) is RunStatus.COMPLETED_WITH_WARNINGS


def test_8_duplicate_and_conflicting_identifiers_are_reported_not_resolved() -> None:
    other = DEPLOYMENT.replace("replicas: 3", "replicas: 5")
    result = discover(("a.yaml", DEPLOYMENT), ("b.yaml", other))
    assert {"duplicate_definition", "conflicting_values"} <= codes(result)
    api = entity(result, "kubernetes:shop/deployment/api")
    replicas = next(c for c in api.configuration if c.property == "replicas")
    assert (replicas.valid, replicas.value) == (False, None)  # neither declaration is chosen


def test_9_sensitive_values_are_redacted_everywhere() -> None:
    result = discover(
        ("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE), ("main.tf.json", json.dumps(TERRAFORM))
    )
    text = json.dumps(result.to_dict(), default=str)
    for secret in ("hunter2", "pa55word", "s3cr3t", "sk_live_do_not_keep"):
        assert secret not in text
    password = next(f for f in result.findings if f.source_property == "password")
    assert (password.redacted, password.value) == (True, None)


def test_10_sources_beyond_the_limits_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(InvalidDiscoveryRequest):
        ArtifactInput("big.yaml", "a" * (MAX_ARTIFACT_BYTES + 1))
    many_documents = discover(("many.yaml", "---\na: 1\n" * (MAX_DOCUMENTS + 1)))
    assert "too_many_documents" in codes(many_documents)
    monkeypatch.setattr(engine_module, "MAX_ENTITIES", 3)
    with pytest.raises(InvalidDiscoveryRequest) as refused:
        discover(("k8s/shop.yaml", SHOP))
    assert refused.value.details["reason"] == "too_many_entities"  # refused, never truncated


def test_11_invalid_architecture_references_are_refused_by_the_ir() -> None:
    document = to_dict(SOURCE_IR)
    source = document["nodes"][0]["id"]
    document["connections"].append(
        {"id": "dangling", "source_id": source, "target_id": "nowhere", "kind": "request"}
    )
    result = discover(("shop.json", json.dumps(document)))
    assert result.artifacts[0].status is ArtifactStatus.FAILED
    errors = [d for d in result.diagnostics if d.code == "invalid_architecture"]
    assert errors
    assert all(d.severity is Severity.ERROR for d in errors)
    assert (result.entities, result.proposed) == ((), None)  # nothing is repaired


def test_12_partial_findings_complete_with_warnings() -> None:
    result = discover(("compose.yaml", COMPOSE + "include: [other.yaml]\n"))
    assert result.artifacts[0].status is ArtifactStatus.PARTIAL
    assert "include_not_read" in codes(result)
    assert entity(result, "compose:shop/service/db").kind is NodeKind.DATABASE  # what was read is kept
    assert status_of(result) is RunStatus.COMPLETED_WITH_WARNINGS


def test_13_a_proposal_leaves_the_canonical_architecture_unchanged() -> None:
    before = content_hash(SOURCE_IR)
    result = discover(("shop.json", json.dumps(to_dict(SOURCE_IR))))
    ENGINE.propose(result, ())
    assert content_hash(SOURCE_IR) == before
    assert result.proposed is not None
    assert {n.id for n in result.proposed.nodes} == {n.id for n in SOURCE_IR.nodes}


def test_14_repeated_identical_input_gives_equivalent_results() -> None:
    artifacts = (("k8s/shop.yaml", SHOP), ("compose.yaml", COMPOSE), ("main.tf.json", json.dumps(TERRAFORM)))
    first, again = discover(*artifacts), discover(*reversed(artifacts))
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    assert [e.id for e in first.entities] == [e.id for e in again.entities]
    assert [r.id for r in first.relationships] == [r.id for r in again.relationships]
    assert first.proposed is not None
    assert again.proposed is not None
    assert content_hash(first.proposed) == content_hash(again.proposed)


def test_15_declared_desired_state_is_never_reported_as_verified_runtime() -> None:
    result = discover(("k8s/shop.yaml", SHOP), ("compose.yaml", COMPOSE))
    assert {f.verification for f in result.findings} <= {Verification.OBSERVED, Verification.UNSUPPORTED}
    assert result.proposed is not None
    for node in result.proposed.nodes:
        provenances = [node.provenance, *node.field_provenance.values()]
        assert all(p is not None and not p.verified for p in provenances)
    for connection in result.proposed.connections:
        assert connection.provenance is not None
        assert not connection.provenance.verified
    included = [e for e in result.elements if e.status is ElementStatus.INCLUDED]
    assert all(e.origin is not Verification.USER_PROVIDED for e in included)  # nobody has confirmed it


def test_a_stored_result_reads_back_exactly_and_tampering_is_refused() -> None:
    result = discover(("k8s/shop.yaml", SHOP), ("compose.yaml", COMPOSE))
    stored = json.loads(json.dumps(result.to_dict()))
    assert result_from_dict(stored, result.fingerprint).to_dict() == result.to_dict()
    tampered = stored | {"entities": stored["entities"][1:]}
    with pytest.raises(InvalidDiscoveryResult):
        result_from_dict(tampered, result.fingerprint)
    with pytest.raises(InvalidDiscoveryResult):  # another result version is not read as this one
        result_from_dict(stored | {"version": 2}, result.fingerprint)
