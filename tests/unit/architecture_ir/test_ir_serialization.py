"""Canonical JSON, strict reading and schema versions (Architecture IR phase 2)."""

import copy
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import MAX_NODES, ArchitectureIR
from core.architecture_ir.serialization import content_hash, from_dict, from_json, to_dict, to_json
from core.architecture_ir.validation import requirement_problems, validate
from core.architecture_ir.versioning import IR_SCHEMA_VERSION, schema_version_of, upgrade

from .builders import LATENCY, THROUGHPUT, api_and_postgres, discovered, service_cache_queue

EXAMPLES = {
    "api_postgres": api_and_postgres,
    "service_cache_queue": service_cache_queue,
    "discovered": discovered,
}


def problems(data: object) -> list[tuple[Any, ...]]:
    with pytest.raises(InvalidArchitecture) as raised:
        from_dict(data)
    return [(v.element, v.element_id, v.field, v.rule) for v in raised.value.violations]


def minimal(**overrides: Any) -> dict[str, Any]:
    return {"schema_version": IR_SCHEMA_VERSION, "name": "Minimal"} | overrides


# --- round trips -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", EXAMPLES)
def test_a_round_trip_preserves_the_architecture(name: str) -> None:
    ir = EXAMPLES[name]()
    again = from_json(to_json(ir))
    assert again == ir
    assert to_json(again) == to_json(ir)
    assert content_hash(again) == content_hash(ir)
    assert from_dict(json.loads(to_json(ir))) == ir  # through plain JSON types as well


def test_the_json_is_canonical() -> None:
    ir = service_cache_queue()
    reordered = ArchitectureIR(
        name=ir.name,
        description=ir.description,
        nodes=tuple(reversed(ir.nodes)),
        connections=tuple(reversed(ir.connections)),
        assumptions=ir.assumptions,
        requirement_refs=tuple(reversed(ir.requirement_refs)),
    )
    assert to_json(reordered) == to_json(ir)
    assert content_hash(reordered) == content_hash(ir)
    assert content_hash(api_and_postgres()) != content_hash(ir)


def test_values_are_written_exactly() -> None:
    data = to_dict(service_cache_queue())
    api = next(n for n in data["nodes"] if n["id"] == "api")
    values = api["configuration"]["values"]
    assert values["cpu_request_cores"] == "0.25"  # decimals are strings
    assert values["autoscaling_target_cpu_ratio"] == "0.7"
    assert values["replicas"] == 4  # whole numbers are numbers
    assert values["availability_zones"] == ["eu-west-1a", "eu-west-1b"]
    db = to_dict(discovered())["nodes"][0]
    assert db["provenance"]["recorded_at"] == "2026-09-26T08:30:00+00:00"
    assert db["configuration"]["unknown"] == ["backup_retention_seconds", "max_connections"]
    assert db["configuration"]["extra"]["iops"] == 3000


def test_every_field_is_present_even_when_empty() -> None:
    data = to_dict(api_and_postgres())
    web = next(n for n in data["nodes"] if n["id"] == "web")
    assert web["technology"] is None
    assert web["configuration"] == {"values": {}, "unknown": [], "extra": {}}
    assert (web["field_provenance"], web["requirement_refs"], data["assumptions"]) == ({}, [], [])


# --- reading ---------------------------------------------------------------------------------------


def test_missing_optional_fields_take_their_defaults() -> None:
    ir = from_dict(
        minimal(
            nodes=[
                {"id": "api", "kind": "service", "name": "API"},
                {"id": "db", "kind": "database", "name": "DB"},
            ],
            connections=[{"id": "api-db", "source_id": "api", "target_id": "db", "kind": "data_access"}],
        )
    )
    assert ir.node("api").configuration.values == {}  # type: ignore[union-attr]
    assert ir.connection("api-db").bidirectional is False  # type: ignore[union-attr]


def test_numbers_are_read_exactly_from_numbers_or_strings() -> None:
    ir = from_json(
        json.dumps(
            minimal(
                nodes=[
                    {
                        "id": "api",
                        "kind": "service",
                        "name": "API",
                        "configuration": {
                            "values": {"cpu_limit_cores": 0.1, "cpu_request_cores": "0.05", "replicas": "3"}
                        },
                        "provenance": {"source": "llm_proposal", "confidence": 0.85},
                    }
                ]
            )
        )
    )
    api = ir.node("api")
    assert api is not None
    assert api.configuration.values == {
        "cpu_limit_cores": Decimal("0.1"),  # not 0.1000000000000000055…
        "cpu_request_cores": Decimal("0.05"),
        "replicas": 3,
    }
    assert api.provenance.confidence == Decimal("0.85")  # type: ignore[union-attr]


def test_unrecognized_settings_are_preserved_and_fractions_kept_as_text() -> None:
    extra = {"iops": 3000, "ratio": 0.125, "flags": {"a": [1, 2.5]}, "note": "keep me"}
    ir = from_json(
        json.dumps(
            minimal(nodes=[{"id": "db", "kind": "database", "name": "DB", "configuration": {"extra": extra}}])
        )
    )
    kept = to_dict(ir)["nodes"][0]["configuration"]["extra"]
    assert kept == {"iops": 3000, "ratio": "0.125", "flags": {"a": [1, "2.5"]}, "note": "keep me"}


def test_unknown_fields_are_refused_wherever_they_appear() -> None:
    node = {"id": "api", "kind": "service", "name": "API", "colour": "blue", "position": {"x": 1, "y": 2}}
    found = problems(
        minimal(
            owner="me",
            nodes=[
                node
                | {
                    "technology": {"name": "go", "vendor": "google"},
                    "provenance": {"source": "user_edit", "why": "?"},
                }
            ],
        )
    )
    assert found == [
        ("architecture", None, "owner", "unknown_field"),
        ("node", "api", "colour", "unknown_field"),
        ("node", "api", "position", "unknown_field"),  # layout is not part of the IR
        ("node", "api", "provenance.why", "unknown_field"),
        ("node", "api", "technology.vendor", "unknown_field"),
    ]


def test_missing_required_fields_are_reported() -> None:
    assert problems({"schema_version": 1}) == [("architecture", None, "name", "required")]
    found = problems(
        minimal(nodes=[{"id": "api", "name": "API"}], connections=[{"id": "c", "source_id": "api"}])
    )
    assert found == [
        ("connection", "c", "kind", "required"),
        ("connection", "c", "target_id", "required"),
        ("node", "api", "kind", "required"),
    ]


def test_nested_problems_name_their_element_and_path() -> None:
    node = {
        "id": "db",
        "kind": "database",
        "name": "DB",
        "technology": {"name": "Postgre SQL"},
        "configuration": {"values": {"replicas": -1, "partitions": 3}},
        "requirement_refs": [{"requirement_id": "not-a-uuid"}],
        "provenance": {"source": "llm_proposal", "confidence": "1.5"},
        "field_provenance": {"configuration.replicas": {"source": "guess"}},
    }
    found = problems(minimal(nodes=[node]))
    assert found == [
        ("node", "db", "configuration.partitions", "not_applicable"),
        ("node", "db", "configuration.replicas", "out_of_range"),
        ("node", "db", "field_provenance.configuration.replicas.source", "invalid_value"),
        ("node", "db", "provenance.confidence", "out_of_range"),
        ("node", "db", "requirement_refs[0].requirement_id", "invalid_reference"),
        ("node", "db", "technology.name", "invalid_technology"),
    ]


def test_graph_rules_are_checked_once_elements_are_valid() -> None:
    bad_node = {"id": "api", "kind": "teleporter", "name": "API"}
    link = {"id": "api-db", "source_id": "api", "target_id": "db", "kind": "data_access"}
    # The connection is not blamed for the node that was refused.
    assert problems(minimal(nodes=[bad_node], connections=[link])) == [
        ("node", "api", "kind", "invalid_kind")
    ]
    good_node = bad_node | {"kind": "service"}
    assert problems(minimal(nodes=[good_node], connections=[link])) == [
        ("connection", "api-db", "target_id", "dangling_reference")
    ]


@pytest.mark.parametrize(
    ("data", "rule"),
    [
        ([], "not_an_object"),
        ("{}", "not_an_object"),
        ({"schema_version": 1, "name": "x", "nodes": {"api": {}}}, "invalid_value"),
        ({"schema_version": 1, "name": "x", "nodes": ["api"]}, "not_an_object"),
    ],
)
def test_malformed_documents(data: object, rule: str) -> None:
    assert [p[3] for p in problems(data)] == [rule]


def test_malformed_json_and_non_numbers() -> None:
    for text in ("{", '{"schema_version": 1, "name": NaN}', b"\xff"):
        with pytest.raises(InvalidArchitecture) as raised:
            from_json(text)
        assert raised.value.violations[0].rule == "malformed_json"


def test_oversized_collections_are_refused_before_reading() -> None:
    nodes = [{"id": f"n{i}", "kind": "service", "name": "n"} for i in range(MAX_NODES + 1)]
    assert problems(minimal(nodes=nodes)) == [("architecture", None, "nodes", "too_many")]


# --- schema versions -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "rule"),
    [
        (None, "required"),
        (IR_SCHEMA_VERSION + 1, "unsupported_schema_version"),
        ("1", "unsupported_schema_version"),
        (0, "unsupported_schema_version"),
        (True, "unsupported_schema_version"),
    ],
)
def test_schema_versions_are_explicit(version: object, rule: str) -> None:
    assert problems({"schema_version": version, "name": "x"}) == [
        ("architecture", None, "schema_version", rule)
    ]


def test_older_documents_are_upgraded_step_by_step_without_touching_the_input() -> None:
    def one_to_two(document: dict[str, Any]) -> dict[str, Any]:
        document["title"] = document.pop("label")
        return document

    def two_to_three(document: dict[str, Any]) -> dict[str, Any]:
        document["name"] = document.pop("title")
        return document

    old = {"schema_version": 1, "label": "Orders", "nodes": []}
    original = copy.deepcopy(old)
    current, written_in = upgrade(old, {1: one_to_two, 2: two_to_three}, current=3)
    assert (current, written_in) == ({"schema_version": 3, "name": "Orders", "nodes": []}, 1)
    assert old == original
    assert schema_version_of(current) == 3
    with pytest.raises(InvalidArchitecture, match="can no longer be read"):
        upgrade(old, {1: one_to_two}, current=3)


# --- validation ------------------------------------------------------------------------------------


def test_validate_never_raises() -> None:
    good = validate(to_dict(api_and_postgres()))
    assert (good.valid, good.ir, good.violations) == (True, api_and_postgres(), ())
    bad = validate({"schema_version": 1})
    assert (bad.valid, bad.ir) == (False, None)
    assert bad.violations[0].rule == "required"


def test_requirement_references_are_checked_against_existing_requirements() -> None:
    ir = service_cache_queue()  # refers to THROUGHPUT and LATENCY v2
    assert requirement_problems(ir, {THROUGHPUT: 1, LATENCY: 3}) == ()
    found = requirement_problems(ir, {LATENCY: 1})
    assert [(v.element, v.element_id, v.rule) for v in found] == [
        ("architecture", None, "unknown_requirement"),
        ("architecture", None, "unknown_requirement_version"),
        ("node", "api", "unknown_requirement_version"),
    ]
    assert str(uuid.UUID(int=0)) not in found[0].message


def test_absurd_nesting_is_refused_not_a_crash() -> None:
    deep: Any = 1
    for _ in range(5000):
        deep = [deep]
    found = problems(
        minimal(
            nodes=[{"id": "db", "kind": "database", "name": "DB", "configuration": {"extra": {"x": deep}}}]
        )
    )
    assert found == [("node", "db", "configuration.x" + "[0]" * 6, "too_deep")]
    with pytest.raises(InvalidArchitecture):
        from_json("[" * 100_000 + "]" * 100_000)
