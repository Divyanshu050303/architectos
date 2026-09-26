"""Structured configuration: typed known properties, unknown values, preserved extras."""

from decimal import Decimal
from functools import partial
from types import MappingProxyType
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import CONNECTION_PROPERTIES, NODE_PROPERTIES, Configuration

from .builders import connection, node, rules

K = NodeKind


def test_known_properties_are_typed_and_exact() -> None:
    config = Configuration(
        {
            "replicas": 3,
            "cpu_request_cores": Decimal("0.50"),
            "availability_zones": ("eu-west-1b", "eu-west-1a", "eu-west-1a"),
        }
    )
    assert config.get("cpu_request_cores") == Decimal("0.5")
    assert config.get("availability_zones") == ("eu-west-1a", "eu-west-1b")  # a set: one representation
    assert node(configuration=config).configuration == config


@pytest.mark.parametrize(
    ("values", "rule"),
    [
        ({"replicas": 2.0}, "invalid_value"),  # a whole number, never a float
        ({"replicas": True}, "invalid_value"),
        ({"replicas": -1}, "out_of_range"),
        ({"cpu_limit_cores": 0.5}, "not_a_number"),
        ({"autoscaling_target_cpu_ratio": Decimal("1.5")}, "out_of_range"),
        ({"deployment_model": "mainframe"}, "invalid_value"),
        ({"region": "EU West"}, "invalid_value"),
        ({"partitions": 12}, "not_applicable"),  # a service has no partitions
        ({"colour": "blue"}, "unknown_property"),  # unrecognized settings go in extra
        ({"autoscaling_min_replicas": 5, "autoscaling_max_replicas": 2}, "inconsistent"),
        ({"memory_request_bytes": 2, "memory_limit_bytes": 1}, "inconsistent"),
    ],
)
def test_invalid_node_configuration(values: dict[str, object], rule: str) -> None:
    assert rules(lambda: node(configuration=Configuration(values))) == {rule}  # type: ignore[arg-type]


def test_violations_name_the_node_and_the_property() -> None:
    try:
        node("orders-db", K.DATABASE, configuration=Configuration({"replicas": -1}))
    except Exception as error:
        [violation] = error.details["violations"]  # type: ignore[attr-defined]
    assert violation == {
        "element": "node",
        "element_id": "orders-db",
        "field": "configuration.replicas",
        "rule": "out_of_range",
        "message": "replicas must be at least 0.",
    }


def test_each_kind_has_its_own_properties() -> None:
    node("q", K.QUEUE, configuration=Configuration({"partitions": 12, "retention_seconds": 604_800}))
    node("c", K.CACHE, configuration=Configuration({"eviction_policy": "allkeys_lru", "persistence": "none"}))
    node("vpc", K.BOUNDARY, configuration=Configuration({"boundary_type": "network", "region": "eu-west-1"}))
    assert rules(
        lambda: node("db", K.DATABASE, configuration=Configuration({"eviction_policy": "allkeys_lru"}))
    ) == {"not_applicable"}
    for spec in NODE_PROPERTIES.values():
        assert spec.applies_to <= set(NodeKind), spec.name
        assert spec.description, spec.name


def test_an_unknown_value_is_not_an_absent_one() -> None:
    config = Configuration({"storage_bytes": 10**9}, unknown={"replicas", "multi_az"})
    db = node("db", K.DATABASE, configuration=config)
    assert db.configuration.is_unknown("replicas")
    assert db.configuration.get("replicas") is None
    assert not db.configuration.is_unknown("backup_enabled")  # simply not stated
    assert rules(lambda: Configuration({"replicas": 1}, unknown={"replicas"})) == {"contradictory"}
    assert rules(lambda: node(configuration=Configuration(unknown={"colour"}))) == {"unknown_property"}


def test_unrecognized_settings_are_preserved_unchanged_and_immutable() -> None:
    extra: dict[str, Any] = {"cacheApiResponses": True, "annotations": {"team": "orders", "ports": [80, 443]}}
    config = Configuration(extra=extra)
    kept = node(configuration=config).configuration.extra
    assert kept["cacheApiResponses"] is True
    assert kept["annotations"] == {"team": "orders", "ports": (80, 443)}
    assert isinstance(kept["annotations"], MappingProxyType)
    extra["cacheApiResponses"] = False  # the caller's copy changes, the IR does not
    assert kept["cacheApiResponses"] is True
    with pytest.raises(TypeError):
        kept["annotations"]["team"] = "x"  # type: ignore[index]


@pytest.mark.parametrize(
    ("extra", "rule"),
    [
        ({"ratio": 0.5}, "unsupported_value"),  # fractional numbers are kept as text
        ({"deep": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}}, "too_deep"),
        ({"many": list(range(101))}, "too_many"),
        ({"text": "x\x00"}, "control_characters"),
        ({"bad key!": 1}, "invalid_key"),
    ],
)
def test_preserved_values_are_bounded(extra: dict[str, object], rule: str) -> None:
    assert rule in rules(lambda: Configuration(extra=extra))  # type: ignore[arg-type]


def test_a_known_property_cannot_hide_in_extra() -> None:
    assert rules(lambda: node(configuration=Configuration(extra={"replicas": 3}))) == {"misplaced_property"}
    assert rules(lambda: Configuration({"replicas": 1}, extra={"replicas": 2})) == {"duplicate_property"}


def test_connection_configuration() -> None:
    link = connection(
        configuration=Configuration({"timeout_seconds": Decimal("2.5"), "retries": 3, "tls": True})
    )
    assert link.configuration.get("timeout_seconds") == Decimal("2.5")
    assert rules(lambda: connection(configuration=Configuration({"port": 70_000}))) == {"out_of_range"}
    assert rules(lambda: connection(configuration=Configuration({"replicas": 2}))) == {"unknown_property"}
    assert set(CONNECTION_PROPERTIES) == {
        "timeout_seconds",
        "retries",
        "tls",
        "dead_letter",
        "port",
        "traffic_ratio",
        "calls_per_request",
        "cache_hit_ratio",
        "access",
    }


def test_traffic_properties_are_bounded() -> None:
    link = connection(
        configuration=Configuration(
            {
                "traffic_ratio": Decimal("0.3"),
                "calls_per_request": 2,
                "cache_hit_ratio": Decimal("0.8"),
                "access": "read",
            }
        )
    )
    assert link.configuration.get("traffic_ratio") == Decimal("0.3")
    bad_values: list[dict[str, Any]] = [
        {"traffic_ratio": Decimal("1.5")},
        {"cache_hit_ratio": Decimal("-0.1")},
        {"calls_per_request": 1001},
    ]
    for bad in bad_values:
        assert rules(partial(connection, configuration=Configuration(bad))) == {"out_of_range"}
    assert rules(lambda: connection(configuration=Configuration({"access": "append"}))) == {"invalid_value"}
