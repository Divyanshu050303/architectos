"""Component catalog and configuration mapping: each normalized entity mapped to the existing component
catalog (by id — the catalog is never copied) and its declared properties mapped to Architecture IR
configuration, by deterministic, versioned rules.

**Components** (``discovery-catalog@1``):

- Terraform: a provider-specific type names one component exactly (``aws_sqs_queue`` is
  ``messaging/aws-sqs``); a managed database or cache maps by its declared ``engine`` (Cloud SQL by
  ``database_version``) — not stated literally, it stays unmapped; a generic type (a load balancer, an
  API gateway, a CDN, a virtual machine) maps to the generic component.
- Kubernetes workloads and Compose services: by container image name (``postgres:16`` is
  ``databases/postgresql``). An image is evidence of the technology, not proof, so the mapping is
  ``mapped``, never ``exact_match``; containers mapping to different components are ``ambiguous``; a
  service built from source, or an image the rules do not know, is ``unmapped``.
- Architecture JSON: the component the document declares (``exact_match``), else its technology.
- Supporting resources (routing, configuration, volumes, networks) are not components.
- A Terraform type of a provider the tables do not cover (not ``aws``, ``google`` or ``azurerm``) is
  ``unsupported``: the rules cannot say, rather than "no component".

When the source does not establish a kind and the catalog component describes exactly one, that kind
is adopted (rule ``discovery-catalog@1``, inferred); a kind the source states that the component does
not describe leaves the entity unmapped, with the reason.

**Configuration** (``discovery-configuration@1``): replicas, CPU and memory requests and limits (exact
unit conversions: Kubernetes quantities, Docker byte units), a declared health check, an instance class
and allocated storage (GiB, as AWS documents it) — each checked by the IR's own property specification,
and against the entity's kind when known; each keeps the value as written and the unit conversion
applied. A value that fails, cannot be converted, or is declared more
than once (several containers, several files) is kept as a mapping with no value, marked invalid, with
the reason — no total or default is invented. A secret value is never mapped.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import NODE_PROPERTIES, ValueType
from core.domain.components.repository import ComponentCatalog
from core.domain.components.specifications import ComponentSpecification
from core.domain.discovery.findings import CandidateEntity, ComponentMapping, PropertyMapping
from core.domain.discovery.values import EntityRole, MappingStatus, SourceType

from .normalize import NormalizedEntity, Value

CATALOG_RULE = "discovery-catalog@1"
CONFIGURATION_RULE = "discovery-configuration@1"
M = MappingStatus

IMAGES = {
    "postgres": "databases/postgresql",
    "postgresql": "databases/postgresql",
    "mysql": "databases/mysql",
    "redis": "databases/redis",
    "mongo": "databases/mongodb",
    "mongodb": "databases/mongodb",
    "elasticsearch": "databases/elasticsearch",
    "cassandra": "databases/cassandra",
    "clickhouse-server": "databases/clickhouse",
    "scylla": "databases/scylladb",
    "rabbitmq": "messaging/rabbitmq",
    "kafka": "messaging/kafka",
    "cp-kafka": "messaging/kafka",
    "nats": "messaging/nats",
    "pulsar": "messaging/pulsar",
    "prometheus": "observability/prometheus",
    "grafana": "observability/grafana",
    "loki": "observability/loki",
    "jaeger": "observability/jaeger",
    "all-in-one": None,  # ambiguous by name alone (jaegertracing/all-in-one, others): never mapped
}
EXACT_TYPES = {
    "aws_sqs_queue": "messaging/aws-sqs",
    "aws_sns_topic": "messaging/aws-sns",
    "aws_s3_bucket": "storage/aws-s3",
    "aws_efs_file_system": "storage/aws-efs",
    "aws_dynamodb_table": "databases/aws-dynamodb",
    "aws_lambda_function": "compute/aws-lambda",
    "aws_ecs_service": "compute/aws-ecs",
    "google_pubsub_topic": "messaging/google-pubsub",
    "google_storage_bucket": "storage/google-cloud-storage",
    "google_cloud_run_service": "compute/google-cloud-run",
    "google_cloud_run_v2_service": "compute/google-cloud-run",
}
GENERIC_TYPES = {
    "aws_lb": "networking/load-balancer",
    "aws_alb": "networking/load-balancer",
    "aws_elb": "networking/load-balancer",
    "aws_api_gateway_rest_api": "networking/api-gateway",
    "aws_apigatewayv2_api": "networking/api-gateway",
    "aws_cloudfront_distribution": "networking/cdn",
    "aws_instance": "compute/virtual-machine",
    "google_compute_instance": "compute/virtual-machine",
    "azurerm_linux_virtual_machine": "compute/virtual-machine",
    "aws_msk_cluster": "messaging/kafka",
    "google_redis_instance": "databases/redis",
    "azurerm_redis_cache": "databases/redis",
    "azurerm_postgresql_flexible_server": "databases/postgresql",
    "azurerm_mysql_flexible_server": "databases/mysql",
}
ENGINES = {
    "postgres": "databases/postgresql",
    "aurora-postgresql": "databases/postgresql",
    "mysql": "databases/mysql",
    "aurora-mysql": "databases/mysql",
    "redis": "databases/redis",
}
ENGINE_TYPES = frozenset(
    {"aws_db_instance", "aws_rds_cluster", "aws_elasticache_cluster", "aws_elasticache_replication_group"}
)
COVERED_PROVIDERS = ("aws_", "google_", "azurerm_")  # the providers the Terraform tables cover
CLOUD_SQL = {"POSTGRES": "databases/postgresql", "MYSQL": "databases/mysql"}
VERSION = re.compile(r"^v?([0-9]+(?:\.[0-9]+){0,3})(?:-[A-Za-z0-9.]+)?$")
QUANTITY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([A-Za-z]*)$")
KUBERNETES_BYTES = {
    "": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
    "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
}  # fmt: skip
DOCKER_BYTES = {"": 1, "b": 1, "k": 2**10, "kb": 2**10, "m": 2**20, "mb": 2**20, "g": 2**30, "gb": 2**30}
GIB = 2**30
TERRAFORM_PROPERTIES = {
    "instance_class": "instance_class",
    "instance_type": "instance_class",
    "node_type": "instance_class",
    "machine_type": "instance_class",
    "allocated_storage": "storage_bytes",
}
RESOURCES = {
    "cpu_request": "cpu_request_cores",
    "cpu_limit": "cpu_limit_cores",
    "memory_request": "memory_request_bytes",
    "memory_limit": "memory_limit_bytes",
}


@dataclass(frozen=True)
class _Index:
    specs: dict[str, ComponentSpecification]
    by_technology: dict[str, str]


def _index(catalog: ComponentCatalog) -> _Index:
    specs = {s.id: s for s in catalog.list()}
    return _Index(specs, {s.technology: s.id for s in specs.values()})


def image_name(reference: str) -> tuple[str, str | None]:
    """(name, tag) of an image reference: ``ghcr.io/acme/postgres:16`` is (``postgres``, ``16``)."""
    reference = reference.split("@", 1)[0]
    last = reference.rpartition("/")[2]
    name, _, tag = last.partition(":")
    return name.lower(), tag or None


def _mapping(
    status: MappingStatus, component: str | None = None, reason: str | None = None
) -> ComponentMapping:
    return ComponentMapping(status, CATALOG_RULE, component, reason=reason)


def _literal(value: Value | None) -> str | None:
    """A value stated literally — not an interpolation, a reference or a redaction."""
    if value is None or value.redacted or not isinstance(value.value, str) or "${" in value.value:
        return None
    return value.value


def _terraform(entity: NormalizedEntity, catalog: _Index) -> ComponentMapping:  # noqa: PLR0911
    kind = entity.resource_type.removeprefix("data.")
    if kind in EXACT_TYPES:
        return _mapping(M.EXACT_MATCH, EXACT_TYPES[kind])
    if kind in GENERIC_TYPES:
        return _mapping(M.MAPPED, GENERIC_TYPES[kind])
    if kind in ENGINE_TYPES:
        engine = _literal(entity.value("engine"))
        if engine is None:
            return _mapping(M.UNMAPPED, reason=f"The {kind}'s engine is not stated literally.")
        if engine not in ENGINES:
            return _mapping(M.UNMAPPED, reason=f"No catalog component for engine {engine[:64]}.")
        return _mapping(M.MAPPED, ENGINES[engine])
    if kind == "google_sql_database_instance":
        version = _literal(entity.value("database_version"))
        family = version.split("_", 1)[0] if version else None
        if family in CLOUD_SQL:
            return _mapping(M.MAPPED, CLOUD_SQL[family])
        return _mapping(
            M.UNMAPPED, reason="The Cloud SQL database_version is not stated, or not in the catalog."
        )
    provider = kind.split("_", 1)[0]
    if f"{provider}_" not in COVERED_PROVIDERS:
        return _mapping(M.UNSUPPORTED, reason=f"The {provider} provider is not covered by {CATALOG_RULE}.")
    return _mapping(M.UNMAPPED, reason=f"No catalog component for {kind}.")


def _images(entity: NormalizedEntity, catalog: _Index) -> ComponentMapping:
    references = sorted({v.value for v in entity.values("image") if isinstance(v.value, str)})
    if not references:
        built = entity.values("build")
        reason = "Built from source: its technology is not declared." if built else "No image is declared."
        return _mapping(M.UNMAPPED, reason=reason)
    components = sorted({c for n, _ in map(image_name, references) if (c := IMAGES.get(n))})
    if len(components) > 1:
        reason = "Its containers' images map to different components."
        return ComponentMapping(M.AMBIGUOUS, CATALOG_RULE, candidates=tuple(components), reason=reason)
    if not components:
        return _mapping(M.UNMAPPED, reason=f"No catalog component for the image {references[0][:200]}.")
    return _mapping(M.MAPPED, components[0])


def _declared(entity: NormalizedEntity, catalog: _Index) -> ComponentMapping:
    component = _literal(entity.value("component"))
    if component is not None:
        if component in catalog.specs:
            return _mapping(M.EXACT_MATCH, component)
        return _mapping(M.UNMAPPED, reason=f"{component} is not in the component catalog.")
    technology = _literal(entity.value("technology"))
    if technology is not None and technology in catalog.by_technology:
        return _mapping(M.MAPPED, catalog.by_technology[technology])
    return _mapping(M.UNMAPPED, reason="The document declares no catalog component.")


READERS: dict[SourceType, Callable[[NormalizedEntity, _Index], ComponentMapping]] = {
    SourceType.TERRAFORM_JSON: _terraform,
    SourceType.KUBERNETES: _images,
    SourceType.DOCKER_COMPOSE: _images,
    SourceType.ARCHITECTURE_JSON: _declared,
}


def _component(entity: NormalizedEntity, catalog: _Index) -> ComponentMapping:
    if entity.role is not EntityRole.COMPONENT:
        return _mapping(M.UNMAPPED, reason=f"A supporting resource ({entity.role.value}), not a component.")
    mapping = READERS[entity.source_type](entity, catalog)
    if mapping.component_id is not None and mapping.component_id not in catalog.specs:
        return _mapping(M.UNMAPPED, reason=f"{mapping.component_id} is not in this component catalog.")
    if mapping.status is M.AMBIGUOUS:
        kept = tuple(c for c in mapping.candidates if c in catalog.specs)
        if len(kept) < 2:  # an ambiguity is between two or more
            return _mapping(M.UNMAPPED, reason="Its candidate components are not in this component catalog.")
    return mapping


# --- configuration ---------------------------------------------------------------------------------


def _decimal(text: str) -> Decimal | None:
    try:
        number = Decimal(text.strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def kubernetes_cpu(value: Any) -> Decimal | None:
    """A Kubernetes CPU quantity in cores: ``250m`` is 0.25, ``2`` is 2."""
    text = str(value)
    if text.endswith("m"):
        millis = _decimal(text[:-1])
        return None if millis is None else (millis / 1000).normalize()
    return _decimal(text)


def byte_count(value: Any, units: dict[str, int]) -> int | None:
    """An exact byte count of a quantity with a unit suffix, or None when not exact or not understood."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    match = QUANTITY.fullmatch(str(value).strip())
    if match is None or match.group(2) not in units:
        return None
    number = _decimal(match.group(1))
    total = None if number is None else number * units[match.group(2)]
    return int(total) if total is not None and total == total.to_integral_value() else None


Conversion = tuple[str, Any, str | None]  # (IR property, IR value or None, the unit conversion)


def _converted(source: SourceType, name: str, value: Any) -> Conversion | None:  # noqa: PLR0911
    """The IR property and value of a source property — the value None when it cannot be converted —
    and the unit conversion applied; None when no rule maps it."""
    match source:
        case SourceType.ARCHITECTURE_JSON if name.startswith("configuration."):
            return name.removeprefix("configuration."), value, None
        case SourceType.TERRAFORM_JSON if name in TERRAFORM_PROPERTIES:
            target = TERRAFORM_PROPERTIES[name]
            if target != "storage_bytes":
                return target, value if isinstance(value, str) and "${" not in value else None, None
            whole = isinstance(value, int) and not isinstance(value, bool)
            return target, value * GIB if whole else None, "GiB to bytes (x 2^30)"
        case SourceType.KUBERNETES | SourceType.DOCKER_COMPOSE if name in {"replicas", "health_check"}:
            return name, value, None
        case SourceType.KUBERNETES if name.startswith("cpu") and name in RESOURCES:
            return RESOURCES[name], kubernetes_cpu(value), "Kubernetes CPU quantity to cores (m = 1/1000)"
        case SourceType.KUBERNETES if name in RESOURCES:
            amount = byte_count(value, KUBERNETES_BYTES)
            return RESOURCES[name], amount, "Kubernetes quantity to bytes (k/M/G = 10^3n, Ki/Mi/Gi = 2^10n)"
        case SourceType.DOCKER_COMPOSE if name.startswith("cpu") and name in RESOURCES:
            return RESOURCES[name], _decimal(str(value)), None
        case SourceType.DOCKER_COMPOSE if name in RESOURCES:
            amount = byte_count(str(value).lower(), DOCKER_BYTES)
            return RESOURCES[name], amount, "Docker byte value to bytes (k/m/g = 2^10n)"
    return None


def _ir_value(value: Any) -> Any:
    """The value as the IR's JSON form writes it: exact decimals as text."""
    return str(value) if isinstance(value, Decimal) else value


def _problem(target: str, value: Any, values: tuple[Value, ...], kind: NodeKind | None) -> str | None:
    spec = NODE_PROPERTIES[target]
    if len(values) > 1:
        return "Declared more than once (several containers or places); no single value is chosen."
    if value is None:
        return f"{values[0].value!r:.80} cannot be converted to {target}."
    typed = value
    if spec.type is ValueType.DECIMAL and isinstance(value, str | int) and not isinstance(value, bool):
        typed = _decimal(str(value))
    if spec.type is ValueType.TEXT_LIST and isinstance(value, list):
        typed = tuple(value)
    violations = spec.problems(typed, f"configuration.{target}")
    if violations:
        return violations[0].message
    if kind is not None and kind.value not in spec.applies_to:
        return f"{target} does not apply to a {kind.value}."
    return None


def property_mappings(entity: NormalizedEntity, kind: NodeKind | None) -> tuple[PropertyMapping, ...]:
    """The entity's declared properties mapped to IR configuration, in property order."""
    found: list[PropertyMapping] = []
    for name, values in sorted(entity.properties.items()):
        if not values or any(v.redacted for v in values):
            continue  # a secret is never mapped
        first = values[0]
        converted = _converted(entity.source_type, name, first.value)
        if converted is None or converted[0] not in NODE_PROPERTIES:
            continue
        target, value, conversion = converted
        problem = _problem(target, value, values, kind)
        found.append(
            PropertyMapping(
                first.source_property, target, CONFIGURATION_RULE, first.verification, first.finding_id,
                None if problem else _ir_value(value), valid=problem is None, problem=problem,
                source_value=first.value, transformation=conversion,
            )
        )  # fmt: skip
    return tuple(found)


# --- candidates ------------------------------------------------------------------------------------


def _technology(
    entity: NormalizedEntity, spec: ComponentSpecification | None
) -> tuple[str | None, str | None]:
    """(technology, version): as the document states it, or the catalog's technology with the version
    every image tag agrees on — a tag that is not a version (``latest``) gives no version."""
    if entity.source_type is SourceType.ARCHITECTURE_JSON:
        return _literal(entity.value("technology")), _literal(entity.value("technology_version"))
    if spec is None:
        return None, None
    tags = [image_name(v.value)[1] for v in entity.values("image") if isinstance(v.value, str)]
    versions = {m.group(1) for t in tags if t and (m := VERSION.fullmatch(t))}
    return spec.technology, versions.pop() if len(versions) == 1 and all(tags) else None


def candidates(
    entities: tuple[NormalizedEntity, ...], catalog: ComponentCatalog
) -> tuple[CandidateEntity, ...]:
    """Every normalized entity as a candidate: mapped to the catalog, its configuration mapped."""
    index = _index(catalog)
    found: list[CandidateEntity] = []
    for entity in entities:
        mapping = _component(entity, index)
        spec = index.specs.get(mapping.component_id) if mapping.component_id else None
        kind, rule = entity.kind, entity.kind_rule
        if spec is not None and kind is None and len(spec.node_kinds) == 1:
            kind, rule = spec.node_kinds[0], CATALOG_RULE
        elif spec is not None and kind is not None and kind not in spec.node_kinds:
            kinds = ", ".join(k.value for k in spec.node_kinds)
            mapping = _mapping(M.UNMAPPED, reason=f"{spec.id} describes {kinds}, not a {kind.value}.")
            spec = None
        technology, version = _technology(entity, spec)
        found.append(
            CandidateEntity(
                entity.key, entity.name, entity.source_type, entity.resource_type, entity.location, mapping,
                kind=kind, role=entity.role, kind_rule=rule, namespace=entity.namespace,
                technology=technology, technology_version=version,
                configuration=property_mappings(entity, kind) if entity.role is EntityRole.COMPONENT else (),
                finding_ids=entity.finding_ids,
            )
        )  # fmt: skip
    return tuple(found)
