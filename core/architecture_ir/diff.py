"""Deterministic comparison of two architecture states.

Elements are matched by their stable ``id``, never by name or position: a renamed node is the
same node, *modified*; a node whose id changed was removed and another added. (Layout is not
part of the IR, so moving a box never appears here at all.)

Each modified element lists its changed fields with their before and after values (in canonical
JSON form, e.g. decimals as strings) and a category, so reviewers and engines can focus on what
matters to them: ``technology``, ``resources`` (replicas, CPU, memory, storage), ``configuration``,
``endpoints``, ``semantics`` (protocol, kind, interaction), ``metadata``, ``provenance``,
``traceability``, and so on.

The result is plain data, suitable for API responses, change review of AI proposals, audit
history, evolution and migration planning. The same two inputs always give the same diff.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import ElementType
from .model import ArchitectureIR
from .serialization import to_dict

RESOURCE_PROPERTIES = frozenset(
    {
        "replicas",
        "autoscaling_min_replicas",
        "autoscaling_max_replicas",
        "autoscaling_target_cpu_ratio",
        "cpu_request_cores",
        "cpu_limit_cores",
        "memory_request_bytes",
        "memory_limit_bytes",
        "storage_bytes",
        "instance_class",
        "max_connections",
        "partitions",
    }
)


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str  # e.g. "name", "configuration.replicas", "technology.version"
    before: Any  # canonical JSON value; None when the field was absent
    after: Any
    category: str

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "before": self.before, "after": self.after, "category": self.category}


@dataclass(frozen=True, slots=True)
class ElementChange:
    element: ElementType
    element_id: str
    change: ChangeKind
    label: str | None  # the element's name (the new one, if it changed), for display
    kind: str | None  # node or connection kind
    fields: tuple[FieldChange, ...] = ()  # for modified elements

    @property
    def categories(self) -> frozenset[str]:
        return frozenset(f.category for f in self.fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.element.value,
            "element_id": self.element_id,
            "change": self.change.value,
            "label": self.label,
            "kind": self.kind,
            "categories": sorted(self.categories),
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True, slots=True)
class ArchitectureDiff:
    architecture: tuple[FieldChange, ...] = ()  # name, description, metadata, provenance, requirement refs
    nodes: tuple[ElementChange, ...] = ()
    connections: tuple[ElementChange, ...] = ()
    assumptions: tuple[ElementChange, ...] = ()
    decisions: tuple[ElementChange, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.architecture or self.changes())

    def changes(self) -> tuple[ElementChange, ...]:
        return (*self.nodes, *self.connections, *self.assumptions, *self.decisions)

    def changed_ids(self) -> frozenset[str]:
        return frozenset(c.element_id for c in self.changes())

    def summary(self) -> str:
        """One deterministic line, e.g. "1 node added (Cache); 2 nodes modified; 1 connection added"."""
        if self.is_empty:
            return "No changes."
        parts: list[str] = []
        for noun, changes in (
            ("node", self.nodes),
            ("connection", self.connections),
            ("assumption", self.assumptions),
            ("decision", self.decisions),
        ):
            for kind in ChangeKind:
                selected = [c for c in changes if c.change is kind]
                if not selected:
                    continue
                count = len(selected)
                text = f"{count} {noun}{'s' if count > 1 else ''} {kind.value}"
                labels = [c.label for c in selected if c.label]
                if kind is not ChangeKind.MODIFIED and labels and count <= 3:
                    text += f" ({', '.join(labels)})"
                parts.append(text)
        if self.architecture:
            parts.append("architecture details changed")
        return "; ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "architecture": [f.to_dict() for f in self.architecture],
            "nodes": [c.to_dict() for c in self.nodes],
            "connections": [c.to_dict() for c in self.connections],
            "assumptions": [c.to_dict() for c in self.assumptions],
            "decisions": [c.to_dict() for c in self.decisions],
        }


# --- comparison ------------------------------------------------------------------------------------

_WHOLE = frozenset({"provenance", "requirement_refs", "subject_ids"})  # compared as one value


def _leaves(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Field path → value. Objects are walked, lists and provenance records compared whole;
    configuration values are addressed by name ("configuration.replicas")."""
    found: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}{key}"
        if path == "configuration":
            found.update(_leaves(item["values"], "configuration."))
            if item["unknown"]:
                found["configuration.unknown"] = item["unknown"]
            found.update(_leaves(item["extra"], "configuration.extra."))
        elif path == "field_provenance":
            found.update({f"field_provenance.{k}": v for k, v in item.items()})
        elif isinstance(item, Mapping) and key not in _WHOLE:
            found.update(_leaves(item, f"{path}."))  # an empty object adds no field
        else:
            found[path] = item
    return found


_CATEGORIES = {
    "technology": "technology",
    "component": "technology",
    "source_id": "endpoints",
    "target_id": "endpoints",
    "protocol": "semantics",
    "interaction": "semantics",
    "bidirectional": "semantics",
    "critical": "semantics",
    "kind": "kind",
    "metadata": "metadata",
    "provenance": "provenance",
    "field_provenance": "provenance",
    "requirement_refs": "traceability",
    "subject_ids": "traceability",
    "parent_id": "placement",
    "lifecycle": "lifecycle",
}


def _category(path: str) -> str:
    head, _, rest = path.partition(".")
    if head == "configuration":
        return "resources" if rest.split(".", 1)[0] in RESOURCE_PROPERTIES else "configuration"
    return _CATEGORIES.get(head, "description")  # name, description, statement


def _field_changes(
    before: Mapping[str, Any], after: Mapping[str, Any], skip: Iterable[str] = ()
) -> tuple[FieldChange, ...]:
    old, new = _leaves(before), _leaves(after)
    ignored = set(skip)
    return tuple(
        FieldChange(path, old.get(path), new.get(path), _category(path))
        for path in sorted(old.keys() | new.keys())
        if path not in ignored and old.get(path) != new.get(path)
    )


def _elements(
    element: ElementType,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    id_key: str = "id",
    label_key: str | None = "name",
) -> tuple[ElementChange, ...]:
    old = {e[id_key]: e for e in before}
    new = {e[id_key]: e for e in after}
    changes: list[ElementChange] = []
    for element_id in sorted(old.keys() | new.keys()):
        was, now = old.get(element_id), new.get(element_id)
        current: dict[str, Any] = now if now is not None else was  # type: ignore[assignment]
        label = current.get(label_key) if label_key else None
        kind = current.get("kind")
        if was is None:
            changes.append(ElementChange(element, element_id, ChangeKind.ADDED, label, kind))
        elif now is None:
            changes.append(ElementChange(element, element_id, ChangeKind.REMOVED, label, kind))
        else:
            fields = _field_changes(was, now, skip=(id_key,))
            if fields:
                changes.append(ElementChange(element, element_id, ChangeKind.MODIFIED, label, kind, fields))
    return tuple(changes)


_COLLECTIONS = frozenset({"nodes", "connections", "assumptions", "decisions", "schema_version"})


def diff(before: ArchitectureIR, after: ArchitectureIR) -> ArchitectureDiff:
    """What changed from ``before`` to ``after`` (both in the current IR schema)."""
    old, new = to_dict(before), to_dict(after)
    return ArchitectureDiff(
        architecture=_field_changes(
            {k: v for k, v in old.items() if k not in _COLLECTIONS},
            {k: v for k, v in new.items() if k not in _COLLECTIONS},
        ),
        nodes=_elements(ElementType.NODE, old["nodes"], new["nodes"]),
        connections=_elements(ElementType.CONNECTION, old["connections"], new["connections"]),
        assumptions=_elements(ElementType.ASSUMPTION, old["assumptions"], new["assumptions"], label_key=None),
        decisions=_elements(ElementType.DECISION, old["decisions"], new["decisions"], "decision_id", None),
    )
