"""Deterministic, identity-based comparison (Architecture IR phase 3)."""

import dataclasses
import uuid
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.diff import ChangeKind, diff
from core.architecture_ir.errors import ElementType
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.traceability import Assumption, DecisionRef, RequirementRef

from .builders import LATENCY, api_and_postgres, connection, llm, node, service_cache_queue


def replaced(ir: ArchitectureIR, node_id: str, **changes: object) -> ArchitectureIR:
    return dataclasses.replace(
        ir,
        nodes=tuple(dataclasses.replace(n, **changes) if n.id == node_id else n for n in ir.nodes),  # type: ignore[arg-type]
    )


def test_no_changes() -> None:
    changes = diff(api_and_postgres(), api_and_postgres())
    assert changes.is_empty
    assert changes.summary() == "No changes."
    assert diff(service_cache_queue(), service_cache_queue()).to_dict()["nodes"] == []


def test_a_rename_is_a_modification_not_a_replacement() -> None:
    before = api_and_postgres()
    after = replaced(before, "api", name="Checkout API")
    [change] = diff(before, after).nodes
    assert (change.element_id, change.change, change.label) == ("api", ChangeKind.MODIFIED, "Checkout API")
    assert [(f.field, f.before, f.after, f.category) for f in change.fields] == [
        ("name", "Orders API", "Checkout API", "description")
    ]


def test_a_new_id_is_a_removal_and_an_addition() -> None:
    before = api_and_postgres()
    after = dataclasses.replace(
        before,
        nodes=tuple(dataclasses.replace(n, id="orders-db") if n.id == "db" else n for n in before.nodes),
        connections=tuple(
            dataclasses.replace(c, target_id="orders-db") if c.id == "api-db" else c
            for c in before.connections
        ),
    )
    changes = diff(before, after)
    assert [(c.element_id, c.change) for c in changes.nodes] == [
        ("db", ChangeKind.REMOVED),
        ("orders-db", ChangeKind.ADDED),
    ]
    [link] = changes.connections
    assert [(f.field, f.before, f.after, f.category) for f in link.fields] == [
        ("target_id", "db", "orders-db", "endpoints")
    ]


def test_configuration_technology_and_resource_changes_are_categorized() -> None:
    before = service_cache_queue()
    api = before.node("api")
    assert api is not None
    after = replaced(
        before,
        "api",
        technology=Technology("fastapi", "0.141"),
        configuration=Configuration(
            {**api.configuration.values, "replicas": 6, "runtime": "python3.14"}, extra={"flag": True}
        ),
    )
    [change] = diff(before, after).nodes
    fields = {f.field: (f.before, f.after, f.category) for f in change.fields}
    assert fields == {
        "configuration.replicas": (4, 6, "resources"),
        "configuration.runtime": (None, "python3.14", "configuration"),
        "configuration.extra.flag": (None, True, "configuration"),
        "technology.version": (None, "0.141", "technology"),
    }
    assert change.categories == {"resources", "configuration", "technology"}


def test_decimal_changes_are_exact() -> None:
    before = service_cache_queue()
    api = before.node("api")
    assert api is not None
    values = {**api.configuration.values, "cpu_request_cores": Decimal("0.250")}  # same value, other form
    assert diff(before, replaced(before, "api", configuration=Configuration(values))).is_empty
    values["cpu_request_cores"] = Decimal("0.3")
    [change] = diff(before, replaced(before, "api", configuration=Configuration(values))).nodes
    assert [(f.before, f.after) for f in change.fields] == [("0.25", "0.3")]


def test_connection_changes() -> None:
    before = service_cache_queue()
    after = dataclasses.replace(
        before,
        connections=tuple(
            dataclasses.replace(c, protocol="redis-tls", configuration=Configuration({"tls": True}))
            if c.id == "api-cache"
            else c
            for c in before.connections
            if c.id != "worker-events"
        ),
    )
    changes = diff(before, after)
    assert [(c.element_id, c.change) for c in changes.connections] == [
        ("api-cache", ChangeKind.MODIFIED),
        ("worker-events", ChangeKind.REMOVED),
    ]
    fields = {f.field: f.category for f in changes.connections[0].fields}
    assert fields == {
        "protocol": "semantics",
        "configuration.tls": "configuration",
        "configuration.timeout_seconds": "configuration",
    }


def test_architecture_level_traceability_assumptions_and_decisions() -> None:
    before = api_and_postgres()
    decision = uuid.uuid4()
    after = dataclasses.replace(
        before,
        requirement_refs=(RequirementRef(LATENCY),),
        assumptions=(Assumption("peak", "Peak is 3x average.", llm(), ("api",)),),
        decisions=(DecisionRef(decision, ("db",)),),
        metadata={"team": "orders"},
    )
    changes = diff(before, after)
    assert {(f.field, f.category) for f in changes.architecture} == {
        ("requirement_refs", "traceability"),
        ("metadata.team", "metadata"),
    }
    assert [(c.element, c.change) for c in changes.assumptions] == [
        (ElementType.ASSUMPTION, ChangeKind.ADDED)
    ]
    assert [c.element_id for c in changes.decisions] == [str(decision)]


def test_provenance_changes_are_visible() -> None:
    before = api_and_postgres()
    verified = Provenance(ProvenanceSource.USER_EDIT, verified=True)
    after = replaced(before, "db", field_provenance={"configuration.multi_az": verified})
    [change] = diff(before, after).nodes
    assert [(f.field, f.category) for f in change.fields] == [
        ("field_provenance.configuration.multi_az", "provenance")
    ]


def test_the_summary_and_the_diff_are_deterministic() -> None:
    before = api_and_postgres()
    after = dataclasses.replace(
        before,
        nodes=(*before.nodes, node("cache", NodeKind.CACHE, name="Cache")),
        connections=(*before.connections, connection("api-cache", "api", "cache", protocol="redis")),
    )
    after = replaced(after, "api", name="Checkout API")
    changes = diff(before, after)
    assert changes.summary() == "1 node added (Cache); 1 node modified; 1 connection added."
    assert changes.to_dict() == diff(before, after).to_dict()
    assert changes.changed_ids() == {"cache", "api", "api-cache"}
    reverse = diff(after, before)
    assert reverse.summary() == "1 node removed (Cache); 1 node modified; 1 connection removed."


def test_unknown_values_becoming_known_is_a_change() -> None:
    db = Node("db", NodeKind.DATABASE, "DB", configuration=Configuration(unknown={"replicas"}))
    known = dataclasses.replace(db, configuration=Configuration({"replicas": 2}))
    before, after = ArchitectureIR("x", nodes=(db,)), ArchitectureIR("x", nodes=(known,))
    [change] = diff(before, after).nodes
    assert {f.field: (f.before, f.after) for f in change.fields} == {
        "configuration.replicas": (None, 2),
        "configuration.unknown": (["replicas"], None),
    }


def test_secret_looking_values_are_redacted_but_the_change_is_reported() -> None:
    db = Node(
        "db",
        NodeKind.DATABASE,
        "DB",
        configuration=Configuration(extra={"db_password": "hunter2", "iops": 1}),
    )
    rotated = dataclasses.replace(
        db,
        configuration=Configuration(extra={"db_password": "correct-horse", "iops": 2}),
        metadata={"api_token": "t2"},
    )
    [change] = diff(ArchitectureIR("x", nodes=(db,)), ArchitectureIR("x", nodes=(rotated,))).nodes
    fields = {f.field: (f.before, f.after) for f in change.fields}
    assert fields == {
        "configuration.extra.db_password": ("[redacted]", "[redacted]"),
        "configuration.extra.iops": (1, 2),
        "metadata.api_token": (None, "[redacted]"),
    }
    assert "hunter2" not in str(change.to_dict())


def test_secrets_nested_in_lists_and_objects_are_redacted() -> None:
    """Found in review: a list is compared as one value, so a secret inside it must be scrubbed."""
    before = Node("db", NodeKind.DATABASE, "DB")
    extra: dict[str, Any] = {
        "connections": [{"host": "db.example.com", "password": "supersecret123"}],
        "nested": {"deep": [{"items": [{"api_key": "k-123", "port": 5432}]}]},
    }
    after = dataclasses.replace(before, configuration=Configuration(extra=extra))
    [change] = diff(ArchitectureIR("x", nodes=(before,)), ArchitectureIR("x", nodes=(after,))).nodes
    shown = {f.field: f.after for f in change.fields}
    assert shown["configuration.extra.connections"] == [{"host": "db.example.com", "password": "[redacted]"}]
    assert shown["configuration.extra.nested.deep"] == [{"items": [{"api_key": "[redacted]", "port": 5432}]}]
    text = str(change.to_dict())
    assert "supersecret123" not in text
    assert "k-123" not in text
