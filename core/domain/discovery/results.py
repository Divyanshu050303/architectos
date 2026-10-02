"""The result of one discovery run: everything extracted, normalized and proposed, with the versions
of the extractors and mapping rules that produced it — serializable, versioned and deterministic.

The result keeps five layers apart: the **artifacts** (what was supplied, by hash), the
**findings** (facts as read), the **candidate entities and relationships** (the normalized model,
each mapped to the catalog or saying why not), the **proposed architecture** (an Architecture IR
built from what can be represented safely) with its **structural validation**, and the
**diagnostics** (parse errors, limits, unsupported constructs). Accepting a proposal is a separate,
explicit act of a person (see ``runs``).

The result is internally consistent — every finding an entity or relationship rests on exists,
relationships join discovered entities, every finding comes from a supplied artifact — and in
canonical order, so identical inputs and versions give the identical result and fingerprint.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import to_dict as ir_to_dict

from .findings import (
    CandidateEntity,
    CandidateRelationship,
    Diagnostic,
    Finding,
    SourceArtifact,
)
from .values import (
    ArtifactStatus,
    EntityRole,
    MappingStatus,
    RelationshipStatus,
    Severity,
    check,
    code,
    fingerprint,
    items,
    key,
    text,
)

RESULT_VERSION = 1
MAX_ARTIFACTS = 50
MAX_FINDINGS = 20_000
MAX_ENTITIES = 1000
MAX_RELATIONSHIPS = 5000
MAX_DIAGNOSTICS = 5000
CONFIDENT = frozenset({MappingStatus.EXACT_MATCH, MappingStatus.MAPPED})


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A structural problem of the proposed architecture, as the IR or the validation engine states it."""

    code: str
    severity: Severity
    message: str
    element_id: str | None = None
    rule: str | None = None

    def __post_init__(self) -> None:
        check(
            [
                code(self.code, "validation.code"),
                None if isinstance(self.severity, Severity) else "validation.severity",
                text(self.message, "validation.message"),
                key(self.element_id, "validation.element_id", required=False),
                text(self.rule, "validation.rule", 128, required=False),
            ]
        )

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.element_id or "", self.code, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "element_id": self.element_id,
            "rule": self.rule,
        }


def _versions(values: object) -> str | None:
    ok = isinstance(values, Mapping) and all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 1
        for k, v in values.items()
    )
    return None if ok else "extractors"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    artifacts: tuple[SourceArtifact, ...]
    findings: tuple[Finding, ...] = ()
    entities: tuple[CandidateEntity, ...] = ()
    relationships: tuple[CandidateRelationship, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    proposed: ArchitectureIR | None = None  # None: nothing can be represented yet
    validation: tuple[ValidationIssue, ...] = ()
    extractors: Mapping[str, int] = field(default_factory=dict)  # adapter and rule versions used
    version: int = RESULT_VERSION

    def __post_init__(self) -> None:
        check(
            [
                items(self.artifacts, SourceArtifact, "artifacts", MAX_ARTIFACTS),
                None if self.artifacts else "artifacts",
                items(self.findings, Finding, "findings", MAX_FINDINGS),
                items(self.entities, CandidateEntity, "entities", MAX_ENTITIES),
                items(self.relationships, CandidateRelationship, "relationships", MAX_RELATIONSHIPS),
                items(self.diagnostics, Diagnostic, "diagnostics", MAX_DIAGNOSTICS),
                None if self.proposed is None or isinstance(self.proposed, ArchitectureIR) else "proposed",
                items(self.validation, ValidationIssue, "validation", MAX_DIAGNOSTICS),
                _versions(self.extractors),
                None if self.version == RESULT_VERSION else "version",
            ]
        )
        check(self._consistency())
        object.__setattr__(self, "artifacts", tuple(sorted(self.artifacts, key=lambda a: a.path)))
        object.__setattr__(self, "findings", tuple(sorted(set(self.findings), key=lambda f: f.sort_key)))
        object.__setattr__(self, "entities", tuple(sorted(self.entities, key=lambda e: e.key)))
        ordered = sorted(self.relationships, key=lambda r: (r.source, r.reference, r.id))
        object.__setattr__(self, "relationships", tuple(ordered))
        object.__setattr__(
            self, "diagnostics", tuple(sorted(set(self.diagnostics), key=lambda d: d.sort_key))
        )
        object.__setattr__(self, "validation", tuple(sorted(set(self.validation), key=lambda v: v.sort_key)))
        object.__setattr__(self, "extractors", dict(sorted(self.extractors.items())))

    def _consistency(self) -> list[str | None]:
        paths = [a.path for a in self.artifacts]
        keys = [e.key for e in self.entities]
        findings = {f.id for f in self.findings}
        cited = {i for e in self.entities for i in e.finding_ids}
        cited |= {i for r in self.relationships for i in r.finding_ids}
        joined = {r.source for r in self.relationships} | {r.target for r in self.relationships if r.target}
        located = {f.location.artifact for f in self.findings} | {e.location.artifact for e in self.entities}
        return [
            "artifacts" if len(paths) != len(set(paths)) else None,
            "entities" if len(keys) != len(set(keys)) else None,
            "findings" if cited - findings else None,  # nothing rests on a finding that is not there
            "relationships" if joined - set(keys) else None,  # relationships join discovered entities
            "findings" if located - set(paths) else None,  # every fact comes from a supplied artifact
        ]

    # --- reading ---------------------------------------------------------------------------------

    @property
    def unresolved(self) -> tuple[str, ...]:
        """Entity keys and relationship ids a person must resolve before they enter the architecture."""
        components = [e for e in self.entities if e.role is EntityRole.COMPONENT]
        entities = [e.key for e in components if e.mapping.status not in CONFIDENT or e.kind is None]
        relationships = [r.id for r in self.relationships if r.status is RelationshipStatus.UNRESOLVED]
        return (*entities, *relationships)

    @property
    def has_warnings(self) -> bool:
        """Whether something was unsupported, ambiguous, unresolved or structurally invalid."""
        return bool(
            any(a.status is not ArtifactStatus.PARSED for a in self.artifacts)
            or any(d.severity is not Severity.INFO for d in self.diagnostics)
            or any(v.severity is not Severity.INFO for v in self.validation)
            or self.unresolved
        )

    def summary(self) -> dict[str, Any]:
        """Counts only — no score."""
        return {
            "artifacts": dict(sorted(Counter(a.status.value for a in self.artifacts).items())),
            "findings": len(self.findings),
            "entities": len(self.entities),
            "mappings": dict(sorted(Counter(e.mapping.status.value for e in self.entities).items())),
            "relationships": dict(sorted(Counter(r.status.value for r in self.relationships).items())),
            "diagnostics": dict(sorted(Counter(d.severity.value for d in self.diagnostics).items())),
            "validation": dict(sorted(Counter(v.severity.value for v in self.validation).items())),
            "unresolved": len(self.unresolved),
            "proposed_nodes": len(self.proposed.nodes) if self.proposed else 0,
            "proposed_connections": len(self.proposed.connections) if self.proposed else 0,
        }

    def _content(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "findings": [f.to_dict() for f in self.findings],
            "entities": [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "proposed": ir_to_dict(self.proposed) if self.proposed else None,
            "validation": [v.to_dict() for v in self.validation],
            "extractors": dict(self.extractors),
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint(self._content())

    def to_dict(self) -> dict[str, Any]:
        return self._content() | {
            "summary": self.summary(),
            "unresolved": list(self.unresolved),
            "fingerprint": self.fingerprint,
        }
