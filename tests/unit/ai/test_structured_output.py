from typing import Any

import jsonschema
import pytest

from ai.agents.architecture_agent import SCHEMA
from ai.llm.structured_output import UnsupportedSchema, problems, relaxed

SMALL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "tags"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 5, "pattern": "^[a-z]+$"},
        "size": {"type": "integer", "minimum": 1, "maximum": 3},
        "tags": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": ["a", "b"]}},
        "value": {"anyOf": [{"type": "number"}, {"type": "boolean"}]},
        "maximum": {"type": "string"},  # a property named like a keyword
    },
}

SAMPLES: list[Any] = [
    {"name": "abc", "tags": []},
    {"name": "abc", "tags": ["a", "b"], "size": 2, "value": True, "maximum": "x"},
    {"name": "", "tags": []},
    {"name": "toolong", "tags": []},
    {"name": "ABC", "tags": []},
    {"name": "abc", "tags": ["c"]},
    {"name": "abc", "tags": ["a", "a", "a"]},
    {"name": "abc", "tags": [], "size": 0},
    {"name": "abc", "tags": [], "size": True},
    {"name": "abc", "tags": [], "size": 1.5},
    {"name": "abc", "tags": [], "value": "x"},
    {"name": "abc", "tags": [], "extra": 1},
    {"name": "abc"},
    ["not", "an", "object"],
    None,
]


@pytest.mark.parametrize("sample", SAMPLES)
def test_agrees_with_jsonschema(sample: Any) -> None:
    expected = jsonschema.Draft202012Validator(SMALL).is_valid(sample)
    assert (not problems(sample, SMALL)) is expected


def test_problems_name_paths_not_values() -> None:
    found = problems({"name": "abc", "tags": ["zzz-secret"], "sneaky-field": "x"}, SMALL)
    assert ("$.tags[0]", "is not an allowed value") in found
    assert ("$", "has an unexpected field") in found
    assert "zzz-secret" not in str(found)
    assert "sneaky-field" not in str(found)


def test_unsupported_keywords_are_refused() -> None:
    with pytest.raises(UnsupportedSchema):
        problems("x", {"type": "string", "format": "email"})


def test_relaxed_keeps_the_shape_only() -> None:
    shape = relaxed(SMALL)
    assert shape["properties"]["name"] == {"type": "string"}
    assert shape["properties"]["size"] == {"type": "integer"}
    assert "maximum" in shape["properties"]  # a property, not a constraint
    assert shape["required"] == ["name", "tags"]
    assert shape["additionalProperties"] is False


def _closed(schema: Any) -> bool:
    if isinstance(schema, dict):
        if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
            return False
        return all(_closed(v) for v in schema.values())
    if isinstance(schema, list):
        return all(_closed(v) for v in schema)
    return True


def test_the_agent_schema_is_valid_and_closed() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert _closed(SCHEMA)
