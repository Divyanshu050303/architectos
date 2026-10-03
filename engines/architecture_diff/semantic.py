"""The semantic diff of two architecture states (rule ``diff-semantic@1``): the IR's own diff, read field
by field, classified, and gathered into groups — never a comparison of its own.

**What changed** is the IR's ``diff``: elements matched by stable id (a renamed element is the same
element, ``renamed``; a changed id is a removal and an addition — no matching by name, position or
layout), fields compared as the IR serializes them, secret-looking values redacted. This module adds:

- **value types and units**: a typed property's numbers are numbers (canonical decimals included); a
  unit is stated only when the property's name states it (``_bytes``, ``_seconds``, ``_cores``,
  ``_ratio``, ``_per_second``);
- **sensitivity**: a field whose path looks like a secret (the IR diff's rule) is ``secret``: its change
  is reported, its values never are;
- **classes**: what each field concerns — from the IR diff's category and, for a typed property, the
  engines that declare it as an input (the same sets migration planning reads). A class never says
  what a change does;
- **groups** (each change in exactly one, with the rule that formed it):

  1. ``connected_changes``: changes to nodes and connections, joined when they concern the same node or
     a changed connection joins their nodes — one group per connected set;
  2. ``traceability``: changes that only touch requirement references;
  3. ``descriptive``: changes that only touch names, descriptions, metadata or provenance;
  4. ``recorded_reasoning``: assumptions and decision references;
  5. ``architecture``: the architecture's own fields.

**Bounds**: more than ``MAX_CHANGES`` changes is ``architecture_diff_too_large`` — refused, not cut.
"""

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from core.architecture_ir.configuration import CONNECTION_PROPERTIES, NODE_PROPERTIES
from core.architecture_ir.configuration import ValueType as PropertyType
from core.architecture_ir.diff import REDACTED, ChangeKind, ElementChange, FieldChange, diff, is_secret_path
from core.architecture_ir.errors import ElementType
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.architecture_diff.changes import (
    ARCHITECTURE,
    MAX_CHANGES,
    Change,
    ChangeGroup,
    FieldDelta,
    SemanticDiff,
    change_id,
)
from core.domain.architecture_diff.errors import DiffTooLarge
from core.domain.architecture_diff.values import ChangeClass, Sensitivity, ValueType
from engines.migration.changes import ENGINE_INPUTS, PLACEMENT, SCALING

RULE = "diff-semantic@1"
C = ChangeClass
PROPERTIES = {**NODE_PROPERTIES, **CONNECTION_PROPERTIES}
NUMERIC = frozenset({PropertyType.INTEGER, PropertyType.DECIMAL})
UNITS = (
    ("_per_second", "per_second"),
    ("_bytes", "bytes"),
    ("_seconds", "seconds"),
    ("_cores", "cores"),
    ("_ratio", "ratio"),
)
ENGINE_CLASSES = {
    "capacity": C.PERFORMANCE,
    "cost": C.COST,
    "reliability": C.RELIABILITY,
    "security": C.SECURITY,
    "observability": C.OBSERVABILITY,
}
CATEGORY_CLASSES = {
    "kind": C.STRUCTURAL,
    "technology": C.TECHNOLOGY,
    "endpoints": C.TOPOLOGY,
    "semantics": C.TOPOLOGY,
    "placement": C.TOPOLOGY,
    "traceability": C.REQUIREMENT,
    "lifecycle": C.OPERATIONAL,
    "metadata": C.METADATA,
    "provenance": C.METADATA,
    "description": C.METADATA,
}
DESCRIPTIVE = frozenset({C.METADATA})
TRACING = frozenset({C.REQUIREMENT, C.METADATA})
REASONING = frozenset({ElementType.ASSUMPTION, ElementType.DECISION})
GROUP_RULES = (
    ("traceability", "Requirement traceability", "These changes only touch requirement references."),
    (
        "descriptive",
        "Descriptive changes",
        "These changes only touch names, descriptions, metadata or provenance.",
    ),
    (
        "recorded_reasoning",
        "Assumptions and decisions",
        "Recorded reasoning: assumptions and decision references.",
    ),
    ("architecture", "The architecture itself", "Changes to the architecture's own fields."),
)


# --- fields ------------------------------------------------------------------------------------


def _property(path: str) -> str | None:
    head, _, rest = path.partition(".")
    if head != "configuration" or not rest or rest.startswith(("extra.", "unknown")):
        return None
    return rest.split(".", 1)[0]


def _unit(name: str | None) -> str | None:
    return next((unit for suffix, unit in UNITS if name and name.endswith(suffix)), None)


def _numeric_text(value: str) -> bool:
    try:
        Decimal(value)
    except InvalidOperation:
        return False
    return True


def _type(value: Any, numeric: bool) -> ValueType:
    """The first rule that holds, in order: absent, redacted, boolean, number, list, object, text."""
    rules: tuple[tuple[bool, ValueType], ...] = (
        (value is None, ValueType.ABSENT),
        (value == REDACTED, ValueType.REDACTED),
        (isinstance(value, bool), ValueType.BOOLEAN),
        (
            isinstance(value, int | float | Decimal)
            or (isinstance(value, str) and numeric and _numeric_text(value)),
            ValueType.NUMBER,
        ),
        (isinstance(value, list | tuple), ValueType.LIST),
        (isinstance(value, Mapping), ValueType.OBJECT),
    )
    return next((kind for holds, kind in rules if holds), ValueType.TEXT)


def _classes(field: FieldChange) -> tuple[ChangeClass, ...]:
    path, category = field.field, field.category
    if path.startswith(("configuration.extra.", "configuration.unknown")):
        return (C.UNKNOWN,)  # a setting the IR does not define, or the set of unknown values
    prop = _property(path)
    if prop is None:
        return (CATEGORY_CLASSES.get(category, C.METADATA),)
    found = [C.RESOURCES if category == "resources" else C.CONFIGURATION]
    if prop in SCALING:
        found.append(C.SCALING)
    if prop in PLACEMENT:
        found.append(C.OPERATIONAL)
    found += [cls for engine, cls in ENGINE_CLASSES.items() if prop in ENGINE_INPUTS[engine]]
    return tuple(dict.fromkeys(found))


def _delta(field: FieldChange) -> FieldDelta:
    secret = is_secret_path(field.field) or REDACTED in (field.before, field.after)
    prop = _property(field.field)
    spec = PROPERTIES.get(prop) if prop else None
    numeric = spec is not None and spec.type in NUMERIC
    before, after = field.before, field.after
    if secret:  # reported, never shown — even if a value slipped past the IR diff's redaction
        before = REDACTED if before is not None else None
        after = REDACTED if after is not None else None
    return FieldDelta(
        field.field,
        before,
        after,
        _type(before, numeric),
        _type(after, numeric),
        field.category,
        _classes(field),
        Sensitivity.SECRET if secret else Sensitivity.PUBLIC,
        _unit(prop),
    )


# --- elements ----------------------------------------------------------------------------------


def _endpoints(change: ElementChange, base: ArchitectureIR, target: ArchitectureIR) -> tuple[str, str] | None:
    if change.element is not ElementType.CONNECTION:
        return None
    connection = target.connection(change.element_id) or base.connection(change.element_id)
    return (connection.source_id, connection.target_id) if connection else None


def _change(change: ElementChange, base: ArchitectureIR, target: ArchitectureIR) -> Change:
    fields = tuple(_delta(f) for f in change.fields)
    if change.change is ChangeKind.MODIFIED:
        classes = tuple(dict.fromkeys(c for f in fields for c in f.classes))
    elif change.element in REASONING:
        classes = (C.METADATA,)
    elif change.element is ElementType.CONNECTION:
        classes = (C.STRUCTURAL, C.TOPOLOGY)
    else:
        classes = (C.STRUCTURAL,)
    renamed = change.change is ChangeKind.MODIFIED and any(f.field == "name" for f in change.fields)
    return Change(
        change_id(change.element, change.element_id),
        change.element,
        change.element_id,
        change.change,
        change.label,
        change.kind,
        classes,
        fields,
        renamed,
        _endpoints(change, base, target),
    )


def _architecture(fields: tuple[FieldChange, ...]) -> Change | None:
    if not fields:
        return None
    deltas = tuple(_delta(f) for f in fields)
    classes = tuple(dict.fromkeys(c for d in deltas for c in d.classes))
    element = ElementType.ARCHITECTURE
    own = change_id(element, ARCHITECTURE)
    return Change(own, element, ARCHITECTURE, ChangeKind.MODIFIED, None, None, classes, deltas)


# --- groups ------------------------------------------------------------------------------------


def _nodes_of(change: Change) -> set[str]:
    if change.element is ElementType.NODE:
        return {change.element_id}
    return set(change.endpoints or ())


def _connected(changes: list[Change]) -> list[list[Change]]:
    """Changes joined when they concern a common node (a connection concerns its endpoints)."""
    parent = list(range(len(changes)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_node: dict[str, int] = {}
    for index, change in enumerate(changes):
        for node in sorted(_nodes_of(change)):
            if node in by_node:
                parent[root(index)] = root(by_node[node])
            else:
                by_node[node] = index
    sets: dict[int, list[Change]] = {}
    for index, change in enumerate(changes):
        sets.setdefault(root(index), []).append(change)
    return list(sets.values())


def _title(changes: list[Change]) -> str:
    labels = sorted({c.label or c.element_id for c in changes if c.element is ElementType.NODE})
    labels = labels or sorted({c.label or c.element_id for c in changes})
    more = f" and {len(labels) - 3} more" if len(labels) > 3 else ""
    return f"Changes to {', '.join(labels[:3])}{more}"[:200]


def _groups(changes: Iterable[Change]) -> tuple[ChangeGroup, ...]:
    by_rule: dict[str, list[Change]] = {rule: [] for rule, _, _ in GROUP_RULES}
    structural: list[Change] = []
    for change in changes:
        classes = set(change.classes)
        if change.element in REASONING:
            by_rule["recorded_reasoning"].append(change)
        elif change.element is ElementType.ARCHITECTURE:
            by_rule["architecture"].append(change)
        elif classes <= DESCRIPTIVE:
            by_rule["descriptive"].append(change)
        elif classes <= TRACING:
            by_rule["traceability"].append(change)
        else:
            structural.append(change)
    reason = "These changes concern the same nodes, or nodes a changed connection joins."
    groups = [
        ChangeGroup.of("connected_changes", _title(members), reason, tuple(members))
        for members in _connected(structural)
    ]
    groups += [
        ChangeGroup.of(rule, title, why, tuple(by_rule[rule]))
        for rule, title, why in GROUP_RULES
        if by_rule[rule]
    ]
    return tuple(groups)


def semantic_diff(base: ArchitectureIR, target: ArchitectureIR) -> SemanticDiff:
    """What changed from ``base`` to ``target``: the same two states always give the same diff."""
    found = diff(base, target)
    changes = [_change(c, base, target) for c in found.changes()]
    own = _architecture(found.architecture)
    if own is not None:
        changes.append(own)
    if len(changes) > MAX_CHANGES:
        raise DiffTooLarge(details={"limit": "changes", "value": len(changes)})
    ordered = tuple(sorted(changes, key=lambda c: (c.element.value, c.element_id)))
    versions = {"semantic": RULE}
    return SemanticDiff(content_hash(base), content_hash(target), ordered, _groups(ordered), versions)
