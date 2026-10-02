"""Component catalog and configuration mapping (Discovery Engine, phase 4): entities mapped to the
existing catalog by versioned rules — exact only where the source names the component, inferred from
images, ambiguous or unmapped with a reason otherwise — and declared configuration mapped to IR
properties by exact unit conversions, validated by the IR's own specifications, never defaulted."""

import json

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.serialization import to_dict
from core.domain.discovery.findings import CandidateEntity
from core.domain.discovery.values import EntityRole, MappingStatus, Verification
from engines.discovery.mapping import (
    CATALOG_RULE,
    DOCKER_BYTES,
    KUBERNETES_BYTES,
    byte_count,
    candidates,
    image_name,
    kubernetes_cpu,
)
from engines.discovery.normalize import normalize
from persistence.component_catalog import default_catalog
from tests.unit.discovery.test_discovery_sources import COMPOSE, DEPLOYMENT, PROBED, SHOWN, TERRAFORM, read
from tests.unit.migration.test_migration_changes import SOURCE_IR

M = MappingStatus
CATALOG = default_catalog()


def mapped(*artifacts: tuple[str, str]) -> dict[str, CandidateEntity]:
    return {c.key: c for c in candidates(normalize(read(*artifacts)).entities, CATALOG)}


def configuration(entity: CandidateEntity) -> dict[str, object]:
    return {c.property: c.value if c.valid else c.problem for c in entity.configuration}


def test_terraform_types_map_by_table_and_engine() -> None:
    found = mapped(("main.tf.json", json.dumps(TERRAFORM)))
    database = found["terraform:aws_db_instance.main"]
    assert (database.mapping.status, database.mapping.component_id) == (M.MAPPED, "databases/postgresql")
    assert (database.kind, database.technology) == (NodeKind.DATABASE, "postgresql")
    web = found["terraform:aws_instance.web"]
    assert (web.mapping.component_id, web.kind) == (
        "compute/virtual-machine",
        None,
    )  # service or worker: unknown
    queue = {"resource": {"aws_sqs_queue": {"jobs": {"name": "jobs"}}}}
    sqs = mapped(("q.tf.json", json.dumps(queue)))["terraform:aws_sqs_queue.jobs"]
    assert (sqs.mapping.status, sqs.mapping.component_id, sqs.kind) == (
        M.EXACT_MATCH,
        "messaging/aws-sqs",
        NodeKind.QUEUE,
    )


def test_an_engine_not_stated_literally_stays_unmapped() -> None:
    body = {"resource": {"aws_db_instance": {"a": {"engine": "${var.engine}"}, "b": {"engine": "oracle-ee"}}}}
    found = mapped(("db.tf.json", json.dumps(body)))
    for key in ("terraform:aws_db_instance.a", "terraform:aws_db_instance.b"):
        assert found[key].mapping.status is M.UNMAPPED
        assert found[key].mapping.reason
        assert found[key].kind is NodeKind.DATABASE  # the type still establishes the kind


def test_terraform_show_maps_modules_and_caches() -> None:
    found = mapped(("plan.json", json.dumps(SHOWN)))
    cache = found["terraform:module.cache.aws_elasticache_cluster.this.0"]
    assert (cache.mapping.component_id, cache.kind) == ("databases/redis", NodeKind.CACHE)


def test_images_are_evidence_not_proof() -> None:
    found = mapped(("compose.yaml", COMPOSE))
    db = found["compose:shop/service/db"]
    assert (db.mapping.status, db.mapping.component_id, db.mapping.rule) == (
        M.MAPPED,
        "databases/postgresql",
        CATALOG_RULE,
    )
    assert (db.kind, db.kind_rule, db.technology, db.technology_version) == (
        NodeKind.DATABASE, CATALOG_RULE, "postgresql", "16",
    )  # fmt: skip
    api = found["compose:shop/service/api"]
    assert (api.mapping.status, api.kind) == (M.UNMAPPED, None)
    assert "Built from source" in (api.mapping.reason or "")
    web = found["compose:shop/service/web"]
    assert web.mapping.status is M.UNMAPPED  # nginx: not in the catalog
    assert (
        found["compose:shop/volume/data"].mapping.reason == "A supporting resource (volume), not a component."
    )


def test_containers_mapping_to_different_components_are_ambiguous() -> None:
    pod = """\
apiVersion: v1
kind: Pod
metadata: {name: both, namespace: x}
spec: {containers: [{name: a, image: "redis:7"}, {name: b, image: "docker.io/library/postgres:16"}]}
"""
    both = mapped(("pod.yaml", pod))["kubernetes:x/pod/both"]
    assert both.mapping.status is M.AMBIGUOUS
    assert both.mapping.candidates == ("databases/postgresql", "databases/redis")
    assert (both.kind, both.technology, both.technology_version) == (None, None, None)


def test_a_stated_kind_the_component_does_not_describe_is_not_mapped() -> None:
    job = """\
apiVersion: batch/v1
kind: Job
metadata: {name: seed, namespace: x}
spec: {template: {spec: {containers: [{name: s, image: "postgres:16"}]}}}
"""
    seed = mapped(("job.yaml", job))["kubernetes:x/job/seed"]
    assert (seed.kind, seed.mapping.status) == (NodeKind.WORKER, M.UNMAPPED)
    assert seed.mapping.reason == "databases/postgresql describes database, not a worker."


def test_an_exported_architecture_maps_its_declared_components() -> None:
    found = mapped(("shop.json", json.dumps(to_dict(SOURCE_IR))))
    for node in SOURCE_IR.nodes:
        entity = found[node.id]
        if node.component:
            assert (entity.mapping.status, entity.mapping.component_id) == (M.EXACT_MATCH, node.component)
        assert entity.kind is node.kind


def test_kubernetes_resources_convert_exactly() -> None:
    api = mapped(("k8s/shop.yaml", DEPLOYMENT))["kubernetes:shop/deployment/api"]
    assert configuration(api) == {
        "replicas": 3,
        "cpu_request_cores": "0.25",
        "memory_request_bytes": 256 * 2**20,
        "memory_limit_bytes": 512 * 2**20,
    }
    replicas = next(c for c in api.configuration if c.property == "replicas")
    assert (replicas.source_property, replicas.verification) == ("spec.replicas", Verification.OBSERVED)
    assert replicas.finding_id in api.finding_ids


def test_compose_and_terraform_configuration() -> None:
    api = mapped(("compose.yaml", COMPOSE))["compose:shop/service/api"]
    assert configuration(api) == {
        "replicas": 2, "cpu_limit_cores": "0.5", "memory_limit_bytes": 512 * 2**20, "health_check": True,
    }  # fmt: skip
    database = mapped(("main.tf.json", json.dumps(TERRAFORM)))["terraform:aws_db_instance.main"]
    assert configuration(database) == {"instance_class": "db.t3.micro"}  # the password is never mapped
    storage = {"resource": {"aws_db_instance": {"s": {"engine": "mysql", "allocated_storage": 20}}}}
    sized = mapped(("s.tf.json", json.dumps(storage)))["terraform:aws_db_instance.s"]
    assert configuration(sized) == {"storage_bytes": 20 * 2**30}


def test_invalid_conflicting_or_inapplicable_values_are_kept_with_a_reason() -> None:
    bad = DEPLOYMENT.replace("cpu: 250m", "cpu: lots").replace("replicas: 3", "replicas: -1")
    api = mapped(("k8s/shop.yaml", bad))["kubernetes:shop/deployment/api"]
    problems = {c.property: c for c in api.configuration}
    assert not problems["cpu_request_cores"].valid
    assert problems["cpu_request_cores"].value is None
    assert "at least" in (problems["replicas"].problem or "")
    two = DEPLOYMENT.replace(
        "      volumes:",
        "        - {name: side, image: busybox, resources: {requests: {cpu: 100m}}}\n      volumes:",
    )
    sidecar = mapped(("k8s/shop.yaml", two))["kubernetes:shop/deployment/api"]
    cpu = next(c for c in sidecar.configuration if c.property == "cpu_request_cores")
    assert not cpu.valid
    assert "more than once" in (cpu.problem or "")  # no per-instance total is invented
    bucket = {"resource": {"aws_s3_bucket": {"b": {"instance_class": "x"}}}}
    s3 = mapped(("b.tf.json", json.dumps(bucket)))["terraform:aws_s3_bucket.b"]
    assert configuration(s3) == {"instance_class": "instance_class does not apply to a storage."}


def test_nothing_is_defaulted() -> None:
    bare = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: q}\n"
        "spec: {template: {spec: {containers: [{name: q, image: redis}]}}}\n"
    )
    q = mapped(("q.yaml", bare))["kubernetes:deployment/q"]
    assert q.configuration == ()
    assert (q.mapping.component_id, q.kind, q.technology_version) == (
        "databases/redis",
        None,
        None,
    )  # cache or database


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("postgres", ("postgres", None)),
        ("ghcr.io/acme/postgres:16.2-alpine", ("postgres", "16.2-alpine")),
        ("localhost:5000/redis:7@sha256:abc", ("redis", "7")),
        ("confluentinc/cp-kafka", ("cp-kafka", None)),
    ],
)
def test_image_names(reference: str, expected: tuple[str, str | None]) -> None:
    assert image_name(reference) == expected


def test_quantities() -> None:
    assert kubernetes_cpu("250m") == kubernetes_cpu("0.25")
    assert kubernetes_cpu("abc") is None
    assert byte_count("1Gi", KUBERNETES_BYTES) == 2**30
    assert byte_count("1G", KUBERNETES_BYTES) == 10**9
    assert byte_count("0.5Ki", KUBERNETES_BYTES) == 512
    assert byte_count("0.3", KUBERNETES_BYTES) is None  # not a whole number of bytes
    assert byte_count("1gb", DOCKER_BYTES) == 2**30
    assert byte_count("1Zi", KUBERNETES_BYTES) is None


def test_mapping_is_deterministic() -> None:
    first = candidates(
        normalize(read(("compose.yaml", COMPOSE), ("k8s/shop.yaml", DEPLOYMENT))).entities, CATALOG
    )
    again = candidates(
        normalize(read(("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE))).entities, CATALOG
    )
    assert [c.to_dict() for c in first] == [c.to_dict() for c in again]


def test_each_mapping_keeps_the_value_as_written_and_the_conversion() -> None:
    api = mapped(("k8s/shop.yaml", DEPLOYMENT))["kubernetes:shop/deployment/api"]
    cpu = next(c for c in api.configuration if c.property == "cpu_request_cores")
    assert (cpu.source_value, cpu.value) == ("250m", "0.25")
    assert cpu.transformation == "Kubernetes CPU quantity to cores (m = 1/1000)"
    replicas = next(c for c in api.configuration if c.property == "replicas")
    assert (replicas.source_value, replicas.transformation) == (3, None)  # nothing converted


def test_probes_and_reservations_map_to_ir_properties() -> None:
    web = mapped(("k8s/web.yaml", PROBED))["kubernetes:shop/deployment/web"]
    assert configuration(web)["health_check"] is True
    compose = (
        "name: shop\nservices:\n  api:\n    image: redis:7\n"
        "    deploy: {resources: {reservations: {cpus: '0.25', memory: 128M}}}\n"
    )
    api = mapped(("compose.yaml", compose))["compose:shop/service/api"]
    assert configuration(api) == {"cpu_request_cores": "0.25", "memory_request_bytes": 128 * 2**20}


def test_an_uncovered_provider_is_unsupported_and_utilities_are_not_components() -> None:
    body = {
        "resource": {
            "cloudflare_record": {"www": {"name": "www"}},
            "aws_kinesis_stream": {"events": {"name": "events"}},
            "random_password": {"db": {"length": 32}},
        }
    }
    found = mapped(("x.tf.json", json.dumps(body)))
    record = found["terraform:cloudflare_record.www"].mapping
    assert (record.status, record.reason) == (
        M.UNSUPPORTED,
        "The cloudflare provider is not covered by discovery-catalog@1.",
    )
    assert found["terraform:aws_kinesis_stream.events"].mapping.status is M.UNMAPPED  # covered: no component
    password = found["terraform:random_password.db"]
    assert (password.role, password.kind) == (EntityRole.CONFIGURATION, None)
