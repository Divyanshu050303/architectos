"""The changes a migration must carry out, read from the Architecture IR's own diff of the source and
target — never from a comparison of its own.

Each change of the diff (an element added, removed or modified, with its changed fields) is kept
with its IR field changes and classified:

- **relevance**: ``migration`` when it changes what runs (components, connections, their
  configuration, placement or role), ``metadata`` when it only changes descriptions, provenance,
  lifecycle labels or traceability — recorded, but no step is planned for it;
- **aspects**: what it touches (provisioning, decommissioning, technology, routing, placement,
  resources, scaling, configuration, security, observability, …);
- **engines**: which analysis engines read the properties it changes (from each engine's declared
  inputs), so their evidence can be consulted for it;
- **interpretation**: ``supported`` when migration patterns can plan it, ``manual_interpretation``
  when what it means operationally is not modeled (a grouping change, an unrecognized setting, a
  value becoming unknown, a stateful component moving region), ``unsupported`` when no pattern can
  plan it (a component changing its role).

A diff is not a deployment: a change says what differs between two revisions, not how to apply it.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.architecture_ir.diff import ChangeKind, FieldChange

from .plans import PlanFinding, SourceRef, TargetRef
from .values import MAX_TEXT, check, items, text


class Relevance(StrEnum):
    MIGRATION = "migration"
    METADATA = "metadata"


class Interpretation(StrEnum):
    SUPPORTED = "supported"
    MANUAL_INTERPRETATION = "manual_interpretation"
    UNSUPPORTED = "unsupported"


class Aspect(StrEnum):
    PROVISIONING = "provisioning"  # an element is added
    DECOMMISSIONING = "decommissioning"  # an element is removed
    TOPOLOGY = "topology"  # the graph changes
    ROLE = "role"  # a node's or connection's kind changes
    TECHNOLOGY = "technology"  # the technology or its catalog component changes
    ROUTING = "routing"  # a connection's endpoints change
    PROTOCOL = "protocol"  # a connection's protocol or interaction changes
    GROUPING = "grouping"  # a boundary or a node's parent changes
    PLACEMENT = "placement"  # region or zones change
    RESOURCES = "resources"
    SCALING = "scaling"  # replicas or autoscaling change
    CONFIGURATION = "configuration"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    UNRECOGNIZED = "unrecognized"  # a setting the IR does not define (preserved from an import)
    UNKNOWN_VALUES = "unknown_values"  # the set of values marked unknown changes
    TRACEABILITY = "traceability"
    METADATA = "metadata"


@dataclass(frozen=True, slots=True)
class MigrationChange:
    element: str  # node, connection, architecture, assumption or decision
    element_id: str  # the element's stable id ("" for the architecture itself)
    change: ChangeKind
    relevance: Relevance
    interpretation: Interpretation
    aspects: tuple[Aspect, ...]
    engines: tuple[str, ...] = ()  # the engines that read what it changes
    kind: str | None = None  # the node or connection kind (the target's, when it changed)
    label: str | None = None
    stateful: bool = False  # a database, cache, queue or storage node
    fields: tuple[FieldChange, ...] = ()  # the IR diff's field changes, as they are
    reason: str | None = None  # why it needs manual interpretation or is unsupported

    def __post_init__(self) -> None:
        check(
            [
                text(self.element, "change.element", 32),
                None if isinstance(self.element_id, str) else "change.element_id",
                None if isinstance(self.change, ChangeKind) else "change.change",
                None if isinstance(self.relevance, Relevance) else "change.relevance",
                None if isinstance(self.interpretation, Interpretation) else "change.interpretation",
                items(self.aspects, Aspect, "change.aspects"),
                items(self.engines, str, "change.engines"),
                items(self.fields, FieldChange, "change.fields"),
                text(self.reason, "change.reason", MAX_TEXT, required=False),
                "change.reason"
                if self.interpretation is not Interpretation.SUPPORTED and not self.reason
                else None,
            ]
        )
        object.__setattr__(self, "aspects", tuple(sorted(set(self.aspects))))
        object.__setattr__(self, "engines", tuple(sorted(set(self.engines))))

    @property
    def ref(self) -> str:
        """How steps, risks and findings trace to it, e.g. ``node:db:modified``."""
        return f"{self.element}:{self.element_id}:{self.change.value}"

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.element, self.element_id, self.change.value)

    def changed(self, prefix: str) -> tuple[FieldChange, ...]:
        """Its field changes under a path, e.g. ``configuration.replicas``."""
        return tuple(f for f in self.fields if f.field == prefix or f.field.startswith(prefix + "."))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "element": self.element,
            "element_id": self.element_id,
            "change": self.change.value,
            "relevance": self.relevance.value,
            "interpretation": self.interpretation.value,
            "aspects": [a.value for a in self.aspects],
            "engines": list(self.engines),
            "kind": self.kind,
            "label": self.label,
            "stateful": self.stateful,
            "reason": self.reason,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True, slots=True)
class ChangeAnalysis:
    """The source-to-target changes, classified, with the findings they give rise to."""

    source: SourceRef
    target: TargetRef
    diff_summary: str
    changes: tuple[MigrationChange, ...]
    findings: tuple[PlanFinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(sorted(self.changes, key=lambda c: c.sort_key)))
        object.__setattr__(self, "findings", tuple(sorted(set(self.findings), key=lambda f: f.sort_key)))

    @property
    def relevant(self) -> tuple[MigrationChange, ...]:
        """The changes a migration must carry out (metadata-only changes excluded)."""
        return tuple(c for c in self.changes if c.relevance is Relevance.MIGRATION)

    def by_ref(self) -> dict[str, MigrationChange]:
        return {c.ref: c for c in self.changes}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "diff_summary": self.diff_summary,
            "changes": [c.to_dict() for c in self.changes],
            "findings": [f.to_dict() for f in self.findings],
        }
