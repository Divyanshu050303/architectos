"""The canonical JSON form of the Architecture IR.

Writing (``to_dict``, ``to_json``) is deterministic: every field is present (``null``, ``[]`` or
``{}`` when empty), collections are in canonical order, keys are sorted in ``to_json``, decimal
values are strings (``"0.5"``), whole numbers are integers, times are UTC ISO 8601. Equal
architectures therefore have byte-identical JSON and the same ``content_hash``.

Reading (``from_dict``, ``from_json``) is strict and explains itself:

- a document from an older schema is upgraded first (see ``versioning``); a newer one is refused;
- a field the schema does not define is refused (``unknown_field``): unrecognized *settings* are
  welcome, but in ``configuration.extra``, where they are preserved, never in the structure;
- a missing optional field takes its default; a missing required one is reported;
- numbers are accepted as JSON numbers or decimal strings and converted exactly;
- every problem of every element is collected and reported at once, each naming its element,
  id and field. Graph rules (dangling references, duplicate ids) are checked once every element
  is valid on its own, so they never report consequences of an element already rejected.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .component import NodeKind, Technology
from .configuration import (
    CONNECTION,
    CONNECTION_PROPERTIES,
    NODE_PROPERTIES,
    Configuration,
    ConfigValue,
    PropertySpec,
    ValueType,
)
from .dependency import ConnectionKind, Interaction
from .edge import Connection
from .errors import ElementType, InvalidArchitecture, Violation, raise_if
from .model import MAX_ASSUMPTIONS, MAX_CONNECTIONS, MAX_DECISIONS, MAX_NODES, ArchitectureIR
from .node import Lifecycle, Node
from .provenance import Provenance, ProvenanceSource
from .traceability import Assumption, DecisionRef, RequirementRef
from .values import MAX_JSON_DEPTH, Json, decimal_to_str
from .versioning import upgrade

# --- writing ---------------------------------------------------------------------------------------


def to_dict(ir: ArchitectureIR) -> dict[str, Any]:
    return {
        "schema_version": ir.schema_version,
        "name": ir.name,
        "description": ir.description,
        "metadata": dict(ir.metadata),
        "provenance": _provenance_dict(ir.provenance),
        "requirement_refs": _refs_dict(ir.requirement_refs),
        "nodes": [_node_dict(n) for n in ir.nodes],
        "connections": [_connection_dict(c) for c in ir.connections],
        "assumptions": [_assumption_dict(a) for a in ir.assumptions],
        "decisions": [
            {"decision_id": str(d.decision_id), "subject_ids": list(d.subject_ids)} for d in ir.decisions
        ],
    }


def to_json(ir: ArchitectureIR) -> str:
    return json.dumps(to_dict(ir), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(ir: ArchitectureIR) -> str:
    """SHA-256 of the canonical JSON: equal architectures, equal hashes."""
    return hashlib.sha256(to_json(ir).encode()).hexdigest()


def _provenance_dict(provenance: Provenance | None) -> dict[str, Any] | None:
    if provenance is None:
        return None
    recorded_at = provenance.recorded_at
    return {
        "source": provenance.source.value,
        "reference": provenance.reference,
        "confidence": decimal_to_str(provenance.confidence) if provenance.confidence is not None else None,
        "verified": provenance.verified,
        "inferred": provenance.inferred,
        "actor": provenance.actor,
        "recorded_at": recorded_at.astimezone(UTC).isoformat() if recorded_at else None,
    }


def _refs_dict(refs: tuple[RequirementRef, ...]) -> list[dict[str, Any]]:
    return [{"requirement_id": str(r.requirement_id), "version": r.version} for r in refs]


def _value_json(value: ConfigValue) -> Any:
    if isinstance(value, Decimal):
        return decimal_to_str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def _thaw(value: Json) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _configuration_dict(configuration: Configuration) -> dict[str, Any]:
    return {
        "values": {k: _value_json(v) for k, v in configuration.values.items()},
        "unknown": sorted(configuration.unknown),
        "extra": _thaw(configuration.extra),
    }


def _annotations_dict(element: Node | Connection) -> dict[str, Any]:
    return {
        "configuration": _configuration_dict(element.configuration),
        "requirement_refs": _refs_dict(element.requirement_refs),
        "metadata": dict(element.metadata),
        "provenance": _provenance_dict(element.provenance),
        "field_provenance": {k: _provenance_dict(v) for k, v in element.field_provenance.items()},
    }


def _node_dict(node: Node) -> dict[str, Any]:
    technology = node.technology
    return {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "description": node.description,
        "technology": {"name": technology.name, "version": technology.version} if technology else None,
        "component": node.component,
        "parent_id": node.parent_id,
        "lifecycle": node.lifecycle.value if node.lifecycle else None,
        **_annotations_dict(node),
    }


def _connection_dict(connection: Connection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "source_id": connection.source_id,
        "target_id": connection.target_id,
        "kind": connection.kind.value,
        "protocol": connection.protocol,
        "interaction": connection.interaction.value if connection.interaction else None,
        "bidirectional": connection.bidirectional,
        "critical": connection.critical,
        "name": connection.name,
        "description": connection.description,
        **_annotations_dict(connection),
    }


def _assumption_dict(assumption: Assumption) -> dict[str, Any]:
    return {
        "id": assumption.id,
        "statement": assumption.statement,
        "provenance": _provenance_dict(assumption.provenance),
        "subject_ids": list(assumption.subject_ids),
        "requirement_refs": _refs_dict(assumption.requirement_refs),
    }


# --- reading ---------------------------------------------------------------------------------------

ARCHITECTURE_FIELDS = frozenset(
    {
        "schema_version",
        "name",
        "description",
        "metadata",
        "provenance",
        "requirement_refs",
        "nodes",
        "connections",
        "assumptions",
        "decisions",
    }
)
_ANNOTATIONS = frozenset({"configuration", "requirement_refs", "metadata", "provenance", "field_provenance"})
NODE_JSON_FIELDS = _ANNOTATIONS | {
    "id",
    "kind",
    "name",
    "description",
    "technology",
    "component",
    "parent_id",
    "lifecycle",
}
CONNECTION_JSON_FIELDS = _ANNOTATIONS | {
    "id",
    "source_id",
    "target_id",
    "kind",
    "protocol",
    "interaction",
    "bidirectional",
    "critical",
    "name",
    "description",
}
ASSUMPTION_FIELDS = frozenset({"id", "statement", "provenance", "subject_ids", "requirement_refs"})
DECISION_FIELDS = frozenset({"decision_id", "subject_ids"})
PROVENANCE_FIELDS = frozenset(
    {"source", "reference", "confidence", "verified", "inferred", "actor", "recorded_at"}
)
TECHNOLOGY_FIELDS = frozenset({"name", "version"})
CONFIGURATION_FIELDS = frozenset({"values", "unknown", "extra"})
REFERENCE_FIELDS = frozenset({"requirement_id", "version"})

_INTEGER_TEXT = re.compile(r"^-?\d{1,18}$")
_DECIMAL_TEXT = re.compile(r"^-?\d{1,18}(?:\.\d{1,18})?$")

type Read[T] = tuple[T | None, list[Violation]]


def from_dict(data: object) -> ArchitectureIR:
    """The IR a JSON document describes, upgraded to the current schema; raises
    ``InvalidArchitecture`` with every problem found."""
    if not isinstance(data, Mapping):
        raise InvalidArchitecture(
            [
                Violation(
                    "not_an_object", "An architecture must be a JSON object.", None, ElementType.ARCHITECTURE
                )
            ]
        )
    document, _ = upgrade(data)
    return _architecture(document)


def from_json(text: str | bytes) -> ArchitectureIR:
    try:
        data = json.loads(text, parse_float=Decimal, parse_constant=_refuse_constant)
    except ValueError, RecursionError:  # RecursionError: absurdly deep nesting
        raise InvalidArchitecture(
            [
                Violation(
                    "malformed_json", "The architecture is not valid JSON.", None, ElementType.ARCHITECTURE
                )
            ]
        ) from None
    return from_dict(data)


def _refuse_constant(name: str) -> None:
    raise ValueError(name)  # NaN and Infinity are not numbers an architecture can use


# Converters: each returns the converted value, or the raw value for the model to report on.


def _enum[E: StrEnum](value: object, kind: type[E]) -> Any:
    try:
        return kind(value) if isinstance(value, str) else value
    except ValueError:
        return value


def _uuid(value: object) -> Any:
    try:
        return uuid.UUID(value) if isinstance(value, str) else value
    except ValueError:
        return value


def _decimal(value: object) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str) and _DECIMAL_TEXT.fullmatch(value.strip()):
        return Decimal(value.strip())
    return value


def _time(value: object) -> Any:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return value


def _tuple(value: object) -> Any:
    return tuple(value) if isinstance(value, list | tuple) else value


def _or(value: object, default: Any) -> Any:
    return default if value is None else value


def _structure_problems(
    data: Mapping[str, Any], allowed: frozenset[str], required: tuple[str, ...], what: str
) -> list[Violation]:
    problems = [
        Violation("unknown_field", f"{key!r} is not a field of {what}.", str(key))
        for key in sorted(data, key=str)
        if key not in allowed
    ]
    problems += [
        Violation("required", f"{key} is required.", key) for key in required if data.get(key) is None
    ]
    return problems


def _under(violations: list[Violation], prefix: str) -> list[Violation]:
    return [replace(v, field=f"{prefix}.{v.field}" if v.field else prefix) for v in violations]


def _attempt[T](build: Callable[[], T]) -> Read[T]:
    try:
        return build(), []
    except InvalidArchitecture as error:
        return None, list(error.violations)


def _not_an_object(what: str, field: str | None, element: ElementType | None = None) -> list[Violation]:
    return [Violation("not_an_object", f"{what} must be an object.", field, element)]


def _provenance(value: object, field: str) -> Read[Provenance]:
    if value is None:
        return None, []
    if not isinstance(value, Mapping):
        return None, _not_an_object(field, field)
    problems = _structure_problems(value, PROVENANCE_FIELDS, ("source",), "provenance")
    if problems:
        return None, _under(problems, field)
    confidence = value.get("confidence")
    provenance, problems = _attempt(
        lambda: Provenance(
            source=_enum(value["source"], ProvenanceSource),
            reference=value.get("reference"),
            confidence=_decimal(confidence) if confidence is not None else None,
            verified=_or(value.get("verified"), False),
            inferred=_or(value.get("inferred"), False),
            actor=value.get("actor"),
            recorded_at=_time(value.get("recorded_at")),
        )
    )
    return provenance, _under(problems, field)


def _reference(item: object, path: str) -> Read[RequirementRef]:
    if not isinstance(item, Mapping):
        return None, [Violation("invalid_reference", f"{path} must be an object.", path)]
    structure = _structure_problems(item, REFERENCE_FIELDS, ("requirement_id",), "a requirement reference")
    if structure:
        return None, _under(structure, path)
    ref, problems = _attempt(lambda: RequirementRef(_uuid(item["requirement_id"]), item.get("version")))
    return ref, _under(problems, path)


def _refs(
    value: object, field: str = "requirement_refs"
) -> tuple[tuple[RequirementRef, ...], list[Violation]]:
    if value is None:
        return (), []
    if not isinstance(value, list | tuple):
        return (), [Violation("invalid_reference", f"{field} must be a list.", field)]
    read = [_reference(item, f"{field}[{i}]") for i, item in enumerate(value)]
    return tuple(r for r, _ in read if r is not None), [p for _, found in read for p in found]


def _technology(value: object) -> Read[Technology]:
    if value is None:
        return None, []
    if not isinstance(value, Mapping):
        return None, _not_an_object("technology", "technology")
    problems = _structure_problems(value, TECHNOLOGY_FIELDS, ("name",), "a technology")
    if problems:
        return None, _under(problems, "technology")
    technology, problems = _attempt(lambda: Technology(value["name"], value.get("version")))
    return technology, _under(problems, "technology")


def _property_value(value: object, spec: PropertySpec | None) -> Any:
    if spec is None or spec.type is ValueType.TEXT_LIST:
        return _tuple(value)
    if spec.type is ValueType.INTEGER and isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()):
        return int(value.strip())
    if spec.type is ValueType.DECIMAL:
        converted = _decimal(value)
        return converted if not isinstance(converted, Decimal) or converted.is_finite() else value
    return value


def _preserved(value: object, depth: int = 0) -> object:
    """Unrecognized settings as found; fractional numbers become text so they keep every digit.
    Past the allowed depth the value is left for validation to refuse."""
    if depth > MAX_JSON_DEPTH + 1:
        return value
    if isinstance(value, Mapping):
        return {k: _preserved(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_preserved(v, depth + 1) for v in value]
    if isinstance(value, float | Decimal):
        number = Decimal(repr(value)) if isinstance(value, float) else value
        if not number.is_finite():
            return value
        return int(number) if number == number.to_integral_value() else decimal_to_str(number)
    return value


def _configuration(value: object, specs: Mapping[str, PropertySpec]) -> Read[Configuration]:
    if value is None:
        return Configuration(), []
    if not isinstance(value, Mapping):
        return None, _not_an_object("configuration", "configuration")
    problems = _structure_problems(value, CONFIGURATION_FIELDS, (), "a configuration")
    values, unknown, extra = (
        _or(value.get(k), d) for k, d in (("values", {}), ("unknown", []), ("extra", {}))
    )
    if not isinstance(values, Mapping) or not isinstance(extra, Mapping):
        problems.append(Violation("not_an_object", "values and extra must be objects.", None))
    if not isinstance(unknown, list | tuple) or not all(isinstance(u, str) for u in unknown):
        problems.append(Violation("invalid_value", "unknown must be a list of property names.", "unknown"))
    if problems:
        return None, _under(problems, "configuration")
    configuration, problems = _attempt(
        lambda: Configuration(
            {k: _property_value(v, specs.get(k)) for k, v in values.items()},
            frozenset(unknown),
            _preserved(extra),  # type: ignore[arg-type]
        )
    )
    return configuration, _under(problems, "configuration")


def _field_provenance(value: object) -> tuple[dict[str, Provenance], list[Violation]]:
    if value is None:
        return {}, []
    if not isinstance(value, Mapping):
        return {}, _not_an_object("field_provenance", "field_provenance")
    found: dict[str, Provenance] = {}
    problems: list[Violation] = []
    for key, item in value.items():
        path = f"field_provenance.{key}"
        provenance, item_problems = _provenance(item, path)
        if provenance is None and not item_problems:
            item_problems = [Violation("required", f"{path} cannot be null.", path)]
        problems += item_problems
        if provenance is not None:
            found[key] = provenance
    return found, problems


def _annotations(
    data: Mapping[str, Any], specs: Mapping[str, PropertySpec], owner: str | None
) -> tuple[dict[str, Any], list[Violation]]:
    """``owner``: the node kind or "connection" the configuration belongs to, if known, so its
    properties are checked together with every other problem of the element."""
    configuration, problems = _configuration(data.get("configuration"), specs)
    if configuration is not None and owner is not None:
        problems += _under(configuration.problems_for(specs, owner), "configuration")
    refs, found = _refs(data.get("requirement_refs"))
    problems += found
    provenance, found = _provenance(data.get("provenance"), "provenance")
    problems += found
    field_provenance, found = _field_provenance(data.get("field_provenance"))
    problems += found
    return {
        "configuration": configuration,
        "requirement_refs": refs,
        "metadata": _or(data.get("metadata"), {}),
        "provenance": provenance,
        "field_provenance": field_provenance,
    }, problems


def _element_id(data: Mapping[str, Any], key: str = "id") -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _element[T](
    data: object,
    path: str,
    element: ElementType,
    id_key: str,
    check: Callable[[Mapping[str, Any]], tuple[Callable[[], T], list[Violation]]],
) -> Read[T]:
    """Read one element: structure and nested values first, then the element itself, every
    violation placed on the element."""
    if not isinstance(data, Mapping):
        return None, _not_an_object(path, path, ElementType.ARCHITECTURE)
    build, problems = check(data)
    built: T | None = None
    if not problems:
        built, problems = _attempt(build)
    return built, [p.within(element, _element_id(data, id_key)) for p in problems]


def _node_parts(data: Mapping[str, Any]) -> tuple[Callable[[], Node], list[Violation]]:
    problems = _structure_problems(data, NODE_JSON_FIELDS, ("id", "kind", "name"), "a node")
    technology, found = _technology(data.get("technology"))
    kind = _enum(data.get("kind"), NodeKind)
    annotations, more = _annotations(
        data, NODE_PROPERTIES, kind.value if isinstance(kind, NodeKind) else None
    )
    return lambda: Node(
        id=data["id"],
        kind=_enum(data["kind"], NodeKind),
        name=data["name"],
        description=data.get("description"),
        technology=technology,
        component=data.get("component"),
        parent_id=data.get("parent_id"),
        lifecycle=_enum(data.get("lifecycle"), Lifecycle),
        **annotations,
    ), problems + found + more


def _connection_parts(data: Mapping[str, Any]) -> tuple[Callable[[], Connection], list[Violation]]:
    required = ("id", "source_id", "target_id", "kind")
    problems = _structure_problems(data, CONNECTION_JSON_FIELDS, required, "a connection")
    annotations, found = _annotations(data, CONNECTION_PROPERTIES, CONNECTION)
    return lambda: Connection(
        id=data["id"],
        source_id=data["source_id"],
        target_id=data["target_id"],
        kind=_enum(data["kind"], ConnectionKind),
        protocol=data.get("protocol"),
        interaction=_enum(data.get("interaction"), Interaction),
        bidirectional=_or(data.get("bidirectional"), False),
        critical=data.get("critical"),
        name=data.get("name"),
        description=data.get("description"),
        **annotations,
    ), problems + found


def _assumption_parts(data: Mapping[str, Any]) -> tuple[Callable[[], Assumption], list[Violation]]:
    problems = _structure_problems(
        data, ASSUMPTION_FIELDS, ("id", "statement", "provenance"), "an assumption"
    )
    provenance, found = _provenance(data.get("provenance"), "provenance")
    refs, more = _refs(data.get("requirement_refs"))
    return lambda: Assumption(
        id=data["id"],
        statement=data["statement"],
        provenance=provenance,  # type: ignore[arg-type]
        subject_ids=_tuple(_or(data.get("subject_ids"), ())),
        requirement_refs=refs,
    ), problems + found + more


def _decision_parts(data: Mapping[str, Any]) -> tuple[Callable[[], DecisionRef], list[Violation]]:
    problems = _structure_problems(data, DECISION_FIELDS, ("decision_id",), "a decision reference")
    return lambda: DecisionRef(
        _uuid(data["decision_id"]),
        _tuple(_or(data.get("subject_ids"), ())),
    ), problems


_COLLECTIONS: tuple[tuple[str, int, ElementType, str, Callable[[Mapping[str, Any]], Any]], ...] = (
    ("nodes", MAX_NODES, ElementType.NODE, "id", _node_parts),
    ("connections", MAX_CONNECTIONS, ElementType.CONNECTION, "id", _connection_parts),
    ("assumptions", MAX_ASSUMPTIONS, ElementType.ASSUMPTION, "id", _assumption_parts),
    ("decisions", MAX_DECISIONS, ElementType.DECISION, "decision_id", _decision_parts),
)


def _architecture(data: Mapping[str, Any]) -> ArchitectureIR:
    problems = _structure_problems(data, ARCHITECTURE_FIELDS, ("schema_version", "name"), "an architecture")
    provenance, found = _provenance(data.get("provenance"), "provenance")
    refs, more = _refs(data.get("requirement_refs"))
    problems = [p.within(ElementType.ARCHITECTURE, None) for p in problems + found + more]
    elements: dict[str, tuple[Any, ...]] = {}
    for name, limit, element, id_key, parts in _COLLECTIONS:
        items = _or(data.get(name), [])
        if not isinstance(items, list | tuple):
            problems.append(
                Violation("invalid_value", f"{name} must be a list.", name, ElementType.ARCHITECTURE)
            )
            continue
        if len(items) > limit:  # refused before reading any of them
            problems.append(
                Violation(
                    "too_many", f"An architecture has at most {limit} {name}.", name, ElementType.ARCHITECTURE
                )
            )
            continue
        read = [_element(item, f"{name}[{i}]", element, id_key, parts) for i, item in enumerate(items)]
        problems += [p for _, item_problems in read for p in item_problems]
        elements[name] = tuple(built for built, _ in read if built is not None)
    raise_if(problems)
    return ArchitectureIR(
        name=data["name"],
        description=data.get("description"),
        metadata=_or(data.get("metadata"), {}),
        provenance=provenance,
        requirement_refs=refs,
        schema_version=data["schema_version"],
        **elements,
    )
