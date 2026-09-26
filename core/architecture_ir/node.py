"""A node: one component (service, database, broker, …) or boundary of the architecture.

Identity is ``id``: stable across revisions, independent of the display ``name`` (renaming a node
is a change to it, not a new node). Two nodes may share a name, never an id.

Visual position is *not* part of a node: layout is stored beside the architecture and never
changes it (moving a box is not an architecture change).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .component import NodeKind, Technology, component_problems
from .configuration import NODE_PROPERTIES, Configuration
from .errors import ElementType, Violation, raise_if
from .provenance import Provenance
from .traceability import RequirementRef, normalized_refs, refs_problems
from .values import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    clean_block,
    clean_line,
    frozen,
    id_problems,
    metadata_problems,
    text_problems,
)


class Lifecycle(StrEnum):
    PLANNED = "planned"  # designed, not built yet
    ACTIVE = "active"  # running
    DEPRECATED = "deprecated"  # still running, being replaced or retired


NODE_FIELDS = frozenset({"kind", "name", "description", "technology", "component", "parent_id", "lifecycle"})


def normalize_annotations(element: object) -> None:
    """Sort references and freeze mappings of a node or connection being constructed."""
    object.__setattr__(element, "requirement_refs", normalized_refs(element.requirement_refs))  # type: ignore[attr-defined]
    for name in ("metadata", "field_provenance"):
        value = getattr(element, name)
        if isinstance(value, Mapping):
            object.__setattr__(element, name, frozen(value))


def annotation_problems(
    requirement_refs: object,
    metadata: object,
    provenance: object,
    field_provenance: object,
    fields: frozenset[str],
    configuration: Configuration,
) -> list[Violation]:
    """Traceability, labels and provenance: the parts every element shares."""
    problems = refs_problems(requirement_refs)
    problems += metadata_problems(metadata)
    if provenance is not None and not isinstance(provenance, Provenance):
        problems.append(Violation("invalid_value", "provenance must be a provenance record.", "provenance"))
    if not isinstance(field_provenance, Mapping):
        problems.append(Violation("not_an_object", "field_provenance must be an object.", "field_provenance"))
        return problems
    known = fields | {f"configuration.{key}" for key in configuration.property_names()}
    for key, value in field_provenance.items():
        path = f"field_provenance.{key}"
        if key not in known:
            problems.append(Violation("unknown_field", f"{key} is not a field of this element.", path))
        if not isinstance(value, Provenance):
            problems.append(Violation("invalid_value", f"{path} must be a provenance record.", path))
    return problems


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    kind: NodeKind
    name: str
    description: str | None = None
    technology: Technology | None = None
    component: str | None = None  # component-catalog path, e.g. "databases/postgresql"
    parent_id: str | None = None  # the boundary containing this node
    configuration: Configuration = field(default_factory=Configuration)
    requirement_refs: tuple[RequirementRef, ...] = ()
    lifecycle: Lifecycle | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    provenance: Provenance | None = None  # None: the architecture's provenance applies
    field_provenance: Mapping[str, Provenance] = field(default_factory=dict)  # e.g. "configuration.replicas"

    def __post_init__(self) -> None:
        if isinstance(self.name, str):
            object.__setattr__(self, "name", clean_line(self.name))
        if isinstance(self.description, str):
            object.__setattr__(self, "description", clean_block(self.description) or None)
        normalize_annotations(self)
        raise_if(self.problems())

    def problems(self) -> list[Violation]:
        problems = id_problems(self.id, "id")
        if not isinstance(self.kind, NodeKind):
            problems.append(Violation("invalid_kind", f"kind must be one of: {', '.join(NodeKind)}.", "kind"))
        problems += text_problems(self.name, "name", MAX_NAME_LENGTH, required=True)
        problems += text_problems(
            self.description, "description", MAX_DESCRIPTION_LENGTH, required=False, block=True
        )
        if self.technology is not None and not isinstance(self.technology, Technology):
            problems.append(Violation("invalid_technology", "technology must be a technology.", "technology"))
        problems += component_problems(self.component)
        if self.parent_id is not None:
            problems += id_problems(self.parent_id, "parent_id")
            if self.parent_id == self.id:
                problems.append(Violation("containment_cycle", "A node cannot contain itself.", "parent_id"))
        if self.lifecycle is not None and not isinstance(self.lifecycle, Lifecycle):
            problems.append(
                Violation("invalid_value", f"lifecycle must be one of: {', '.join(Lifecycle)}.", "lifecycle")
            )
        if not isinstance(self.configuration, Configuration):
            problems.append(
                Violation("invalid_value", "configuration must be a configuration.", "configuration")
            )
            return [p.within(ElementType.NODE, self.id) for p in problems]
        if isinstance(self.kind, NodeKind):
            problems += [
                p.within(ElementType.NODE, self.id, "configuration")
                for p in self.configuration.problems_for(NODE_PROPERTIES, self.kind.value)
            ]
        problems += annotation_problems(
            self.requirement_refs,
            self.metadata,
            self.provenance,
            self.field_provenance,
            NODE_FIELDS,
            self.configuration,
        )
        return [p.within(ElementType.NODE, self.id) for p in problems]
