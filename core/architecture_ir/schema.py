"""JSON Schema (draft 2020-12) of the canonical IR document, generated from the same enums and
property specifications the model uses, so the published schema cannot drift from the code.

It describes the canonical form ``to_json`` writes (every field present, decimals as strings).
It is a contract for other tools and languages; the authority remains ``serialization.from_dict``,
which also enforces what JSON Schema cannot express simply: which properties apply to which node
kind, and the graph rules (unique ids, no dangling references, …).

Regenerate with ``make schemas`` (a test fails if the file is out of date).
"""

import json
import sys
from collections.abc import Mapping
from typing import Any

from .component import COMPONENT_REFERENCE, TECHNOLOGY_NAME, TECHNOLOGY_VERSION, NodeKind
from .configuration import CONNECTION_PROPERTIES, NODE_PROPERTIES, PROPERTY_KEY, PropertySpec, ValueType
from .dependency import PROTOCOL, ConnectionKind, Interaction
from .model import MAX_ASSUMPTIONS, MAX_CONNECTIONS, MAX_DECISIONS, MAX_NODES
from .node import Lifecycle
from .provenance import ProvenanceSource
from .values import ELEMENT_ID, MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, METADATA_KEY
from .versioning import IR_SCHEMA_VERSION

SCHEMA_ID = f"urn:architectos:architecture-ir:{IR_SCHEMA_VERSION}"
DECIMAL_STRING = r"^-?\d+(\.\d+)?$"
UUID_STRING = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _object(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(properties),
        "properties": dict(properties),
    }


def _property_schema(spec: PropertySpec) -> dict[str, Any]:
    schema: dict[str, Any]
    match spec.type:
        case ValueType.INTEGER:
            schema = {"type": "integer"}
            if spec.minimum is not None:
                schema["minimum"] = int(spec.minimum)
            if spec.maximum is not None:
                schema["maximum"] = int(spec.maximum)
        case ValueType.DECIMAL:
            schema = {"type": "string", "pattern": DECIMAL_STRING}
        case ValueType.BOOLEAN:
            schema = {"type": "boolean"}
        case ValueType.CHOICE:
            schema = {"enum": sorted(spec.choices)}
        case ValueType.TEXT:
            schema = {"type": "string", "minLength": 1}
            if spec.pattern is not None:
                schema["pattern"] = spec.pattern.pattern
        case ValueType.TEXT_LIST:
            item: dict[str, Any] = {"type": "string"}
            if spec.pattern is not None:
                item["pattern"] = spec.pattern.pattern
            schema = {"type": "array", "items": item}
    return {"description": spec.description, **schema}


def _configuration(specs: Mapping[str, PropertySpec]) -> dict[str, Any]:
    return _object(
        {
            "values": {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: _property_schema(spec) for name, spec in sorted(specs.items())},
            },
            "unknown": {"type": "array", "items": {"enum": sorted(specs)}, "uniqueItems": True},
            "extra": {"type": "object", "propertyNames": {"pattern": PROPERTY_KEY.pattern}},
        }
    )


def _annotations(configuration: str) -> dict[str, Any]:
    return {
        "configuration": _ref(configuration),
        "requirement_refs": {"type": "array", "items": _ref("requirement_ref")},
        "metadata": _ref("metadata"),
        "provenance": _nullable(_ref("provenance")),
        "field_provenance": {"type": "object", "additionalProperties": _ref("provenance")},
    }


def json_schema() -> dict[str, Any]:
    text = {"type": "string"}
    ids = {"type": "array", "items": _ref("id")}
    definitions = {
        "id": {"type": "string", "pattern": ELEMENT_ID.pattern},
        "metadata": {
            "type": "object",
            "propertyNames": {"pattern": METADATA_KEY.pattern},
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "provenance": _object(
            {
                "source": {"enum": [s.value for s in ProvenanceSource]},
                "reference": _nullable(text),
                "confidence": _nullable({"type": "string", "pattern": DECIMAL_STRING}),
                "verified": {"type": "boolean"},
                "inferred": {"type": "boolean"},
                "actor": _nullable(text),
                "recorded_at": _nullable({"type": "string", "format": "date-time"}),
            }
        ),
        "requirement_ref": _object(
            {
                "requirement_id": {"type": "string", "pattern": UUID_STRING},
                "version": _nullable({"type": "integer", "minimum": 1}),
            }
        ),
        "technology": _object(
            {
                "name": {"type": "string", "pattern": TECHNOLOGY_NAME.pattern},
                "version": _nullable({"type": "string", "pattern": TECHNOLOGY_VERSION.pattern}),
            }
        ),
        "node_configuration": _configuration(NODE_PROPERTIES),
        "connection_configuration": _configuration(CONNECTION_PROPERTIES),
        "node": _object(
            {
                "id": _ref("id"),
                "kind": {"enum": [k.value for k in NodeKind]},
                "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
                "description": _nullable({"type": "string", "maxLength": MAX_DESCRIPTION_LENGTH}),
                "technology": _nullable(_ref("technology")),
                "component": _nullable({"type": "string", "pattern": COMPONENT_REFERENCE.pattern}),
                "parent_id": _nullable(_ref("id")),
                "lifecycle": _nullable({"enum": [x.value for x in Lifecycle]}),
                **_annotations("node_configuration"),
            }
        ),
        "connection": _object(
            {
                "id": _ref("id"),
                "source_id": _ref("id"),
                "target_id": _ref("id"),
                "kind": {"enum": [k.value for k in ConnectionKind]},
                "protocol": _nullable({"type": "string", "pattern": PROTOCOL.pattern}),
                "interaction": _nullable({"enum": [x.value for x in Interaction]}),
                "bidirectional": {"type": "boolean"},
                "critical": _nullable({"type": "boolean"}),
                "name": _nullable({"type": "string", "maxLength": MAX_NAME_LENGTH}),
                "description": _nullable({"type": "string", "maxLength": MAX_DESCRIPTION_LENGTH}),
                **_annotations("connection_configuration"),
            }
        ),
        "assumption": _object(
            {
                "id": _ref("id"),
                "statement": {"type": "string", "minLength": 1},
                "provenance": _ref("provenance"),
                "subject_ids": ids,
                "requirement_refs": {"type": "array", "items": _ref("requirement_ref")},
            }
        ),
        "decision": _object({"decision_id": {"type": "string", "pattern": UUID_STRING}, "subject_ids": ids}),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "ArchitectOS Architecture IR",
        "description": (
            f"Canonical form of an ArchitectOS architecture, schema version {IR_SCHEMA_VERSION}. "
            "Generated from core/architecture_ir; kind applicability and graph rules are enforced "
            "by the validator, not by this schema."
        ),
        **_object(
            {
                "schema_version": {"const": IR_SCHEMA_VERSION},
                "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
                "description": _nullable({"type": "string", "maxLength": MAX_DESCRIPTION_LENGTH}),
                "metadata": _ref("metadata"),
                "provenance": _nullable(_ref("provenance")),
                "requirement_refs": {"type": "array", "items": _ref("requirement_ref")},
                "nodes": {"type": "array", "items": _ref("node"), "maxItems": MAX_NODES},
                "connections": {"type": "array", "items": _ref("connection"), "maxItems": MAX_CONNECTIONS},
                "assumptions": {"type": "array", "items": _ref("assumption"), "maxItems": MAX_ASSUMPTIONS},
                "decisions": {"type": "array", "items": _ref("decision"), "maxItems": MAX_DECISIONS},
            }
        ),
        "$defs": definitions,
    }


def render() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())
