"""What changed between two architecture states — the deterministic layer, built on the IR's own diff.

- A ``Change`` is one element (node, connection, assumption, decision reference, or the architecture
  itself) added, removed or modified, matched by its **stable id** — a renamed element is the same
  element, modified (``renamed``); an element whose id changed was removed and another added. Its id is
  derived from what it is (``ch_…`` of the element type and id), so the same pair of states always
  gives the same changes with the same ids.
- A ``FieldDelta`` is one field of it: its path (``configuration.replicas``), both values in canonical
  form, their type, a unit when the IR's property name states one, and whether it is a secret — a
  secret's change is reported, never its values.
- A ``ChangeGroup`` gathers related changes by a stated deterministic rule; every change is in
  exactly one group, so grouping never hides a change, and the group never alters what it holds.

Classes say what a change *concerns* (``scaling``, ``security``…), never what it does.
"""

from dataclasses import dataclass, field
from typing import Any

from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.errors import ElementType

from .values import (
    FINGERPRINT,
    KEY,
    SCHEMA_VERSION,
    ChangeClass,
    Sensitivity,
    ValueType,
    check,
    code,
    digest,
    items,
    text,
    texts,
)

MAX_CHANGES = 2000
MAX_FIELDS = 200
MAX_GROUPS = 500
MAX_VALUE_CHARS = 2000
ARCHITECTURE = "architecture"  # the element id of changes to the architecture's own fields
HIDDEN = frozenset({ValueType.REDACTED, ValueType.ABSENT})


def change_id(element: ElementType, element_id: str) -> str:
    return digest("ch", element.value, element_id)


def group_id(rule: str, change_ids: tuple[str, ...]) -> str:
    return digest("cg", rule, sorted(change_ids))


def _classes(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or not all(isinstance(c, ChangeClass) for c in values):
        return name
    return None if len(set(values)) == len(values) else name


def _value(value: object, name: str) -> str | None:
    """A canonical JSON value, bounded (a huge value is a malformed record, not a change to show)."""
    return None if len(repr(value)) <= MAX_VALUE_CHARS else name


def _hash(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and FINGERPRINT.fullmatch(value) else name


def _endpoints(value: object) -> str | None:
    if value is None:
        return None
    ok = (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(e, str) and KEY.fullmatch(e) for e in value)
    )
    return None if ok else "change.endpoints"


@dataclass(frozen=True, slots=True)
class FieldDelta:
    path: str  # within the element, e.g. "configuration.replicas", "technology.version"
    before: Any  # canonical JSON value; None when absent (``before_type`` says which)
    after: Any
    before_type: ValueType
    after_type: ValueType
    category: str  # the IR diff's category: resources, configuration, semantics, endpoints…
    classes: tuple[ChangeClass, ...]
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    unit: str | None = None  # stated by the property's name: bytes, seconds, cores, ratio…

    def __post_init__(self) -> None:
        secret = self.sensitivity is Sensitivity.SECRET
        hidden = self.before_type in HIDDEN and self.after_type in HIDDEN
        check(
            [
                text(self.path, "field.path", 300),
                _value(self.before, "field.before"),
                _value(self.after, "field.after"),
                None if isinstance(self.before_type, ValueType) else "field.before_type",
                None if isinstance(self.after_type, ValueType) else "field.after_type",
                code(self.category, "field.category"),
                _classes(self.classes, "field.classes"),
                None if isinstance(self.sensitivity, Sensitivity) else "field.sensitivity",
                code(self.unit, "field.unit", required=False),
                "field.sensitivity" if secret and not hidden else None,  # a secret never shows a value
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "before": self.before,
            "after": self.after,
            "before_type": self.before_type.value,
            "after_type": self.after_type.value,
            "category": self.category,
            "classes": [c.value for c in self.classes],
            "sensitivity": self.sensitivity.value,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class Change:
    id: str
    element: ElementType
    element_id: str
    change: ChangeKind
    label: str | None  # the element's name (the target's, when renamed), for display
    kind: str | None  # node or connection kind (the target's, when changed)
    classes: tuple[ChangeClass, ...]
    fields: tuple[FieldDelta, ...] = ()  # for a modified element
    renamed: bool = False  # its name changed; its identity did not
    endpoints: tuple[str, str] | None = None  # a connection's source and target ids (the target's)

    def __post_init__(self) -> None:
        modified = self.change is ChangeKind.MODIFIED
        known = isinstance(self.element, ElementType) and isinstance(self.element_id, str)
        check(
            [
                None if known and self.id == change_id(self.element, self.element_id) else "change.id",
                text(self.element_id, "change.element_id", 128),
                None if isinstance(self.change, ChangeKind) else "change.change",
                text(self.label, "change.label", 200, required=False),
                text(self.kind, "change.kind", 64, required=False),
                _classes(self.classes, "change.classes"),
                "change.classes" if not self.classes else None,
                items(self.fields, FieldDelta, "change.fields", MAX_FIELDS),
                "change.fields" if modified != bool(self.fields) else None,  # modified iff fields changed
                "change.renamed" if self.renamed and not modified else None,
                _endpoints(self.endpoints),
            ]
        )

    @property
    def secret(self) -> bool:
        return any(f.sensitivity is Sensitivity.SECRET for f in self.fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "element": self.element.value,
            "element_id": self.element_id,
            "change": self.change.value,
            "label": self.label,
            "kind": self.kind,
            "classes": [c.value for c in self.classes],
            "fields": [f.to_dict() for f in self.fields],
            "renamed": self.renamed,
            "endpoints": list(self.endpoints) if self.endpoints else None,
        }


@dataclass(frozen=True, slots=True)
class ChangeGroup:
    """Related changes, gathered by a deterministic rule that says why. A group's title is the rule's;
    an AI explanation may give it a better one, separately — never by changing this record."""

    id: str
    rule: str  # e.g. "shared_element", "connected_additions", "metadata"
    title: str
    reason: str  # why these changes are together, in the rule's words
    change_ids: tuple[str, ...]
    element_ids: tuple[str, ...]  # the elements its changes concern (connections' endpoints included)

    def __post_init__(self) -> None:
        ids = self.change_ids
        check(
            [
                code(self.rule, "group.rule"),
                texts(ids, "group.change_ids", 64),
                None if isinstance(ids, tuple) and ids and len(set(ids)) == len(ids) else "group.change_ids",
                None if isinstance(ids, tuple) and self.id == group_id(self.rule, ids) else "group.id",
                text(self.title, "group.title", 200),
                text(self.reason, "group.reason", 500),
                texts(self.element_ids, "group.element_ids", 128),
            ]
        )

    @classmethod
    def of(cls, rule: str, title: str, reason: str, changes: tuple[Change, ...]) -> ChangeGroup:
        ids = tuple(c.id for c in changes)
        elements = sorted({e for c in changes for e in (c.element_id, *(c.endpoints or ()))} - {ARCHITECTURE})
        return cls(group_id(rule, ids), rule, title, reason, ids, tuple(elements))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule": self.rule,
            "title": self.title,
            "reason": self.reason,
            "change_ids": list(self.change_ids),
            "element_ids": list(self.element_ids),
        }


@dataclass(frozen=True, slots=True)
class SemanticDiff:
    base_hash: str
    target_hash: str
    changes: tuple[Change, ...] = ()
    groups: tuple[ChangeGroup, ...] = ()
    versions: dict[str, Any] = field(default_factory=dict)  # the rules that produced it
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        ids = [c.id for c in self.changes if isinstance(c, Change)]
        grouped = [i for g in self.groups if isinstance(g, ChangeGroup) for i in g.change_ids]
        check(
            [
                _hash(self.base_hash, "diff.base_hash"),
                _hash(self.target_hash, "diff.target_hash"),
                items(self.changes, Change, "diff.changes", MAX_CHANGES),
                items(self.groups, ChangeGroup, "diff.groups", MAX_GROUPS),
                "diff.changes" if len(set(ids)) != len(ids) else None,
                # every change in exactly one group: grouping never hides or duplicates a change
                "diff.groups" if sorted(grouped) != sorted(ids) else None,
                "diff.changes" if self.base_hash == self.target_hash and self.changes else None,
            ]
        )

    @property
    def identical(self) -> bool:
        return not self.changes

    def change(self, wanted: str) -> Change | None:
        return next((c for c in self.changes if c.id == wanted), None)

    def counts(self) -> dict[str, int]:
        """Changes by kind and by class: counts only, never a score."""
        found: dict[str, int] = {k.value: 0 for k in ChangeKind}
        for change in self.changes:
            found[change.change.value] += 1
            for cls in change.classes:
                found[f"class:{cls.value}"] = found.get(f"class:{cls.value}", 0) + 1
        return found

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_hash": self.base_hash,
            "target_hash": self.target_hash,
            "schema_version": self.schema_version,
            "versions": dict(self.versions),
            "changes": [c.to_dict() for c in self.changes],
            "groups": [g.to_dict() for g in self.groups],
        }
