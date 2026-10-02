"""Relationship discovery (Discovery Engine, phase 5): explicit references resolved to entities of the
same discovery — names, selectors, cluster DNS, Terraform addresses, IR connections — or kept
unresolved with the reason; a connection kind only where the source's semantics establish it; never
across formats; deterministic."""

import json

from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.serialization import to_dict
from core.domain.discovery.findings import CandidateRelationship
from core.domain.discovery.values import RelationshipStatus
from engines.discovery.normalize import normalize
from engines.discovery.relationships import DEPENDS_ON, relationships
from tests.unit.discovery.test_discovery_normalize import JOBS
from tests.unit.discovery.test_discovery_sources import COMPOSE, DEPLOYMENT, TERRAFORM, read
from tests.unit.migration.test_migration_changes import SOURCE_IR

R, U = RelationshipStatus.RESOLVED, RelationshipStatus.UNRESOLVED
DATABASE_SERVICE = """\
apiVersion: v1
kind: Service
metadata: {name: db, namespace: shop}
spec: {ports: [{port: 5432}]}
"""


def discovered(*artifacts: tuple[str, str]) -> list[CandidateRelationship]:
    extraction = read(*artifacts)
    return list(relationships(extraction, normalize(extraction).entities))


def by_reference(found: list[CandidateRelationship], source: str) -> dict[str, CandidateRelationship]:
    return {r.reference: r for r in found if r.source == source}


def test_kubernetes_names_selectors_and_ingress_routes() -> None:
    found = discovered(("k8s/shop.yaml", DEPLOYMENT), ("k8s/jobs.yaml", JOBS))
    api = by_reference(found, "kubernetes:shop/deployment/api")
    secret = api["secret/api-secrets"]
    assert (secret.status, secret.target) == (R, "kubernetes:shop/secret/api-secrets")
    assert api["configmap/api-config"].status is U
    assert api["configmap/api-config"].reason == "configmap/api-config is not declared in these artifacts."
    database = api["host/db.shop.svc"]  # cluster DNS for a Service these artifacts do not declare
    assert (database.status, database.reason) == (U, "db.shop.svc is not declared in these artifacts.")
    service = by_reference(found, "kubernetes:shop/service/api")["selector:app=api"]
    assert (service.status, service.target, service.kind) == (R, "kubernetes:shop/deployment/api", None)
    ingress = by_reference(found, "kubernetes:shop/ingress/web")["service/api"]
    assert (ingress.target, ingress.kind) == ("kubernetes:shop/service/api", ConnectionKind.REQUEST)


def test_cluster_dns_resolves_to_the_service_it_names() -> None:
    found = discovered(("k8s/shop.yaml", DEPLOYMENT), ("k8s/db.yaml", DATABASE_SERVICE))
    host = by_reference(found, "kubernetes:shop/deployment/api")["host/db.shop.svc"]
    assert (host.status, host.target, host.kind) == (R, "kubernetes:shop/service/db", None)  # kind unknown


def test_a_selector_matching_several_workloads_is_not_resolved() -> None:
    copy = DEPLOYMENT.split("---")[0].replace("name: api, namespace", "name: api2, namespace")
    found = discovered(("k8s/shop.yaml", DEPLOYMENT), ("k8s/copy.yaml", copy))
    service = by_reference(found, "kubernetes:shop/service/api")["selector:app=api"]
    assert service.status is U
    assert "matches 2 workloads" in (service.reason or "")


def test_compose_dependencies_hosts_and_volumes() -> None:
    found = discovered(("compose.yaml", COMPOSE))
    web = by_reference(found, "compose:shop/service/web")["service/api"]
    assert (web.target, web.kind, web.reason) == (
        "compose:shop/service/api", ConnectionKind.DEPENDENCY, DEPENDS_ON,
    )  # fmt: skip
    api = by_reference(found, "compose:shop/service/api")["host/db"]
    assert (api.status, api.target, api.kind) == (R, "compose:shop/service/db", None)
    db = by_reference(found, "compose:shop/service/db")["volume/data"]
    assert db.target == "compose:shop/volume/data"


def test_terraform_addresses_and_depends_on() -> None:
    found = discovered(("main.tf.json", json.dumps(TERRAFORM)))
    web = [r for r in found if r.source == "terraform:aws_instance.web"]
    assert {(r.target, r.kind) for r in web} == {
        ("terraform:aws_db_instance.main", None),  # an interpolation: the kind is unknown
        ("terraform:aws_db_instance.main", ConnectionKind.DEPENDENCY),
    }
    assert len({r.id for r in web}) == 2  # one per place it is written


def _shown(references: dict[str, list[str]]) -> str:
    resources = [
        {"address": f"aws_instance.web[{i}]", "mode": "managed", "type": "aws_instance", "name": "web",
         "values": {}}
        for i in range(2)
    ] + [
        {"address": f"aws_eip.{n}", "mode": "managed", "type": "aws_eip", "name": n, "values": {}}
        for n in references
    ]  # fmt: skip
    expressions = [
        {"address": f"aws_eip.{n}", "expressions": {"instance": {"references": targets}}}
        for n, targets in references.items()
    ]
    return json.dumps(
        {
            "format_version": "1.0",
            "values": {"root_module": {"resources": resources}},
            "configuration": {"root_module": {"resources": expressions}},
        }
    )


def test_terraform_instances_and_missing_resources() -> None:
    plan = _shown(
        {
            "one": ["aws_instance.web[0].id", "aws_instance.web[0]"],
            "all": ["aws_instance.web"],
            "gone": ["aws_instance.missing"],
        }
    )
    found = discovered(("plan.json", plan))
    one = by_reference(found, "terraform:aws_eip.one")
    assert {r.target for r in one.values()} == {"terraform:aws_instance.web.0"}
    every = by_reference(found, "terraform:aws_eip.all")["aws_instance.web"]
    assert (every.status, every.reason) == (U, "aws_instance.web names 2 instances; none is chosen.")
    assert by_reference(found, "terraform:aws_eip.gone")["aws_instance.missing"].status is U


def test_architecture_connections_keep_their_declared_kind() -> None:
    found = discovered(("shop.json", json.dumps(to_dict(SOURCE_IR))))
    assert len(found) == len(SOURCE_IR.connections)
    for connection in SOURCE_IR.connections:
        relationship = by_reference(found, connection.source_id)[connection.target_id]
        assert (relationship.status, relationship.kind) == (R, connection.kind)


def test_references_are_never_resolved_across_formats() -> None:
    found = discovered(("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE))
    for relationship in found:
        if relationship.target:
            assert relationship.target.split(":", 1)[0] == relationship.source.split(":", 1)[0]


def test_every_relationship_cites_its_finding_and_is_deterministic() -> None:
    extraction = read(("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE))
    ids = {f.id for f in extraction.findings}
    first = discovered(("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE))
    assert all(set(r.finding_ids) <= ids for r in first)
    again = discovered(("compose.yaml", COMPOSE), ("k8s/shop.yaml", DEPLOYMENT))
    assert [r.to_dict() for r in first] == [r.to_dict() for r in again]
