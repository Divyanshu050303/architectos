"""The semantic diff (spec 24.1, 24.2): every kind of change, stable identity, secrets, grouping, bounds,
determinism — on small architectures and on a realistic platform."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind, Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.errors import ElementType
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.traceability import RequirementRef
from core.domain.architecture_diff.changes import Change, SemanticDiff, change_id
from core.domain.architecture_diff.errors import DiffTooLarge
from core.domain.architecture_diff.values import ChangeClass, Sensitivity, ValueType
from engines.architecture_diff import semantic
from engines.architecture_diff.semantic import RULE, semantic_diff
from tests.unit.architecture_ir.builders import LATENCY, connection, node

K = NodeKind
C = ChangeClass


def platform(**overrides: Any) -> ArchitectureIR:
    """A gateway, a load balancer, two services, PostgreSQL, Redis, Kafka, object storage, Prometheus."""
    nodes = (
        node(
            "lb", K.LOAD_BALANCER, name="Load balancer", configuration=Configuration({"exposure": "public"})
        ),
        node("gateway", K.GATEWAY, name="API gateway"),
        node(
            "orders",
            name="Orders service",
            technology=Technology("python"),
            configuration=Configuration({"replicas": 2}),
        ),
        node("payments", name="Payments service", configuration=Configuration({"replicas": 2})),
        node(
            "db",
            K.DATABASE,
            name="Orders DB",
            component="databases/postgresql",
            technology=Technology("postgresql", "16"),
        ),
        node("cache", K.CACHE, name="Redis", component="databases/redis"),
        node("events", K.QUEUE, name="Kafka", component="messaging/kafka"),
        node("files", K.STORAGE, name="Object storage", component="storage/aws-s3"),
        node("metrics", K.OBSERVABILITY, name="Prometheus"),
    )
    connections = (
        connection("lb-gateway", "lb", "gateway", kind=ConnectionKind.REQUEST, protocol="https"),
        connection("gateway-orders", "gateway", "orders", kind=ConnectionKind.REQUEST, protocol="https"),
        connection("orders-db", "orders", "db"),
        connection("orders-cache", "orders", "cache", protocol="redis"),
        connection("orders-events", "orders", "events", kind=ConnectionKind.PUBLISH, protocol="kafka"),
        connection("payments-events", "payments", "events", kind=ConnectionKind.CONSUME, protocol="kafka"),
        connection("orders-files", "orders", "files", protocol="https"),
    )
    fields: dict[str, Any] = {"name": "Shop", "nodes": nodes, "connections": connections}
    return ArchitectureIR(**(fields | overrides))


def changed(ir: ArchitectureIR, node_id: str, **fields: Any) -> ArchitectureIR:
    nodes = tuple(replace(n, **fields) if n.id == node_id else n for n in ir.nodes)
    return replace(ir, nodes=nodes)


def changed_connection(ir: ArchitectureIR, connection_id: str, **fields: Any) -> ArchitectureIR:
    connections = tuple(replace(c, **fields) if c.id == connection_id else c for c in ir.connections)
    return replace(ir, connections=connections)


def configured(node_id: str, values: dict[str, Any], extra: dict[str, Any] | None = None) -> ArchitectureIR:
    return changed(platform(), node_id, configuration=Configuration(values, extra=extra or {}))


def only(diff: SemanticDiff) -> Change:
    [change] = diff.changes
    return change


# --- what changed ------------------------------------------------------------------------------


def test_empty_to_populated_and_back() -> None:
    empty, full = ArchitectureIR("Shop"), platform()
    grown = semantic_diff(empty, full)
    assert {c.change for c in grown.changes} == {ChangeKind.ADDED}
    assert len([c for c in grown.changes if c.element is ElementType.NODE]) == 9
    gone = semantic_diff(full, empty)
    assert {c.change for c in gone.changes} == {ChangeKind.REMOVED}
    assert len(gone.changes) == len(grown.changes)


def test_identical_states_have_no_changes() -> None:
    diff = semantic_diff(platform(), platform())
    assert diff.identical
    assert diff.groups == ()
    assert diff.base_hash == diff.target_hash


def test_an_added_and_a_removed_component() -> None:
    base = platform()
    target = replace(base, nodes=(*base.nodes, node("search", K.SERVICE, name="Search")))
    added = only(semantic_diff(base, target))
    assert (added.change, added.element_id, added.classes) == (ChangeKind.ADDED, "search", (C.STRUCTURAL,))
    removed = only(semantic_diff(target, base))
    assert (removed.change, removed.label) == (ChangeKind.REMOVED, "Search")


def test_a_rename_is_the_same_element_modified() -> None:
    change = only(semantic_diff(platform(), changed(platform(), "orders", name="Order service")))
    assert change.change is ChangeKind.MODIFIED
    assert change.renamed
    assert change.label == "Order service"
    assert change.classes == (C.METADATA,)


def test_a_changed_id_is_a_removal_and_an_addition_never_matched_by_name() -> None:
    base = ArchitectureIR("Shop", nodes=(node("orders", name="Orders"),))
    target = ArchitectureIR("Shop", nodes=(node("orders-v2", name="Orders"),))  # same name, new id
    kinds = {c.element_id: c.change for c in semantic_diff(base, target).changes}
    assert kinds == {"orders": ChangeKind.REMOVED, "orders-v2": ChangeKind.ADDED}


def test_a_type_change_is_structural() -> None:
    change = only(semantic_diff(platform(), changed(platform(), "metrics", kind=K.SERVICE)))
    assert C.STRUCTURAL in change.classes
    [field] = change.fields
    assert (field.path, field.before, field.after) == ("kind", "observability", "service")


def test_a_scaling_change_is_field_aware() -> None:
    change = only(semantic_diff(platform(), configured("orders", {"replicas": 4})))
    [field] = change.fields
    assert (field.path, field.before, field.after) == ("configuration.replicas", 2, 4)
    assert (field.before_type, field.after_type) == (ValueType.NUMBER, ValueType.NUMBER)
    assert {C.SCALING, C.RESOURCES} <= set(field.classes)
    assert C.PERFORMANCE in field.classes  # the capacity engine reads it — which is not "faster"


def test_units_come_from_the_property_name() -> None:
    [field] = only(
        semantic_diff(platform(), configured("cache", {"memory_limit_bytes": 1_073_741_824}))
    ).fields
    assert (field.before_type, field.after_type, field.unit) == (ValueType.ABSENT, ValueType.NUMBER, "bytes")


def test_a_technology_and_component_change() -> None:
    target = changed(platform(), "db", component="databases/mysql", technology=Technology("mysql", "8.0"))
    change = only(semantic_diff(platform(), target))
    assert C.TECHNOLOGY in change.classes
    assert {f.path for f in change.fields} >= {"component", "technology.name", "technology.version"}


def test_a_nested_configuration_change() -> None:
    base = configured("orders", {"replicas": 2}, {"jvm": {"heap": "1g"}})
    target = configured("orders", {"replicas": 2}, {"jvm": {"heap": "2g"}})
    [field] = only(semantic_diff(base, target)).fields
    assert field.path == "configuration.extra.jvm.heap"
    assert field.classes == (C.UNKNOWN,)  # a setting the IR does not define: no engine reads it


def test_a_security_change() -> None:
    [field] = only(semantic_diff(platform(), configured("db", {"exposure": "public"}))).fields
    assert C.SECURITY in field.classes


@pytest.mark.parametrize("key", ["api_key", "db_password", "session_token", "private_key"])
def test_a_secret_is_reported_never_shown(key: str) -> None:
    base = configured("payments", {"replicas": 2}, {key: "old-value-1"})
    target = configured("payments", {"replicas": 2}, {key: "new-value-2"})
    change = only(semantic_diff(base, target))
    [field] = change.fields
    assert field.sensitivity is Sensitivity.SECRET
    assert (field.before_type, field.after_type) == (ValueType.REDACTED, ValueType.REDACTED)
    assert "old-value-1" not in repr(change.to_dict())
    assert "new-value-2" not in repr(change.to_dict())
    assert change.secret


def test_added_removed_and_modified_connections() -> None:
    base = platform()
    extra = connection("payments-db", "payments", "db")
    wider = replace(base, connections=(*base.connections, extra))
    added = only(semantic_diff(base, wider))
    assert (added.element, added.change) == (ElementType.CONNECTION, ChangeKind.ADDED)
    assert added.endpoints == ("payments", "db")
    assert set(added.classes) == {C.STRUCTURAL, C.TOPOLOGY}
    removed = only(semantic_diff(wider, base))
    assert removed.endpoints == ("payments", "db")
    timeout = changed_connection(
        base, "orders-db", configuration=Configuration({"timeout_seconds": Decimal("2.5")})
    )
    [field] = only(semantic_diff(base, timeout)).fields
    assert (field.path, field.after, field.unit) == ("configuration.timeout_seconds", "2.5", "seconds")
    assert field.after_type is ValueType.NUMBER


def test_a_protocol_change_is_topology_not_an_outcome() -> None:
    change = only(semantic_diff(platform(), changed_connection(platform(), "orders-files", protocol="http")))
    [field] = change.fields
    assert (field.path, field.before, field.after) == ("protocol", "https", "http")
    assert change.classes == (C.TOPOLOGY,)


def test_a_traceability_change_is_its_own_class() -> None:
    target = changed(platform(), "orders", requirement_refs=(RequirementRef(LATENCY, 1),))
    diff = semantic_diff(platform(), target)
    assert only(diff).classes == (C.REQUIREMENT,)
    assert [g.rule for g in diff.groups] == ["traceability"]


def test_metadata_only_changes() -> None:
    diff = semantic_diff(platform(), changed(platform(), "orders", description="Takes orders."))
    assert only(diff).classes == (C.METADATA,)
    assert [g.rule for g in diff.groups] == ["descriptive"]


def test_the_architectures_own_fields() -> None:
    diff = semantic_diff(platform(), platform(name="Shop v2"))
    change = only(diff)
    assert (change.element, change.element_id) == (ElementType.ARCHITECTURE, "architecture")
    assert change.id == change_id(ElementType.ARCHITECTURE, "architecture")
    assert [g.rule for g in diff.groups] == ["architecture"]


# --- grouping ----------------------------------------------------------------------------------


def caching_and_scaling() -> tuple[ArchitectureIR, ArchitectureIR]:
    base = ArchitectureIR(
        "Shop",
        nodes=(
            node("gateway", K.GATEWAY),
            node("svc", configuration=Configuration({"replicas": 2})),
            node("db", K.DATABASE),
        ),
        connections=(connection("svc-db", "svc", "db"),),
    )
    target = ArchitectureIR(
        "Shop",
        nodes=(
            node("gateway", K.GATEWAY),
            node("svc", configuration=Configuration({"replicas": 4})),
            node("db", K.DATABASE),
            node("redis", K.CACHE, name="Redis"),
            node("search", name="Search"),  # unrelated: its own group
        ),
        connections=(
            connection("svc-db", "svc", "db"),
            connection("gateway-svc", "gateway", "svc", kind=ConnectionKind.REQUEST, protocol="https"),
            connection("svc-redis", "svc", "redis", protocol="redis"),
        ),
    )
    return base, target


def test_related_changes_are_grouped_and_none_is_hidden() -> None:
    diff = semantic_diff(*caching_and_scaling())
    caching = next(g for g in diff.groups if "redis" in g.element_ids)
    members = {c.element_id for c in diff.changes if c.id in caching.change_ids}
    assert members == {"svc", "redis", "gateway-svc", "svc-redis"}  # the spec's caching-and-scaling example
    assert caching.rule == "connected_changes"
    assert caching.reason
    search = next(g for g in diff.groups if "search" in g.element_ids)
    assert len(search.change_ids) == 1
    grouped = sorted(i for g in diff.groups for i in g.change_ids)
    assert grouped == sorted(c.id for c in diff.changes)


def test_grouping_never_changes_a_fact() -> None:
    diff = semantic_diff(*caching_and_scaling())
    replicas = next(c for c in diff.changes if c.element_id == "svc")
    assert [(f.path, f.before, f.after) for f in replicas.fields] == [("configuration.replicas", 2, 4)]


# --- determinism and bounds --------------------------------------------------------------------


def test_the_same_states_always_give_the_same_diff() -> None:
    base, target = caching_and_scaling()
    first, second = semantic_diff(base, target), semantic_diff(base, target)
    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.versions == {"semantic": RULE}


def test_a_comparison_over_the_limit_is_refused_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(semantic, "MAX_CHANGES", 5)
    with pytest.raises(DiffTooLarge) as caught:
        semantic_diff(ArchitectureIR("Shop"), platform())
    assert caught.value.details["limit"] == "changes"


def test_a_value_too_long_to_show_is_refused_not_cut() -> None:
    long = configured("payments", {"replicas": 2}, {"notes": ["x" * 900] * 4})
    with pytest.raises(DiffTooLarge) as caught:
        semantic_diff(platform(), long)
    assert caught.value.details["limit"] == "value"


def test_too_many_groups_are_refused_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(semantic, "MAX_GROUPS", 0)
    with pytest.raises(DiffTooLarge) as caught:
        semantic_diff(ArchitectureIR("Shop"), platform())
    assert caught.value.details["limit"] == "groups"


def test_a_realistic_change_set() -> None:
    """The platform scaled, its database exposed and a cache connection removed."""
    base = platform()
    target = changed(base, "orders", configuration=Configuration({"replicas": 6}))
    target = changed(target, "db", configuration=Configuration({"exposure": "public"}))
    target = replace(target, connections=tuple(c for c in target.connections if c.id != "orders-cache"))
    diff = semantic_diff(base, target)
    by_id = {c.element_id: c for c in diff.changes}
    assert set(by_id) == {"orders", "db", "orders-cache"}
    assert by_id["orders-cache"].change is ChangeKind.REMOVED
    assert C.SECURITY in by_id["db"].classes
    # orders and the removed orders-cache connection share a node; the database's change shares none
    # (orders-db exists but did not change): grouping never links elements through unchanged connections
    groups = sorted(sorted(diff.change(i).element_id for i in g.change_ids) for g in diff.groups)  # type: ignore[union-attr]
    assert groups == [["db"], ["orders", "orders-cache"]]
    assert diff.counts()["removed"] == 1
