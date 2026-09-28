"""The published JSON Schema of a component specification (``core/schemas/component.schema.json``),
generated from the code so the two cannot drift: its vocabularies are the code's enumerations. It
describes the structure; the rules that relate fields (provenance kinds, support states, sources)
are the domain's and are checked when a specification is read."""

import json
import sys
from typing import Any

from core.architecture_ir.component import COMPONENT_REFERENCE, TECHNOLOGY_NAME, NodeKind
from core.architecture_ir.configuration import CONNECTION_PROPERTIES, NODE_PROPERTIES
from core.domain.capacity.units import UNITS
from core.domain.cost.pricing import PricingUnit

from .capabilities import CAPABILITIES, SCALING_METHODS, SECURITY_PROPERTIES, CapabilityState
from .entities import CATEGORIES, CODE, MAX_ITEMS, Engine, Hosting, ProvenanceKind, SupportStatus
from .specifications import (
    Effect,
    OperationArea,
    Responsibility,
    Scope,
    SignalAvailability,
    SignalType,
)

SPECIFICATION_SCHEMA_VERSION = 1
SCHEMA_ID = f"urn:architectos:component-specification:{SPECIFICATION_SCHEMA_VERSION}"


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _enum(values: Any) -> dict[str, Any]:
    return {"enum": sorted(v.value if hasattr(v, "value") else v for v in values)}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _list(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items, "maxItems": MAX_ITEMS}


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def json_schema() -> dict[str, Any]:
    text = {"type": "string", "minLength": 1}
    code = {"type": "string", "pattern": CODE.pattern}
    decimal = {
        "anyOf": [{"type": "integer", "minimum": 0}, {"type": "string", "pattern": r"^[0-9]+(\.[0-9]+)?$"}]
    }
    provenanced = {"provenance": _ref("provenance")}
    definitions: dict[str, Any] = {
        "provenance": _object(
            {
                "kind": _enum(ProvenanceKind),
                "sources": _list(code),
                "assumptions": _list(text),
                "model": _nullable(text),
                "note": _nullable(text),
            },
            ["kind"],
        ),
        "source": _object(
            {
                "id": code,
                "name": text,
                "reference": text,
                "version": _nullable(text),
                "published": _nullable({"type": "string", "format": "date"}),
                "retrieved": _nullable({"type": "string", "format": "date"}),
            },
            ["id", "name", "reference"],
        ),
        "capability": _object(
            {
                "id": _enum(CAPABILITIES),
                "state": _enum(CapabilityState),
                "requires": _list(text),
                "note": _nullable(text),
                **provenanced,
            },
            ["id", "state", "provenance"],
        ),
        "configuration_field": _object(
            {
                "property": _enum(NODE_PROPERTIES),
                "required": {"type": "boolean"},
                "user_configurable": {"type": "boolean"},
                "engines": _list(_enum(Engine)),
                "default": _nullable({"type": ["integer", "string", "boolean", "array"]}),
                "description": _nullable(text),
                **provenanced,
            },
            ["property"],
        ),
        "capacity_dimension": _object(
            {
                "id": code,
                "unit": _enum(UNITS),
                "scope": _enum(Scope),
                "value": _nullable(decimal),
                "property": _nullable(_enum(NODE_PROPERTIES)),
                "assumptions": _list(text),
                "description": _nullable(text),
                **provenanced,
            },
            ["id", "unit", "scope", "provenance"],
        ),
        "scaling_method": _object(
            {
                "id": _enum(SCALING_METHODS),
                "state": _enum(CapabilityState),
                "requires": _list(text),
                "limitations": _list(text),
                "affects": _list(_enum(Effect)),
                "evaluated_by": _list(_enum(Engine)),
                **provenanced,
            },
            ["id", "state", "provenance"],
        ),
        "failure_mode": _object(
            {
                "id": code,
                "description": text,
                "impact": text,
                "preconditions": _list(text),
                "signals": _list(code),
                "mitigations": _list(text),
                **provenanced,
            },
            ["id", "description", "impact", "provenance"],
        ),
        "signal": _object(
            {
                "id": code,
                "type": _enum(SignalType),
                "availability": _enum(SignalAvailability),
                "unit": _nullable(_enum(UNITS)),
                "collection": _nullable(text),
                "description": _nullable(text),
                **provenanced,
            },
            ["id", "type", "availability", "provenance"],
        ),
        "security_property": _object(
            {
                "id": _enum(SECURITY_PROPERTIES),
                "state": _enum(CapabilityState),
                "property": _nullable(_enum(set(NODE_PROPERTIES) | set(CONNECTION_PROPERTIES))),
                "requires": _list(text),
                "note": _nullable(text),
                **provenanced,
            },
            ["id", "state", "provenance"],
        ),
        "billing_dimension": _object(
            {
                "id": code,
                "unit": _enum(PricingUnit),
                "driver": _nullable(_enum(NODE_PROPERTIES)),
                "description": _nullable(text),
                **provenanced,
            },
            ["id", "unit", "provenance"],
        ),
        "operational_consideration": _object(
            {
                "area": _enum(OperationArea),
                "responsibility": _enum(Responsibility),
                "statement": text,
                **provenanced,
            },
            ["area", "responsibility", "statement", "provenance"],
        ),
    }
    root = _object(
        {
            "id": {"type": "string", "pattern": COMPONENT_REFERENCE.pattern},
            "version": {"type": "integer", "minimum": 1},
            "name": text,
            "category": _enum(CATEGORIES),
            "technology": {"type": "string", "pattern": TECHNOLOGY_NAME.pattern},
            "node_kinds": {**_list(_enum(NodeKind)), "minItems": 1},
            "support_status": _enum(SupportStatus),
            "description": text,
            "provider": _object({"name": code, "service": text}, ["name", "service"]),
            "hosting": _nullable(_enum(Hosting)),
            "aliases": _list(text),
            "technology_versions": _list(text),
            "replaced_by": _nullable({"type": "string", "pattern": COMPONENT_REFERENCE.pattern}),
            "capabilities": _list(_ref("capability")),
            "configuration": _list(_ref("configuration_field")),
            "capacity": _list(_ref("capacity_dimension")),
            "scaling": _list(_ref("scaling_method")),
            "failure_modes": _list(_ref("failure_mode")),
            "signals": _list(_ref("signal")),
            "security": _list(_ref("security_property")),
            "billing": _list(_ref("billing_dimension")),
            "operations": _list(_ref("operational_consideration")),
            "sources": _list(_ref("source")),
        },
        [
            "id",
            "version",
            "name",
            "category",
            "technology",
            "node_kinds",
            "support_status",
            "description",
            "provider",
        ],
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "ArchitectOS component specification",
        **root,
        "$defs": definitions,
    }


def render() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())
