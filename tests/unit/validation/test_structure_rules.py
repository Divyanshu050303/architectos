"""Structural and completeness rules (Milestone 6, phase 3): each fires on what it is for, stays
quiet otherwise, and the shipped registry runs deterministically."""

from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Lifecycle
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.validation.results import Category, Finding, Severity
from engines.validation.context import RevisionInfo, ValidationConfig, ValidationContext
from engines.validation.engine import Registry, validate
from engines.validation.registry import default_registry
from engines.validation.rules.consistency import strongly_connected
from tests.unit.architecture_ir.builders import (
    api_and_postgres,
    connection,
    discovered,
    node,
    service_cache_queue,
)

REVISION = RevisionInfo("arch-1", 1, "c" * 64)


def run(ir: ArchitectureIR, revision: RevisionInfo = REVISION, **config: Any) -> list[Finding]:
    context = ValidationContext(ir, revision, config=ValidationConfig(**config))
    result = validate(context, default_registry())
    assert result.failures == ()
    return list(result.findings)


def of(findings: list[Finding], rule_id: str) -> list[Finding]:
    return [f for f in findings if f.rule_id == rule_id]


def request(
    cid: str, source: str, target: str, interaction: Interaction | None = Interaction.SYNCHRONOUS
) -> Any:
    return connection(
        cid, source, target, kind=ConnectionKind.REQUEST, protocol="https", interaction=interaction
    )


def services(*ids: str, **kwargs: Any) -> tuple[Any, ...]:
    return tuple(node(i, **kwargs) for i in ids)


def test_well_formed_examples_raise_no_structural_findings() -> None:
    for ir in (api_and_postgres(), service_cache_queue()):
        assert run(ir) == []


def test_the_registry_ships_every_rule_once_in_both_profiles() -> None:
    registry = default_registry()
    ids = [r.meta.id for r in registry.rules()]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert {"default", "strict"} <= registry.profiles()
    assert registry is not default_registry()  # never a shared, mutable instance
    assert all(r.meta.category in (Category.STRUCTURE, Category.COMPLETENESS) for r in registry.rules())


# --- disconnected components ---------------------------------------------------------------------


def test_a_component_without_connections_is_reported() -> None:
    ir = api_and_postgres(nodes=(*api_and_postgres().nodes, node("orphan", NodeKind.WORKER, name="Orphan")))
    [found] = of(run(ir), "structure.disconnected-component")
    assert (found.entity_ids, found.code, found.severity) == (("orphan",), "disconnected", Severity.MEDIUM)


def test_a_lone_component_and_boundaries_are_not_disconnected() -> None:
    assert run(ArchitectureIR(name="One", nodes=(node("api"),))) == []
    assert run(ArchitectureIR(name="Empty")) == []
    ir = ArchitectureIR(
        name="Grouped",
        nodes=(node("vpc", NodeKind.BOUNDARY), node("a", parent_id="vpc"), node("b", parent_id="vpc")),
        connections=(request("a-b", "a", "b"),),
    )
    assert of(run(ir), "structure.disconnected-component") == []


def test_a_dependency_counts_as_a_connection() -> None:
    ir = ArchitectureIR(
        name="Config",
        nodes=services("api", "config"),
        connections=(
            connection("api-config", "api", "config", kind=ConnectionKind.DEPENDENCY, protocol=None),
        ),
    )
    assert run(ir) == []


# --- empty boundaries ----------------------------------------------------------------------------


def test_an_empty_boundary_is_reported() -> None:
    ir = api_and_postgres(nodes=(*api_and_postgres().nodes, node("vpc", NodeKind.BOUNDARY, name="VPC")))
    [found] = run(ir)
    assert (found.rule_id, found.entity_ids, found.severity) == (
        "structure.empty-boundary",
        ("vpc",),
        Severity.LOW,
    )


def test_a_boundary_holding_only_a_boundary_is_not_empty_but_the_inner_one_is() -> None:
    ir = ArchitectureIR(
        name="Nested",
        nodes=(node("outer", NodeKind.BOUNDARY), node("inner", NodeKind.BOUNDARY, parent_id="outer")),
    )
    assert [f.entity_ids for f in of(run(ir), "structure.empty-boundary")] == [("inner",)]


# --- synchronous cycles --------------------------------------------------------------------------


def test_a_synchronous_cycle_is_reported_once_with_its_members() -> None:
    ir = ArchitectureIR(
        name="Loop",
        nodes=services("a", "b", "c", "d"),
        connections=(
            request("a-b", "a", "b"),
            request("b-c", "b", "c"),
            request("c-a", "c", "a"),
            request("c-d", "c", "d"),
        ),
    )
    [found] = of(run(ir), "structure.synchronous-cycle")
    assert found.entity_ids == ("a", "a-b", "b", "b-c", "c", "c-a")  # d is outside the cycle
    assert found.severity is Severity.HIGH
    assert dict((e.label, e.value) for e in found.evidence) == {"components": "3", "requests": "3"}


def test_separate_cycles_are_separate_findings() -> None:
    ir = ArchitectureIR(
        name="Two loops",
        nodes=services("a", "b", "x", "y"),
        connections=(
            request("a-b", "a", "b"),
            request("b-a", "b", "a"),
            request("x-y", "x", "y"),
            request("y-x", "y", "x"),
        ),
    )
    assert [f.entity_ids[0] for f in of(run(ir), "structure.synchronous-cycle")] == ["a", "x"]


def test_an_asynchronous_link_breaks_the_cycle() -> None:
    ir = ArchitectureIR(
        name="Broken loop",
        nodes=services("a", "b"),
        connections=(request("a-b", "a", "b"), request("b-a", "b", "a", Interaction.ASYNCHRONOUS)),
    )
    assert of(run(ir), "structure.synchronous-cycle") == []


def test_messaging_and_data_access_are_not_request_cycles() -> None:
    ir = ArchitectureIR(
        name="Events",
        nodes=(node("a"), node("q", NodeKind.QUEUE)),
        connections=(
            connection("a-q", "a", "q", kind=ConnectionKind.PUBLISH, protocol="kafka"),
            connection("q-a", "q", "a", kind=ConnectionKind.CONSUME, protocol="kafka"),
        ),
    )
    assert of(run(ir), "structure.synchronous-cycle") == []


def test_unstated_interaction_counts_as_synchronous_unless_configured_otherwise() -> None:
    ir = ArchitectureIR(
        name="Unstated",
        nodes=services("a", "b"),
        connections=(request("a-b", "a", "b", None), request("b-a", "b", "a", None)),
    )
    assert len(of(run(ir), "structure.synchronous-cycle")) == 1
    relaxed = run(ir, parameters={"structure.synchronous-cycle": {"include_unstated": False}})
    assert of(relaxed, "structure.synchronous-cycle") == []


def test_a_large_cycle_is_capped_and_does_not_exhaust_the_stack() -> None:
    ids = [f"s{i:04d}" for i in range(600)]
    ir = ArchitectureIR(
        name="Ring",
        nodes=services(*ids),
        connections=tuple(request(f"c{i:04d}", ids[i], ids[(i + 1) % 600]) for i in range(600)),
    )
    [found] = of(run(ir), "structure.synchronous-cycle")
    assert len(found.entity_ids) == 200
    assert ("components", "600") in [(e.label, e.value) for e in found.evidence]


def test_strongly_connected_components_are_deterministic() -> None:
    edges = {"b": ["a"], "a": ["b", "c"], "c": []}
    assert (
        strongly_connected(["c", "b", "a"], edges)
        == strongly_connected(["a", "b", "c"], edges)
        == [["a", "b"], ["c"]]
    )


# --- deprecated components -----------------------------------------------------------------------


def test_depending_on_a_deprecated_component_is_reported() -> None:
    base = api_and_postgres()
    nodes = tuple(
        n if n.id != "db" else node("db", NodeKind.DATABASE, name="Orders DB", lifecycle=Lifecycle.DEPRECATED)
        for n in base.nodes
    )
    [found] = run(api_and_postgres(nodes=nodes))
    assert (found.code, found.entity_ids, found.severity) == (
        "depends_on_deprecated",
        ("api", "api-db", "db"),
        Severity.MEDIUM,
    )


def test_a_deprecated_source_is_low_and_two_deprecated_ends_are_quiet() -> None:
    old: dict[str, Any] = {"lifecycle": Lifecycle.DEPRECATED}
    one = ArchitectureIR(
        name="Old caller", nodes=(node("a", **old), node("b")), connections=(request("a-b", "a", "b"),)
    )
    [found] = run(one)
    assert (found.code, found.severity) == ("deprecated_source", Severity.LOW)
    both = ArchitectureIR(
        name="All old", nodes=(node("a", **old), node("b", **old)), connections=(request("a-b", "a", "b"),)
    )
    assert run(both) == []


# --- schema version ------------------------------------------------------------------------------


def test_a_revision_stored_in_an_older_schema_is_reported() -> None:
    assert run(api_and_postgres(), RevisionInfo("arch-1", 1, "c" * 64, IR_SCHEMA_VERSION)) == []
    [found] = run(api_and_postgres(), RevisionInfo("arch-1", 1, "c" * 64, IR_SCHEMA_VERSION - 1))
    assert (found.rule_id, found.severity, found.actual) == (
        "structure.schema-version",
        Severity.INFO,
        str(IR_SCHEMA_VERSION - 1),
    )


def test_the_schema_rule_cannot_be_deselected() -> None:
    context = ValidationContext(
        api_and_postgres(), RevisionInfo("a", 1, "c" * 64, 0), config=ValidationConfig(rules=())
    )
    assert [f.rule_id for f in validate(context, default_registry()).findings] == ["structure.schema-version"]


# --- unknown values ------------------------------------------------------------------------------


def test_unknown_values_are_reported_per_element() -> None:
    [found] = run(discovered())
    assert found.rule_id == "completeness.unknown-values"
    assert found.entity_ids == ("aws_db_instance.orders",)
    assert found.field_paths == ("configuration.backup_retention_seconds", "configuration.max_connections")
    assert found.category is Category.COMPLETENESS


def test_unknown_connection_values_are_reported_too() -> None:
    ir = api_and_postgres(
        connections=(
            connection(configuration=Configuration(unknown={"tls"})),
            *(c for c in api_and_postgres().connections if c.id != "api-db"),
        )
    )
    [found] = run(ir)
    assert (found.entity_ids, found.field_paths) == (("api-db",), ("configuration.tls",))


# --- the shipped rule set ------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["default", "strict"])
def test_the_shipped_rules_are_deterministic(profile: str) -> None:
    ir = discovered()
    first = validate(
        ValidationContext(ir, REVISION, config=ValidationConfig(profile=profile)), default_registry()
    )
    again = validate(
        ValidationContext(ir, REVISION, config=ValidationConfig(profile=profile)), default_registry()
    )
    assert first == again
    assert first.fingerprint == again.fingerprint


def test_an_empty_registry_is_not_the_shipped_one() -> None:
    assert Registry().rules() == ()
