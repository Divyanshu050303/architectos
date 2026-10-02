"""Normalization: the findings about each declared resource gathered into one normalized entity — its
name, resource type, role, IR node kind (when established), namespace and properties, every value
still pointing at the finding it came from.

**Role and kind** are set by versioned, per-format rules, and only where the source's own semantics
establish them:

- Kubernetes (``kubernetes-kinds@1``): a Job or CronJob is a ``worker`` and an Ingress a ``gateway``;
  a Service object is ``routing``, a ConfigMap or Secret ``configuration``, a PersistentVolumeClaim
  a ``volume``. Other workloads (Deployment, StatefulSet, …) are components of unknown kind: a
  Deployment does not say what it serves — the catalog (by image) or a person may.
- Compose (``compose-kinds@1``): a service is a component of unknown kind; named volumes and
  networks are ``volume`` and ``network``.
- Terraform (``terraform-kinds@1``): a table of unambiguous managed resource types (a managed
  database, cache, queue, bucket, load balancer, API gateway, CDN); networks, security and identity
  resources are ``network`` or ``configuration``; any other type is a component of unknown kind.
- Architecture JSON (``architecture-json@1``): the node kind the document states.

**Conflicts are reported, not resolved**: a resource declared in two places is reported
(``duplicate_definition``); a single-valued property with two different values keeps both and is
reported (``conflicting_values``). Nothing about the running system is inferred from a declaration.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.architecture_ir.component import NodeKind
from core.domain.discovery.findings import Diagnostic, Finding, SourceLocation
from core.domain.discovery.values import EntityRole, FindingType, Severity, SourceType, Verification

from .adapters import Extraction

NORMALIZATION_VERSION = 1
R, K = EntityRole, NodeKind
MULTI = frozenset({"image", "init_image", "ports", "environment_variable", "volume_claim", "hosts"})
COMPOSE_PARTS = 3  # project/kind/name

KUBERNETES: dict[str, tuple[EntityRole, NodeKind | None]] = {
    "Job": (R.COMPONENT, K.WORKER),
    "CronJob": (R.COMPONENT, K.WORKER),
    "Ingress": (R.COMPONENT, K.GATEWAY),
    "Service": (R.ROUTING, None),
    "ConfigMap": (R.CONFIGURATION, None),
    "Secret": (R.CONFIGURATION, None),
    "PersistentVolumeClaim": (R.VOLUME, None),
}
COMPOSE: dict[str, tuple[EntityRole, NodeKind | None]] = {
    "service": (R.COMPONENT, None),
    "volume": (R.VOLUME, None),
    "network": (R.NETWORK, None),
}
TERRAFORM_KINDS: dict[NodeKind, frozenset[str]] = {
    K.DATABASE: frozenset(
        {
            "aws_db_instance",
            "aws_rds_cluster",
            "aws_dynamodb_table",
            "google_sql_database_instance",
            "azurerm_postgresql_flexible_server",
            "azurerm_mysql_flexible_server",
            "azurerm_cosmosdb_account",
        }
    ),
    K.CACHE: frozenset(
        {
            "aws_elasticache_cluster",
            "aws_elasticache_replication_group",
            "google_redis_instance",
            "azurerm_redis_cache",
        }
    ),
    K.QUEUE: frozenset(
        {
            "aws_sqs_queue",
            "aws_sns_topic",
            "aws_msk_cluster",
            "aws_mq_broker",
            "google_pubsub_topic",
            "azurerm_servicebus_namespace",
        }
    ),
    K.STORAGE: frozenset(
        {"aws_s3_bucket", "aws_efs_file_system", "google_storage_bucket", "azurerm_storage_account"}
    ),
    K.LOAD_BALANCER: frozenset({"aws_lb", "aws_alb", "aws_elb"}),
    K.GATEWAY: frozenset({"aws_api_gateway_rest_api", "aws_apigatewayv2_api"}),
    K.CDN: frozenset({"aws_cloudfront_distribution"}),
}
TERRAFORM_ROLES: dict[EntityRole, frozenset[str]] = {
    R.NETWORK: frozenset(
        {
            "aws_vpc",
            "aws_subnet",
            "google_compute_network",
            "google_compute_subnetwork",
            "azurerm_virtual_network",
            "azurerm_subnet",
        }
    ),
    R.CONFIGURATION: frozenset(
        {
            "aws_security_group",
            "aws_security_group_rule",
            "aws_iam_role",
            "aws_iam_policy",
            "aws_iam_role_policy_attachment",
            "aws_ssm_parameter",
            "aws_secretsmanager_secret",
            "aws_kms_key",
        }
    ),
    R.ROUTING: frozenset({"aws_lb_listener", "aws_lb_target_group", "aws_route53_record"}),
}
RULES = {
    SourceType.KUBERNETES: "kubernetes-kinds@1",
    SourceType.DOCKER_COMPOSE: "compose-kinds@1",
    SourceType.TERRAFORM_JSON: "terraform-kinds@1",
    SourceType.ARCHITECTURE_JSON: "architecture-json@1",
}


@dataclass(frozen=True, slots=True)
class Value:
    value: Any
    finding_id: str
    verification: Verification
    source_property: str
    location: SourceLocation
    redacted: bool = False


@dataclass(frozen=True)
class NormalizedEntity:
    key: str
    name: str
    source_type: SourceType
    resource_type: str
    role: EntityRole
    location: SourceLocation
    finding_ids: tuple[str, ...]
    kind: NodeKind | None = None
    kind_rule: str | None = None
    namespace: str | None = None
    properties: Mapping[str, tuple[Value, ...]] = field(default_factory=dict)

    def value(self, name: str) -> Value | None:
        """The property's one value — None when absent or conflicting."""
        values = self.properties.get(name, ())
        return values[0] if values and len({repr(v.value) for v in values}) == 1 else None

    def values(self, name: str) -> tuple[Value, ...]:
        return self.properties.get(name, ())


@dataclass(frozen=True)
class Normalization:
    entities: tuple[NormalizedEntity, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


def _classify(source: SourceType, resource_type: str, kind_value: Any) -> tuple[EntityRole, NodeKind | None]:
    if source is SourceType.KUBERNETES:
        return KUBERNETES.get(resource_type, (R.COMPONENT, None))
    if source is SourceType.DOCKER_COMPOSE:
        return COMPOSE.get(resource_type, (R.COMPONENT, None))
    if source is SourceType.TERRAFORM_JSON:
        bare = resource_type.removeprefix("data.")
        role = next((r for r, types in TERRAFORM_ROLES.items() if bare in types), R.COMPONENT)
        kind = next((k for k, types in TERRAFORM_KINDS.items() if bare in types), None)
        return role, kind if role is R.COMPONENT else None
    try:
        return R.COMPONENT, NodeKind(kind_value)
    except ValueError:
        return R.COMPONENT, None


def _first(properties: Mapping[str, tuple[Value, ...]], name: str) -> Any:
    values = properties.get(name, ())
    return values[0].value if values else None


def _shape(
    key: str, source: SourceType, properties: Mapping[str, tuple[Value, ...]]
) -> tuple[str, str, str | None]:
    """(name, resource type, namespace) of an entity, from its key and properties."""
    if source is SourceType.KUBERNETES:
        return key.rsplit("/", 1)[-1], str(_first(properties, "kind")), _first(properties, "namespace")
    if source is SourceType.DOCKER_COMPOSE:
        parts = key.removeprefix("compose:").split("/")
        return parts[-1], parts[-2], parts[0] if len(parts) == COMPOSE_PARTS else None
    if source is SourceType.TERRAFORM_JSON:
        address = key.removeprefix("terraform:")
        module = address.rsplit(".", 2)[0] if address.startswith("module.") else None
        resource_type = str(_first(properties, "resource_type") or address.split(".")[0])
        return address, ("data." + resource_type if address.startswith("data.") else resource_type), module
    return key, "node", None


def _values(findings: list[Finding]) -> dict[str, tuple[Value, ...]]:
    properties: dict[str, list[Value]] = defaultdict(list)
    for finding in findings:
        if finding.type is FindingType.PROPERTY and finding.property:
            source_property = finding.source_property or finding.property
            value = Value(
                finding.value,
                finding.id,
                finding.verification,
                source_property,
                finding.location,
                finding.redacted,
            )
            properties[finding.property].append(value)
    return {name: tuple(values) for name, values in sorted(properties.items())}


def normalize(extraction: Extraction) -> Normalization:
    """One normalized entity per declared resource, in key order."""
    types = {a.path: a.source_type for a in extraction.artifacts}
    by_entity: dict[str, list[Finding]] = defaultdict(list)
    for finding in extraction.findings:
        by_entity[finding.entity].append(finding)
    entities: list[NormalizedEntity] = []
    diagnostics: list[Diagnostic] = []
    for key in sorted(by_entity):
        findings = sorted(by_entity[key], key=lambda f: f.sort_key)
        declared = [f for f in findings if f.type is FindingType.ENTITY]
        source = types.get(declared[0].location.artifact) if declared else None
        if not declared or source is None:
            continue  # findings about an artifact itself, or a resource that could not be read
        if len(declared) > 1:
            places = ", ".join(sorted({f.location.reference for f in declared}))
            message = f"{key} is declared more than once ({places}); every declaration is kept."
            diagnostics.append(
                Diagnostic("duplicate_definition", Severity.WARNING, message, declared[1].location)
            )
        properties = _values(findings)
        for name, values in properties.items():
            if name not in MULTI and len({repr(v.value) for v in values}) > 1:
                message = f"{key}: {name} has different values; none is chosen."
                diagnostics.append(
                    Diagnostic("conflicting_values", Severity.WARNING, message, values[1].location)
                )
        name, resource_type, namespace = _shape(key, source, properties)
        role, kind = _classify(source, resource_type, _first(properties, "kind"))
        entities.append(
            NormalizedEntity(
                key, name[:200], source, resource_type, role, declared[0].location,
                tuple(f.id for f in findings if f.type is not FindingType.UNSUPPORTED),
                kind, RULES[source] if kind is not None else None, namespace, properties,
            )
        )  # fmt: skip
    return Normalization(tuple(entities), tuple(diagnostics))
