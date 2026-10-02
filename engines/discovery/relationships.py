"""Relationship discovery: each explicit reference a source makes resolved to an entity of the same
discovery — or kept unresolved with the reason. Only references the source states are followed
(names, selectors, addresses, the host of a connection string); nothing is inferred from naming or
proximity, and a reference is never resolved across formats (a Kubernetes host is not guessed to be
a Terraform database).

Resolution (``discovery-relationships@1``):

- Kubernetes: ``service/``, ``configmap/``, ``secret/`` and ``persistentvolumeclaim/`` names in the
  referring object's namespace; a Service's selector to the one workload whose pod labels contain it
  (none, or several, stays unresolved); a host by cluster DNS (``api``, ``api.shop``,
  ``api.shop.svc``, ``api.shop.svc.cluster.local``) to the Service it names.
- Compose: ``depends_on``, ``links``, named volumes and a host naming a service of the same project.
- Terraform: an address (``aws_db_instance.main``, its attributes, ``[0]`` instances) to the resource
  it names; a resource with several instances, referenced as a whole, stays unresolved.
- Architecture JSON: a connection to its target node.

A connection kind is set only where the source's semantics establish it: ``depends_on`` (Compose,
Terraform) is a ``dependency`` — a need, with no communication stated; an Ingress routes HTTP
requests to its Service (``request``); an Architecture JSON connection keeps its declared kind. Every
other kind stays unknown.
"""

from dataclasses import replace
from typing import Any

from core.architecture_ir.dependency import ConnectionKind
from core.domain.discovery.findings import CandidateRelationship, Finding
from core.domain.discovery.values import FindingType, RelationshipStatus, SourceType

from .adapters import Extraction
from .normalize import NormalizedEntity
from .terraform import address_key

RULE = "discovery-relationships@1"
RESOLVED, UNRESOLVED = RelationshipStatus.RESOLVED, RelationshipStatus.UNRESOLVED
KUBERNETES_NAMED = frozenset({"service", "configmap", "secret", "persistentvolumeclaim"})
CLUSTER_SUFFIXES = ((), ("svc",), ("svc", "cluster", "local"))
DEPENDS_ON = "Declared with depends_on: the source needs the target; no communication is stated."
INGRESS = "An Ingress routes HTTP requests to its Service."
DECLARED = "The connection kind the document declares."
NOT_DECLARED = "{} is not declared in these artifacts."
EXTERNAL = "The host {} is outside these artifacts, or not declared in them."
SELF = "The reference names its own resource."

Resolution = tuple[str | None, str | None]  # the target's key, or None and the reason


class _Index:
    def __init__(self, entities: tuple[NormalizedEntity, ...]) -> None:
        self.entities = {e.key: e for e in entities}

    def find(self, key: str, written: str) -> Resolution:
        return (key, None) if key in self.entities else (None, NOT_DECLARED.format(written))


def _kubernetes_key(namespace: str | None, kind: str, name: str) -> str:
    return f"kubernetes:{namespace}/{kind}/{name}" if namespace else f"kubernetes:{kind}/{name}"


def _labels(entity: NormalizedEntity) -> dict[str, Any] | None:
    value = entity.value("labels" if entity.resource_type == "Pod" else "pod_labels")
    return value.value if value is not None and isinstance(value.value, dict) else None


def _selector(index: _Index, source: NormalizedEntity, reference: str) -> Resolution:
    wanted = dict(pair.split("=", 1) for pair in reference.removeprefix("selector:").split(","))
    matched = sorted(
        e.key
        for e in index.entities.values()
        if e.source_type is SourceType.KUBERNETES
        and e.namespace == source.namespace
        and (labels := _labels(e)) is not None
        and all(labels.get(k) == v for k, v in wanted.items())
    )
    if len(matched) == 1:
        return matched[0], None
    if not matched:
        return None, "No workload in these artifacts has pod labels matching the selector."
    return None, f"The selector matches {len(matched)} workloads: {', '.join(matched[:5])}."


def _cluster_host(index: _Index, source: NormalizedEntity, host: str) -> Resolution:
    parts = host.lower().split(".")
    if len(parts) == 1:
        return index.find(_kubernetes_key(source.namespace, "service", parts[0]), host)
    if tuple(parts[2:]) in CLUSTER_SUFFIXES:
        return index.find(_kubernetes_key(parts[1], "service", parts[0]), host)
    return None, EXTERNAL.format(host)


def _kubernetes(index: _Index, source: NormalizedEntity, reference: str) -> Resolution:
    if reference.startswith("selector:"):
        return _selector(index, source, reference)
    kind, _, name = reference.partition("/")
    if kind == "host":
        return _cluster_host(index, source, name)
    if kind in KUBERNETES_NAMED:
        return index.find(_kubernetes_key(source.namespace, kind, name), reference)
    return None, f"{reference} is not a reference this rule follows."


def _compose(index: _Index, source: NormalizedEntity, reference: str) -> Resolution:
    kind, _, name = reference.partition("/")
    project = f"{source.namespace}/" if source.namespace else ""
    if kind == "host":
        key = f"compose:{project}service/{name}"
        return (key, None) if key in index.entities else (None, EXTERNAL.format(name))
    return index.find(f"compose:{project}{kind}/{name}", reference)


def _terraform(index: _Index, source: NormalizedEntity, reference: str) -> Resolution:
    parts = address_key(reference).removeprefix("terraform:").split(".")
    for end in range(len(parts), 1, -1):  # the longest prefix naming a resource: attributes dropped
        key = "terraform:" + ".".join(parts[:end])
        if key in index.entities:
            return key, None
        instances = [k for k in index.entities if k.startswith(f"{key}.") and "." not in k[len(key) + 1 :]]
        if len(instances) == 1:
            return instances[0], None
        if instances:
            return None, f"{reference} names {len(instances)} instances; none is chosen."
    return None, NOT_DECLARED.format(reference)


def _architecture(index: _Index, source: NormalizedEntity, reference: str) -> Resolution:
    return index.find(reference, reference)


RESOLVERS = {
    SourceType.KUBERNETES: _kubernetes,
    SourceType.DOCKER_COMPOSE: _compose,
    SourceType.TERRAFORM_JSON: _terraform,
    SourceType.ARCHITECTURE_JSON: _architecture,
}


def _kind(
    source: NormalizedEntity, finding: Finding, target: str
) -> tuple[ConnectionKind | None, str | None]:
    """The connection kind the source's semantics establish, and what it rests on — or unknown."""
    written = finding.source_property or ""
    if source.source_type is SourceType.ARCHITECTURE_JSON:
        declared = source.value(f"connection.{written.removeprefix('connections.')}.kind")
        if declared is not None and declared.value in set(ConnectionKind):
            return ConnectionKind(declared.value), DECLARED
        return None, None
    depends = written == "depends_on" or written.startswith("depends_on.")
    if depends and source.source_type in {SourceType.DOCKER_COMPOSE, SourceType.TERRAFORM_JSON}:
        return ConnectionKind.DEPENDENCY, DEPENDS_ON
    if (
        source.source_type is SourceType.KUBERNETES
        and source.resource_type == "Ingress"
        and "/service/" in target
    ):
        return ConnectionKind.REQUEST, INGRESS
    return None, None


def relationships(
    extraction: Extraction, entities: tuple[NormalizedEntity, ...]
) -> tuple[CandidateRelationship, ...]:
    """Every reference of a discovered entity, resolved or not — one per (source, reference as written,
    location) — in source, then reference order."""
    index = _Index(entities)
    found: dict[str, CandidateRelationship] = {}
    for finding in extraction.findings:
        source = index.entities.get(finding.entity)
        if finding.type is not FindingType.REFERENCE or source is None or not finding.target:
            continue
        target, reason = RESOLVERS[source.source_type](index, source, finding.target)
        kind: ConnectionKind | None = None
        if target == source.key:
            target, reason = None, SELF
        elif target is not None:
            kind, reason = _kind(source, finding, target)
        relationship = CandidateRelationship(
            source.key, finding.target, finding.location, RESOLVED if target else UNRESOLVED,
            target, kind, (finding.id,), reason, RULE,
        )  # fmt: skip
        earlier = found.get(relationship.id)
        if earlier is not None:
            relationship = replace(earlier, finding_ids=(*earlier.finding_ids, finding.id))
        found[relationship.id] = relationship
    return tuple(sorted(found.values(), key=lambda r: (r.source, r.reference, r.id)))
