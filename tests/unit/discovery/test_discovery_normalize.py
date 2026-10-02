"""Normalization and entity extraction (Discovery Engine, phase 3): one entity per declared resource,
its role and IR node kind set only where the source's semantics establish them (unknown otherwise),
supporting resources kept apart from components, every value traced to its finding, duplicates and
conflicts reported rather than resolved, deterministic output."""

import json

from core.architecture_ir.component import NodeKind
from core.architecture_ir.serialization import to_dict
from core.domain.discovery.values import EntityRole, SourceType, Verification
from engines.discovery.normalize import Normalization, NormalizedEntity, normalize
from tests.unit.discovery.test_discovery_sources import COMPOSE, DEPLOYMENT, TERRAFORM, read
from tests.unit.migration.test_migration_changes import SOURCE_IR

JOBS = """\
apiVersion: batch/v1
kind: CronJob
metadata: {name: nightly, namespace: shop}
spec:
  schedule: "0 3 * * *"
  jobTemplate: {spec: {template: {spec: {containers: [{name: j, image: busybox}]}}}}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: web, namespace: shop}
spec: {rules: [{host: shop.example.com, http: {paths: [{path: /, backend: {service: {name: api}}}]}}]}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: data, namespace: shop}
spec: {resources: {requests: {storage: 10Gi}}}
"""


def entities(*artifacts: tuple[str, str]) -> dict[str, NormalizedEntity]:
    return {e.key: e for e in normalize(read(*artifacts)).entities}


def test_kinds_are_set_only_where_the_source_establishes_them() -> None:
    found = entities(("k8s/shop.yaml", DEPLOYMENT), ("k8s/jobs.yaml", JOBS))
    deployment = found["kubernetes:shop/deployment/api"]
    assert (deployment.role, deployment.kind, deployment.kind_rule) == (
        EntityRole.COMPONENT,
        None,
        None,
    )  # unknown
    cron = found["kubernetes:shop/cronjob/nightly"]
    assert (cron.kind, cron.kind_rule) == (NodeKind.WORKER, "kubernetes-kinds@1")
    assert found["kubernetes:shop/ingress/web"].kind is NodeKind.GATEWAY
    roles = {key: e.role for key, e in found.items()}
    assert roles["kubernetes:shop/service/api"] is EntityRole.ROUTING
    assert roles["kubernetes:shop/secret/api-secrets"] is EntityRole.CONFIGURATION
    assert roles["kubernetes:shop/persistentvolumeclaim/data"] is EntityRole.VOLUME


def test_terraform_kinds_come_from_a_table_of_unambiguous_types() -> None:
    found = entities(("main.tf.json", json.dumps(TERRAFORM)))
    database = found["terraform:aws_db_instance.main"]
    assert (database.kind, database.kind_rule, database.resource_type) == (
        NodeKind.DATABASE, "terraform-kinds@1", "aws_db_instance",
    )  # fmt: skip
    instance = found["terraform:aws_instance.web"]
    assert (instance.role, instance.kind) == (EntityRole.COMPONENT, None)  # a VM: what it runs is unknown
    vpc = {"resource": {"aws_vpc": {"main": {"cidr_block": "10.0.0.0/16"}}}}
    assert entities(("net.tf.json", json.dumps(vpc)))["terraform:aws_vpc.main"].role is EntityRole.NETWORK


def test_compose_services_volumes_and_networks() -> None:
    found = entities(("compose.yaml", COMPOSE))
    api = found["compose:shop/service/api"]
    assert (api.name, api.resource_type, api.namespace, api.kind) == ("api", "service", "shop", None)
    assert found["compose:shop/volume/data"].role is EntityRole.VOLUME


def test_an_exported_architecture_keeps_its_kinds() -> None:
    found = entities(("shop.json", json.dumps(to_dict(SOURCE_IR))))
    assert (found["db"].kind, found["db"].kind_rule) == (NodeKind.DATABASE, "architecture-json@1")
    assert found["db"].source_type is SourceType.ARCHITECTURE_JSON


def test_every_value_points_at_its_finding() -> None:
    deployment = entities(("k8s/shop.yaml", DEPLOYMENT))["kubernetes:shop/deployment/api"]
    replicas = deployment.value("replicas")
    assert replicas is not None
    assert (replicas.value, replicas.verification, replicas.source_property) == (
        3, Verification.OBSERVED, "spec.replicas",
    )  # fmt: skip
    assert replicas.finding_id in deployment.finding_ids
    assert replicas.location.reference == "k8s/shop.yaml#0:spec.replicas"


def test_duplicates_and_conflicts_are_reported_not_resolved() -> None:
    other = DEPLOYMENT.replace("replicas: 3", "replicas: 5")
    normalization: Normalization = normalize(read(("a.yaml", DEPLOYMENT), ("b.yaml", other)))
    assert {"duplicate_definition", "conflicting_values"} <= {d.code for d in normalization.diagnostics}
    deployment = next(e for e in normalization.entities if e.key == "kubernetes:shop/deployment/api")
    assert deployment.value("replicas") is None  # neither value is chosen
    assert sorted(v.value for v in deployment.values("replicas")) == [3, 5]


def test_normalization_is_deterministic() -> None:
    first = normalize(read(("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE)))
    again = normalize(read(("compose.yaml", COMPOSE), ("k8s/shop.yaml", DEPLOYMENT)))
    assert [(e.key, e.role, e.kind, dict(e.properties)) for e in first.entities] == [
        (e.key, e.role, e.kind, dict(e.properties)) for e in again.entities
    ]
