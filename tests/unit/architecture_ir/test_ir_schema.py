"""The published JSON Schema: generated from the code, and matching what the code writes."""

from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from core.architecture_ir.schema import json_schema, render
from core.architecture_ir.serialization import to_dict

from .builders import api_and_postgres, discovered, service_cache_queue

SCHEMA_FILE = Path(__file__).resolve().parents[3] / "core" / "schemas" / "architecture.schema.json"


def validator() -> Draft202012Validator:
    Draft202012Validator.check_schema(json_schema())
    return Draft202012Validator(json_schema(), format_checker=FormatChecker())


def test_the_published_schema_is_up_to_date() -> None:
    assert SCHEMA_FILE.read_text() == render(), "run `make schemas`"


@pytest.mark.parametrize("build", [api_and_postgres, service_cache_queue, discovered])
def test_what_the_code_writes_matches_the_schema(build: Any) -> None:
    errors = [e.message for e in validator().iter_errors(to_dict(build()))]
    assert errors == []


def test_the_schema_refuses_what_the_code_refuses() -> None:
    data = to_dict(api_and_postgres())
    data["nodes"][0]["position"] = {"x": 1, "y": 2}
    data["nodes"][1]["configuration"]["values"]["colour"] = "blue"
    data["connections"][0]["kind"] = "teleport"
    assert len(list(validator().iter_errors(data))) == 3
