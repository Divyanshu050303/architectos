"""What a discovery run extracts from its sources, each item with a stable id and the evidence it
rests on.

- An **artifact** is one supplied source file: its path, detected source type, content hash and size
  — never its content, which may hold secrets.
- A **finding** is one extracted fact: an entity, one of its properties, a reference to another
  entity, or a construct the adapter does not interpret. It records where it was read (artifact,
  YAML document, path within it), how it is known (``Verification``), which extractor (and version)
  produced it, and its value as written — or, for a secret, the fact that a value exists and is
  redacted.
- A **candidate entity** normalizes the findings about one declared resource: its IR node kind when
  the source establishes it (``None`` otherwise: unknown), its catalog mapping and the configuration
  values mapped from its properties.
- A **candidate relationship** is an explicit reference from one entity to another. Its kind is
  stated only when the source's semantics establish it; a reference to something absent is
  unresolved — never an IR connection.
"""

import builtins
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind

from .values import (
    FINGERPRINT,
    MAX_REFERENCE,
    ArtifactStatus,
    FindingType,
    MappingStatus,
    RelationshipStatus,
    Severity,
    SourceType,
    Verification,
    check,
    code,
    count,
    digest,
    items,
    json_value,
    key,
    text,
    texts,
)

MAX_PATH = 256
AMBIGUOUS_MINIMUM = 2  # an ambiguous mapping lists at least two candidates


@dataclass(frozen=True, slots=True)
class SourceLocation:
    artifact: str  # the artifact's path, as supplied
    document: int | None = None  # the YAML document within it, from 0
    pointer: str | None = None  # the path within the document, e.g. "spec.template.spec.containers[0].image"
    line: int | None = None  # 1-based, when the parser knows it

    def __post_init__(self) -> None:
        check(
            [
                text(self.artifact, "location.artifact", MAX_PATH),
                count(self.document, "location.document", required=False),
                text(self.pointer, "location.pointer", MAX_REFERENCE, required=False),
                count(self.line, "location.line", required=False, minimum=1),
            ]
        )

    @property
    def key(self) -> tuple[str, int, str, int]:
        document = -1 if self.document is None else self.document
        return (self.artifact, document, self.pointer or "", self.line or 0)

    @property
    def reference(self) -> str:
        """As a provenance reference: ``path#document:pointer``."""
        where = f"#{self.document}" if self.document is not None else ""
        return f"{self.artifact}{where}:{self.pointer}" if self.pointer else f"{self.artifact}{where}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "document": self.document,
            "pointer": self.pointer,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    path: str
    content_hash: str  # sha256 of the content as supplied
    size_bytes: int
    status: ArtifactStatus
    source_type: SourceType | None = None  # None: no adapter recognizes it
    format_version: str | None = None  # e.g. a Compose "version", a Terraform format_version
    documents: int = 0  # YAML documents (or 1 for JSON)

    def __post_init__(self) -> None:
        hashed = isinstance(self.content_hash, str) and FINGERPRINT.fullmatch(self.content_hash)
        read = self.status in {ArtifactStatus.PARSED, ArtifactStatus.PARTIAL}
        check(
            [
                text(self.path, "artifact.path", MAX_PATH),
                None if hashed else "artifact.content_hash",
                count(self.size_bytes, "artifact.size_bytes"),
                None if isinstance(self.status, ArtifactStatus) else "artifact.status",
                None
                if self.source_type is None or isinstance(self.source_type, SourceType)
                else "artifact.source_type",
                text(self.format_version, "artifact.format_version", 64, required=False),
                count(self.documents, "artifact.documents"),
                "artifact.source_type" if read and self.source_type is None else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "status": self.status.value,
            "source_type": self.source_type.value if self.source_type else None,
            "format_version": self.format_version,
            "documents": self.documents,
        }


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A parse error, a limit reached, an unsupported construct or format — stated, never dropped."""

    code: str  # e.g. "malformed_yaml", "unsupported_kind", "too_deep", "hcl_not_supported"
    severity: Severity
    message: str
    location: SourceLocation | None = None

    def __post_init__(self) -> None:
        check(
            [
                code(self.code, "diagnostic.code"),
                None if isinstance(self.severity, Severity) else "diagnostic.severity",
                text(self.message, "diagnostic.message"),
                None
                if self.location is None or isinstance(self.location, SourceLocation)
                else "diagnostic.location",
            ]
        )

    @property
    def id(self) -> str:
        return digest("dsd", self.code, self.location.key if self.location else None, self.message)

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (self.location.key if self.location else ("", -1, "", 0), self.code, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "location": self.location.to_dict() if self.location else None,
        }


@dataclass(frozen=True, slots=True)
class Finding:
    type: FindingType
    entity: str  # the key of the entity it concerns (for an unsupported construct, of its artifact)
    location: SourceLocation
    verification: Verification
    extractor: str  # e.g. "kubernetes@1"
    property: str | None = None  # the normalized property, e.g. "replicas", "image", "ports"
    source_property: str | None = None  # as written, e.g. "spec.replicas"
    value: Any = None  # as written; None when redacted or absent
    redacted: bool = False  # a secret: its presence is recorded, never its value
    target: str | None = None  # a reference's target, as written
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.type, FindingType) else "finding.type",
                key(self.entity, "finding.entity"),
                None if isinstance(self.location, SourceLocation) else "finding.location",
                None if isinstance(self.verification, Verification) else "finding.verification",
                text(self.extractor, "finding.extractor", 64),
                code(self.property, "finding.property", required=False),
                text(self.source_property, "finding.source_property", MAX_REFERENCE, required=False),
                json_value(self.value, "finding.value"),
                None if isinstance(self.redacted, bool) else "finding.redacted",
                text(self.target, "finding.target", MAX_REFERENCE, required=False),
                texts(self.warnings, "finding.warnings"),
                "finding.property" if self.type is FindingType.PROPERTY and self.property is None else None,
                "finding.target" if self.type is FindingType.REFERENCE and self.target is None else None,
                "finding.value" if self.redacted and self.value is not None else None,  # never kept
            ]
        )

    @builtins.property  # the field `property` shadows the builtin here
    def id(self) -> str:
        return digest("dsf", self.type.value, self.entity, self.property, self.target, self.location.key)

    @builtins.property  # the field `property` shadows the builtin here
    def sort_key(self) -> tuple[Any, ...]:
        return (self.entity, self.type.value, self.property or "", self.target or "", self.location.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "entity": self.entity,
            "location": self.location.to_dict(),
            "verification": self.verification.value,
            "extractor": self.extractor,
            "property": self.property,
            "source_property": self.source_property,
            "value": self.value,
            "redacted": self.redacted,
            "target": self.target,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ComponentMapping:
    """How an entity maps to the component catalog — never forced: an ambiguous entity lists its
    candidates for a person to choose."""

    status: MappingStatus
    rule: str  # the mapping rule and its version, e.g. "discovery-catalog@1"
    component_id: str | None = None  # a catalog id, e.g. "databases/postgresql"
    candidates: tuple[str, ...] = ()  # for an ambiguous mapping
    reason: str | None = None

    def __post_init__(self) -> None:
        mapped = self.status in {MappingStatus.EXACT_MATCH, MappingStatus.MAPPED}
        ambiguous = self.status is MappingStatus.AMBIGUOUS
        check(
            [
                None if isinstance(self.status, MappingStatus) else "mapping.status",
                text(self.rule, "mapping.rule", 64),
                text(self.component_id, "mapping.component_id", 128, required=False),
                texts(self.candidates, "mapping.candidates", 128),
                text(self.reason, "mapping.reason", required=False),
                "mapping.component_id" if mapped != (self.component_id is not None) else None,
                "mapping.candidates" if ambiguous != (len(self.candidates) >= AMBIGUOUS_MINIMUM) else None,
                "mapping.reason" if not mapped and not self.reason else None,  # says why not
            ]
        )
        object.__setattr__(self, "candidates", tuple(sorted(set(self.candidates))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "rule": self.rule,
            "component_id": self.component_id,
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PropertyMapping:
    """A source property mapped to an IR configuration property, by a defined rule — or why not."""

    source_property: str
    property: str  # the IR configuration property, e.g. "replicas"
    rule: str
    verification: Verification
    finding_id: str
    value: Any = None
    valid: bool = True
    problem: str | None = None  # why the value cannot be used

    def __post_init__(self) -> None:
        check(
            [
                text(self.source_property, "configuration.source_property", MAX_REFERENCE),
                code(self.property, "configuration.property"),
                text(self.rule, "configuration.rule", 64),
                None if isinstance(self.verification, Verification) else "configuration.verification",
                text(self.finding_id, "configuration.finding_id", 64),
                json_value(self.value, "configuration.value"),
                None if isinstance(self.valid, bool) else "configuration.valid",
                text(self.problem, "configuration.problem", required=False),
                "configuration.problem" if not self.valid and not self.problem else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_property": self.source_property,
            "property": self.property,
            "rule": self.rule,
            "verification": self.verification.value,
            "finding_id": self.finding_id,
            "value": self.value,
            "valid": self.valid,
            "problem": self.problem,
        }


@dataclass(frozen=True, slots=True)
class CandidateEntity:
    key: str  # stable, e.g. "kubernetes:shop/deployment/api"; also its IR node id
    name: str
    source_type: SourceType
    resource_type: str  # as declared, e.g. "Deployment", "aws_db_instance", "service"
    location: SourceLocation
    mapping: ComponentMapping
    kind: NodeKind | None = None  # None: the source does not establish what it is
    namespace: str | None = None  # a namespace, project or module, when declared
    technology: str | None = None  # a technology name, when established
    technology_version: str | None = None
    configuration: tuple[PropertyMapping, ...] = ()
    finding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                key(self.key, "entity.key"),
                text(self.name, "entity.name", 200),
                None if isinstance(self.source_type, SourceType) else "entity.source_type",
                text(self.resource_type, "entity.resource_type", 128),
                None if isinstance(self.location, SourceLocation) else "entity.location",
                None if isinstance(self.mapping, ComponentMapping) else "entity.mapping",
                None if self.kind is None or isinstance(self.kind, NodeKind) else "entity.kind",
                text(self.namespace, "entity.namespace", 128, required=False),
                text(self.technology, "entity.technology", 64, required=False),
                text(self.technology_version, "entity.technology_version", 32, required=False),
                items(self.configuration, PropertyMapping, "entity.configuration"),
                texts(self.finding_ids, "entity.finding_ids", 64),
                None if self.finding_ids else "entity.finding_ids",  # every entity rests on evidence
            ]
        )
        object.__setattr__(self, "configuration", tuple(sorted(self.configuration, key=lambda c: c.property)))
        object.__setattr__(self, "finding_ids", tuple(sorted(set(self.finding_ids))))

    @property
    def id(self) -> str:
        return digest("dse", self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "name": self.name,
            "source_type": self.source_type.value,
            "resource_type": self.resource_type,
            "location": self.location.to_dict(),
            "mapping": self.mapping.to_dict(),
            "kind": self.kind.value if self.kind else None,
            "namespace": self.namespace,
            "technology": self.technology,
            "technology_version": self.technology_version,
            "configuration": [c.to_dict() for c in self.configuration],
            "finding_ids": list(self.finding_ids),
        }


@dataclass(frozen=True, slots=True)
class CandidateRelationship:
    source: str  # the referring entity's key
    reference: str  # the target as written in the source
    location: SourceLocation
    status: RelationshipStatus
    target: str | None = None  # the referenced entity's key, when resolved
    kind: ConnectionKind | None = None  # only when the source's semantics establish it
    finding_ids: tuple[str, ...] = ()
    reason: str | None = None  # why it is unresolved, or what the kind rests on

    def __post_init__(self) -> None:
        resolved = self.status is RelationshipStatus.RESOLVED
        check(
            [
                key(self.source, "relationship.source"),
                text(self.reference, "relationship.reference", MAX_REFERENCE),
                None if isinstance(self.location, SourceLocation) else "relationship.location",
                None if isinstance(self.status, RelationshipStatus) else "relationship.status",
                key(self.target, "relationship.target", required=False),
                None if self.kind is None or isinstance(self.kind, ConnectionKind) else "relationship.kind",
                texts(self.finding_ids, "relationship.finding_ids", 64),
                None if self.finding_ids else "relationship.finding_ids",
                text(self.reason, "relationship.reason", required=False),
                "relationship.target" if resolved != (self.target is not None) else None,
                "relationship.reason" if not resolved and not self.reason else None,
                "relationship.target" if self.target is not None and self.target == self.source else None,
            ]
        )
        object.__setattr__(self, "finding_ids", tuple(sorted(set(self.finding_ids))))

    @property
    def id(self) -> str:
        """Stable: the referring entity, the reference as written and where."""
        return digest("dsr", self.source, self.reference, self.location.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "reference": self.reference,
            "location": self.location.to_dict(),
            "status": self.status.value,
            "target": self.target,
            "kind": self.kind.value if self.kind else None,
            "finding_ids": list(self.finding_ids),
            "reason": self.reason,
        }
