"""Source adapters and safe parsing (Discovery Engine, phase 2): each supported format read into
located findings without executing, evaluating or expanding anything; YAML read faithfully (YAML 1.2
scalars, no aliases, no custom tags); limits enforced; secrets never kept; unsupported formats and
constructs reported, never dropped; deterministic output."""

import json
from typing import Any

import pytest

from core.architecture_ir.serialization import to_dict
from core.domain.discovery.findings import Finding
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest
from core.domain.discovery.values import ArtifactStatus, FindingType, SourceType, Verification
from engines.discovery.adapters import Extraction, host_of, read_artifacts
from engines.discovery.loading import MAX_DEPTH, MAX_DOCUMENTS, parse
from engines.discovery.sources import ADAPTERS
from tests.unit.migration.test_migration_changes import SOURCE_IR

DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: api, namespace: shop, labels: {app: api}}
spec:
  replicas: 3
  template:
    metadata: {labels: {app: api}}
    spec:
      containers:
        - name: api
          image: ghcr.io/acme/api:1.4
          ports: [{containerPort: 8080}]
          resources: {requests: {cpu: 250m, memory: 256Mi}, limits: {memory: 512Mi}}
          env:
            - {name: DATABASE_URL, value: "postgres://app:hunter2@db.shop.svc:5432/app"}
            - {name: API_KEY, valueFrom: {secretKeyRef: {name: api-secrets, key: key}}}
          envFrom: [{configMapRef: {name: api-config}}]
      volumes: [{name: data, persistentVolumeClaim: {claimName: api-data}}]
---
apiVersion: v1
kind: Service
metadata: {name: api, namespace: shop}
spec: {type: ClusterIP, selector: {app: api}, ports: [{port: 80, targetPort: 8080}]}
---
apiVersion: v1
kind: Secret
metadata: {name: api-secrets, namespace: shop}
stringData: {key: "sk_live_do_not_keep"}
"""
COMPOSE = """\
name: shop
services:
  web:
    image: nginx:1.27
    ports: ["80:80"]
    depends_on: [api]
  api:
    build: ./api
    deploy: {replicas: 2, resources: {limits: {cpus: "0.5", memory: 512M}}}
    environment:
      - DATABASE_URL=postgres://u:pa55word@db:5432/shop
    healthcheck: {test: ["CMD", "true"]}
  db:
    image: postgres:16
    volumes: ["data:/var/lib/postgresql/data"]
volumes:
  data: {}
"""
TERRAFORM = {
    "resource": {
        "aws_db_instance": {
            "main": {"engine": "postgres", "instance_class": "db.t3.micro", "password": "s3cr3t"}
        },
        "aws_instance": {
            "web": {
                "ami": "ami-1",
                "user_data": "${aws_db_instance.main.address}",
                "depends_on": ["aws_db_instance.main"],
            }
        },
    },
    "module": {"network": {"source": "./network"}},
    "variable": {"region": {"default": "eu-west-1"}},
}
SHOWN = {
    "format_version": "1.0",
    "values": {
        "root_module": {
            "resources": [
                {"address": "aws_db_instance.main", "mode": "managed", "type": "aws_db_instance",
                 "name": "main",
                 "values": {"engine": "postgres", "password": "s3cr3t", "multi_az": True},
                 "sensitive_values": {"password": True}},
            ],
            "child_modules": [
                {"resources": [{"address": "module.cache.aws_elasticache_cluster.this[0]", "mode": "managed",
                                "type": "aws_elasticache_cluster", "name": "this",
                                "values": {"engine": "redis"}}]},
            ],
        }
    },
}  # fmt: skip


def read(*artifacts: tuple[str, str], source_type: SourceType | None = None) -> Extraction:
    request = DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts), source_type)
    return read_artifacts(request, ADAPTERS)


def found(extraction: Extraction, entity: str, kind: FindingType | None = None) -> list[Finding]:
    return [f for f in extraction.findings if f.entity == entity and (kind is None or f.type is kind)]


def properties(extraction: Extraction, entity: str) -> dict[str, Any]:
    return {f.property or "": f.value for f in found(extraction, entity, FindingType.PROPERTY)}


def targets(extraction: Extraction, entity: str) -> set[str]:
    return {f.target or "" for f in found(extraction, entity, FindingType.REFERENCE)}


# --- safe parsing ----------------------------------------------------------------------------------


def test_yaml_is_read_as_written() -> None:
    [document] = parse("c.yaml", "ports: [80:80]\ntty: no\nversion: 2024-01-01\nn: 012\nf: 1.5\n").documents
    assert document.data == {"ports": ["80:80"], "tty": "no", "version": "2024-01-01", "n": "012", "f": 1.5}
    assert document.lines["ports[0]"] == 1


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("a: &x 1\nb: *x\n", "malformed_yaml"),  # aliases (and billion-laughs expansion) are refused
        ("a: !!python/object/apply:os.system [ls]\n", "unsupported_tag"),
        ("a: [1, 2\n", "malformed_yaml"),
        ('{"a": NaN}', "malformed_json"),
        ("[" * (MAX_DEPTH + 5) + "]" * (MAX_DEPTH + 5), "too_deep"),
        ("---\na: 1\n" * (MAX_DOCUMENTS + 1), "too_many_documents"),
    ],
)
def test_hostile_or_malformed_input_is_refused_safely(content: str, code: str) -> None:
    parsed = parse("x.yaml", content)
    assert parsed.failed
    assert [d.code for d in parsed.diagnostics] == [code]


def test_deep_yaml_and_duplicate_keys_are_reported() -> None:
    deep = "".join("  " * i + f"k{i}:\n" for i in range(MAX_DEPTH + 2)) + "  " * (MAX_DEPTH + 2) + "v: 1\n"
    assert parse("deep.yaml", deep).diagnostics[0].code == "too_deep"
    duplicated = parse("d.yaml", "a: 1\na: 2\n")
    assert (duplicated.documents[0].data, duplicated.diagnostics[0].code) == ({"a": 2}, "duplicate_key")


def test_a_connection_string_contributes_only_its_host() -> None:
    assert host_of("postgres://app:hunter2@db.shop.svc:5432/app?sslmode=require") == "db.shop.svc"
    assert host_of("redis:6379") == "redis"
    assert host_of("not a url") is None
    assert host_of("http://user:pass@/nohost") is None


# --- kubernetes ------------------------------------------------------------------------------------


def test_kubernetes_manifests_are_read_as_their_desired_state() -> None:
    extraction = read(("k8s/shop.yaml", DEPLOYMENT))
    api = "kubernetes:shop/deployment/api"
    props = properties(extraction, api)
    assert (props["replicas"], props["image"], props["ports"]) == (3, "ghcr.io/acme/api:1.4", [8080])
    assert (props["cpu_request"], props["memory_limit"]) == ("250m", "512Mi")
    assert targets(extraction, api) == {
        "host/db.shop.svc", "secret/api-secrets", "configmap/api-config", "persistentvolumeclaim/api-data",
    }  # fmt: skip
    assert targets(extraction, "kubernetes:shop/service/api") == {"selector:app=api"}
    assert properties(extraction, "kubernetes:shop/secret/api-secrets")["keys"] == [
        "key"
    ]  # names, never values
    replicas = next(f for f in found(extraction, api) if f.property == "replicas")
    assert replicas.verification is Verification.OBSERVED
    assert (replicas.location.reference, replicas.location.line) == ("k8s/shop.yaml#0:spec.replicas", 5)
    [artifact] = extraction.artifacts
    assert (artifact.status, artifact.source_type, artifact.documents) == (
        ArtifactStatus.PARSED, SourceType.KUBERNETES, 3,
    )  # fmt: skip


def test_unsupported_kinds_and_templates_are_reported() -> None:
    hpa = "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata: {name: api}\n"
    extraction = read(("hpa.yaml", hpa))
    assert [d.code for d in extraction.diagnostics] == ["unsupported_kind"]
    assert [f.type for f in extraction.findings] == [FindingType.UNSUPPORTED]
    assert extraction.artifacts[0].status is ArtifactStatus.PARTIAL
    helm = read(("chart.yaml", "apiVersion: v1\nkind: Service\nmetadata:\n  name: {{ .Values.name }}\n"))
    assert "template_not_rendered" in {d.code for d in helm.diagnostics}  # never rendered
    assert helm.artifacts[0].status is ArtifactStatus.FAILED


# --- compose ---------------------------------------------------------------------------------------


def test_compose_services_and_their_explicit_references() -> None:
    extraction = read(("compose.yaml", COMPOSE))
    api = "compose:shop/service/api"
    props = properties(extraction, api)
    assert (props["build"], props["replicas"], props["cpu_limit"], props["health_check"]) == (
        "./api",
        2,
        "0.5",
        True,
    )
    assert props["environment_variable"] == "DATABASE_URL"
    assert properties(extraction, "compose:shop/service/web")["ports"] == ["80:80"]  # never 4880
    assert targets(extraction, "compose:shop/service/web") == {"service/api"}
    assert targets(extraction, api) == {"host/db"}
    assert targets(extraction, "compose:shop/service/db") == {"volume/data"}
    assert found(extraction, "compose:shop/volume/data", FindingType.ENTITY)


# --- terraform -------------------------------------------------------------------------------------


def test_terraform_json_configuration_is_never_evaluated() -> None:
    extraction = read(("main.tf.json", json.dumps(TERRAFORM)))
    db, web = "terraform:aws_db_instance.main", "terraform:aws_instance.web"
    assert properties(extraction, db) == {
        "resource_type": "aws_db_instance", "engine": "postgres", "instance_class": "db.t3.micro",
        "password": None,
    }  # fmt: skip
    assert next(f for f in found(extraction, db) if f.property == "password").redacted
    assert properties(extraction, web)["user_data"] == "${aws_db_instance.main.address}"  # as written
    assert targets(extraction, web) == {"aws_db_instance.main"}
    assert {d.code for d in extraction.diagnostics} == {"module_not_expanded", "not_interpreted"}


def test_terraform_show_output_reads_values_and_redacts_sensitive_ones() -> None:
    extraction = read(("plan.json", json.dumps(SHOWN)))
    db = "terraform:aws_db_instance.main"
    assert properties(extraction, db)["multi_az"] is True
    assert next(f for f in found(extraction, db) if f.property == "password").redacted
    assert found(extraction, "terraform:module.cache.aws_elasticache_cluster.this.0", FindingType.ENTITY)
    assert extraction.artifacts[0].format_version == "1.0"


def test_native_terraform_is_unsupported_not_guessed() -> None:
    extraction = read(("main.tf", 'resource "aws_instance" "web" {}'))
    assert (extraction.artifacts[0].status, extraction.findings) == (ArtifactStatus.UNSUPPORTED, ())
    assert [d.code for d in extraction.diagnostics] == ["hcl_not_supported"]


# --- architecture json, detection and safety -------------------------------------------------------


def test_an_exported_architecture_is_read_by_the_ir() -> None:
    extraction = read(("shop.json", json.dumps(to_dict(SOURCE_IR))))
    assert extraction.artifacts[0].source_type is SourceType.ARCHITECTURE_JSON
    assert properties(extraction, "db")["kind"] == "database"
    assert targets(extraction, "api") == {"db"}
    broken = read(("bad.json", json.dumps({"schema_version": "1", "nodes": [{"id": "x"}]})))
    assert broken.artifacts[0].status is ArtifactStatus.FAILED
    assert {d.code for d in broken.diagnostics} == {"invalid_architecture"}


def test_unrecognized_content_is_unsupported() -> None:
    extraction = read(("notes.yaml", "shopping: [milk]\n"))
    assert (extraction.artifacts[0].status, extraction.diagnostics[0].code) == (
        ArtifactStatus.UNSUPPORTED, "unrecognized_format",
    )  # fmt: skip


def test_no_secret_value_is_ever_kept() -> None:
    extraction = read(
        ("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE),
        ("main.tf.json", json.dumps(TERRAFORM)), ("plan.json", json.dumps(SHOWN)),
    )  # fmt: skip
    text = json.dumps(
        [f.to_dict() for f in extraction.findings] + [d.to_dict() for d in extraction.diagnostics]
    )
    for secret in ("hunter2", "pa55word", "s3cr3t", "sk_live_do_not_keep"):
        assert secret not in text


def test_extraction_is_deterministic() -> None:
    first = read(("k8s/shop.yaml", DEPLOYMENT), ("compose.yaml", COMPOSE))
    again = read(("compose.yaml", COMPOSE), ("k8s/shop.yaml", DEPLOYMENT))
    assert [f.to_dict() for f in first.findings] == [f.to_dict() for f in again.findings]
    assert first.extractors == {"docker_compose": 1, "kubernetes": 1}


# --- coverage: nothing present is silently dropped -------------------------------------------------

PROBED = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: web, namespace: shop, annotations: {owner: team-a}}
spec:
  replicas: 2
  strategy: {type: RollingUpdate}
  template:
    spec:
      nodeSelector: {disk: ssd}
      containers:
        - name: web
          image: nginx:1.27
          command: ["nginx"]
          ports: []
          readinessProbe: {httpGet: {path: /healthz, port: 80}}
"""


def test_uninterpreted_fields_are_named_never_dropped() -> None:
    extraction = read(("k8s/web.yaml", PROBED))
    values = properties(extraction, "kubernetes:shop/deployment/web")
    assert values["uninterpreted_fields"] == [
        "metadata.annotations",
        "spec.strategy",
        "spec.template.spec.containers[0].command",
        "spec.template.spec.nodeSelector",
    ]  # names only — never "team-a" or "ssd"
    unread = next(
        f for f in found(extraction, "kubernetes:shop/deployment/web") if f.property == "uninterpreted_fields"
    )
    assert unread.verification is Verification.UNSUPPORTED
    assert unread.warnings
    assert extraction.artifacts[0].status is ArtifactStatus.PARSED  # visible, not a failure to read


def test_kubernetes_probes_api_version_and_declared_empty_ports() -> None:
    values = properties(read(("k8s/web.yaml", PROBED)), "kubernetes:shop/deployment/web")
    assert values["health_check"] is True  # declared — that it passes is not known
    assert values["api_version"] == "apps/v1"
    assert values["ports"] == []  # declared empty, unlike absent


def test_compose_reservations_labels_and_unread_fields() -> None:
    compose = """\
name: shop
x-common: {restart: always}
services:
  api:
    image: acme/api
    restart: always
    labels: ["tier=backend"]
    deploy: {mode: replicated, resources: {reservations: {cpus: "0.25", memory: 128M}}}
"""
    extraction = read(("compose.yaml", compose))
    values = properties(extraction, "compose:shop/service/api")
    assert (values["cpu_request"], values["memory_request"]) == ("0.25", "128M")
    assert values["labels"] == {"tier": "backend"}
    assert values["uninterpreted_fields"] == ["deploy.mode", "restart"]
    top = [
        f
        for f in extraction.findings
        if f.property == "uninterpreted_fields" and f.entity.startswith("artifact")
    ]
    assert [f.value for f in top] == [["x-common"]]


def test_terraform_tags_and_nested_blocks() -> None:
    body = {
        "resource": {
            "aws_s3_bucket": {
                "logs": {
                    "bucket": "acme-logs",
                    "tags": {"team": "platform"},
                    "versioning": {"enabled": True},
                    "lifecycle": {"prevent_destroy": True},
                }
            }
        }
    }
    extraction = read(("s3.tf.json", json.dumps(body)))
    values = properties(extraction, "terraform:aws_s3_bucket.logs")
    assert values["tags"] == {"team": "platform"}
    assert values["uninterpreted_fields"] == ["versioning"]  # lifecycle is a meta-argument
    assert extraction.artifacts[0].extractor == "terraform_json@1"
