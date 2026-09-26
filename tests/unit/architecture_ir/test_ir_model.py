"""The architecture graph: nodes, connections and the structural invariants (Architecture IR phase 1)."""

import dataclasses
import uuid
from decimal import Decimal

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import MAX_NODES, ArchitectureIR
from core.architecture_ir.node import Lifecycle
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.traceability import Assumption, DecisionRef, RequirementRef
from core.architecture_ir.versioning import IR_SCHEMA_VERSION

from .builders import LATENCY, THROUGHPUT, api_and_postgres, connection, llm, node, rules, violations

K = NodeKind


def test_a_valid_architecture() -> None:
    ir = api_and_postgres()
    assert ir.schema_version == IR_SCHEMA_VERSION
    assert [n.id for n in ir.nodes] == ["api", "db", "web"]  # canonical order: by id
    assert [c.id for c in ir.connections] == ["api-db", "web-api"]
    assert ir.node("db").technology.name == "postgresql"  # type: ignore[union-attr]
    assert ir.connection("web-api").interaction is Interaction.SYNCHRONOUS  # type: ignore[union-attr]
    assert ir.nodes_of_kind(K.DATABASE) == (ir.node("db"),)
    assert ir.node("missing") is None


def test_the_order_elements_are_given_in_does_not_matter() -> None:
    ir = api_and_postgres()
    shuffled = dataclasses.replace(
        ir, nodes=tuple(reversed(ir.nodes)), connections=tuple(reversed(ir.connections))
    )
    assert shuffled == ir


def test_the_ir_is_immutable() -> None:
    ir = api_and_postgres()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ir.name = "Other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        ir.node("api").configuration.values["replicas"] = 9  # type: ignore[index, union-attr]


def test_an_empty_architecture_is_where_design_starts() -> None:
    assert ArchitectureIR("Draft").nodes == ()


def test_identity_is_the_id_not_the_name() -> None:
    ir = ArchitectureIR(
        "Two workers", nodes=(node("w1", K.WORKER, name="Worker"), node("w2", K.WORKER, name="Worker"))
    )
    assert len(ir.nodes) == 2
    worker = ir.node("w1")
    assert worker is not None
    renamed = dataclasses.replace(worker, name="Email worker")
    assert renamed.id == "w1"


def test_names_and_descriptions_are_normalized() -> None:
    api = node(name="  Orders    API ", description="  Takes orders.\r\nAnd payments.  ")
    assert (api.name, api.description) == ("Orders API", "Takes orders.\nAnd payments.")
    assert rules(lambda: node(name="   ")) == {"required"}
    assert rules(lambda: node(name="x" * 101)) == {"too_long"}


# --- graph rules -----------------------------------------------------------------------------------


def test_ids_are_unique_across_all_elements() -> None:
    found = violations(
        lambda: api_and_postgres(
            nodes=(node("api"), node("db", K.DATABASE), node("api-db", K.CACHE)),
            connections=(connection(),),
        )
    )
    assert {(v["element"], v["element_id"], v["rule"]) for v in found} == {
        ("node", "api-db", "duplicate_id"),
        ("connection", "api-db", "duplicate_id"),
    }


def test_a_connection_must_join_existing_nodes() -> None:
    found = violations(lambda: ArchitectureIR("x", nodes=(node("api"),), connections=(connection(),)))
    assert found == [
        {
            "element": "connection",
            "element_id": "api-db",
            "field": "target_id",
            "rule": "dangling_reference",
            "message": "target_id 'db' is not a node of this architecture.",
        }
    ]


def test_self_connections_are_refused() -> None:
    assert rules(lambda: connection("loop", "api", "api")) == {"self_connection"}


def test_cycles_are_allowed() -> None:
    nodes = (node("a"), node("b"))
    calls = (
        connection("a-b", "a", "b", kind=ConnectionKind.REQUEST, protocol="grpc"),
        connection("b-a", "b", "a", kind=ConnectionKind.REQUEST, protocol="grpc"),
    )
    assert len(ArchitectureIR("Mesh", nodes=nodes, connections=calls).connections) == 2


def test_the_same_connection_cannot_be_stated_twice() -> None:
    ir = api_and_postgres()
    twice = (*ir.connections, connection("api-db-2"))
    assert rules(lambda: dataclasses.replace(ir, connections=twice)) == {"duplicate_connection"}
    other_protocol = (*ir.connections, connection("api-db-admin", protocol="ssh"))
    assert len(dataclasses.replace(ir, connections=other_protocol).connections) == 3


def test_boundaries_contain_nodes_and_are_not_connected() -> None:
    region = node("eu", K.BOUNDARY, configuration=Configuration({"boundary_type": "region"}))
    vpc = node("vpc", K.BOUNDARY, parent_id="eu")
    ir = api_and_postgres()
    nested = dataclasses.replace(
        ir, nodes=(region, vpc, *(dataclasses.replace(n, parent_id="vpc") for n in ir.nodes))
    )
    assert nested.node("api").parent_id == "vpc"  # type: ignore[union-attr]
    assert rules(lambda: dataclasses.replace(ir, nodes=(*ir.nodes, region), connections=(
        *ir.connections, connection("api-eu", "api", "eu")))) == {"boundary_not_connectable"}  # fmt: skip


@pytest.mark.parametrize(
    ("parents", "rule"),
    [
        ({"api": "nowhere"}, "dangling_reference"),
        ({"api": "db"}, "invalid_parent"),  # only boundaries contain
        ({"b1": "b2", "b2": "b1"}, "containment_cycle"),
    ],
)
def test_containment_rules(parents: dict[str, str], rule: str) -> None:
    nodes = [node("api"), node("db", K.DATABASE), node("b1", K.BOUNDARY), node("b2", K.BOUNDARY)]
    nodes = [dataclasses.replace(n, parent_id=parents.get(n.id)) for n in nodes]
    assert rules(lambda: ArchitectureIR("x", nodes=tuple(nodes))) == {rule}


def test_a_dependency_does_not_communicate() -> None:
    config = connection("api-cfg", "api", "cfg", kind=ConnectionKind.DEPENDENCY, protocol=None)
    assert config.kind.communicates is False
    assert rules(lambda: connection(kind=ConnectionKind.DEPENDENCY)) == {"not_applicable"}  # has a protocol


def test_assumptions_and_decisions_refer_to_existing_elements() -> None:
    ir = api_and_postgres()
    decision = uuid.uuid4()
    annotated = dataclasses.replace(
        ir,
        assumptions=(Assumption("read-heavy", "Reads outnumber writes 10 to 1.", llm(), ("db", "api-db")),),
        decisions=(DecisionRef(decision, ("db",)),),
    )
    assert annotated.assumptions[0].subject_ids == ("api-db", "db")
    assert rules(lambda: dataclasses.replace(ir, assumptions=(Assumption("a", "x", llm(), ("ghost",)),))) == {
        "dangling_reference"
    }
    assert rules(
        lambda: dataclasses.replace(
            ir, decisions=(DecisionRef(decision, ("db",)), DecisionRef(decision, ("api",)))
        )
    ) == {"duplicate_decision"}


def test_every_problem_is_reported_at_once_in_a_stable_order() -> None:
    with pytest.raises(InvalidArchitecture) as raised:
        ArchitectureIR(
            "",
            nodes=(node("api"), node("b", K.BOUNDARY)),
            connections=(connection("c2", "api", "b"), connection("c1", "api", "ghost")),
            schema_version=99,
        )
    found = [(v.element, v.element_id, v.field, v.rule) for v in raised.value.violations]
    assert found == [
        ("architecture", None, "name", "required"),
        ("architecture", None, "schema_version", "unsupported_schema_version"),
        ("connection", "c1", "target_id", "dangling_reference"),
        ("connection", "c2", "target_id", "boundary_not_connectable"),
    ]
    assert raised.value.code == "invalid_architecture"
    assert "(and 3 more problems)" in str(raised.value)


def test_size_limits() -> None:
    nodes = tuple(node(f"n{i}") for i in range(MAX_NODES + 1))
    assert rules(lambda: ArchitectureIR("Big", nodes=nodes)) == {"too_many"}


# --- traceability and provenance -------------------------------------------------------------------


def test_requirements_are_referenced_not_copied() -> None:
    latency, throughput = RequirementRef(LATENCY, 2), RequirementRef(THROUGHPUT)
    ir = api_and_postgres(requirement_refs=(throughput, latency, throughput))
    original = ir.node("api")
    assert original is not None
    api = dataclasses.replace(original, requirement_refs=(latency,))
    assert ir.requirement_refs == (latency, throughput)
    assert api.requirement_refs == (latency,)
    assert rules(lambda: node(requirement_refs=("REQ-1",))) == {"invalid_reference"}


def test_discovered_values_keep_their_provenance_and_unknowns() -> None:
    terraform = Provenance(ProvenanceSource.TERRAFORM, "aws_db_instance.orders", verified=True)
    guessed = Provenance(ProvenanceSource.SYSTEM_DEFAULT, inferred=True)
    db = node(
        "db",
        K.DATABASE,
        configuration=Configuration(
            {"storage_bytes": 100 * 10**9, "replicas": 2}, unknown={"backup_enabled"}
        ),
        provenance=terraform,
        field_provenance={"configuration.replicas": guessed},
        lifecycle=Lifecycle.ACTIVE,
        metadata={"team": "orders"},
    )
    assert db.field_provenance["configuration.replicas"].inferred is True
    assert db.provenance.verified is True  # type: ignore[union-attr]
    assert db.configuration.is_unknown("backup_enabled")
    assert rules(lambda: node(field_provenance={"configuration.replicas": guessed})) == {"unknown_field"}
    assert rules(lambda: node(field_provenance={"colour": guessed})) == {"unknown_field"}


def test_metadata_is_text_labels() -> None:
    assert node(metadata={"team": "orders"}).metadata == {"team": "orders"}
    assert rules(lambda: node(metadata={"Team": "orders"})) == {"invalid_key"}
    assert rules(lambda: node(metadata={"team": 7})) == {"not_a_string"}


def test_connections_carry_their_semantics() -> None:
    link = Connection(
        "orders-events",
        "api",
        "bus",
        ConnectionKind.PUBLISH,
        protocol="Kafka",
        interaction=Interaction.ASYNCHRONOUS,
        critical=False,
        name="order placed",
        description="Order events for billing and delivery",
        configuration=Configuration({"retries": 5, "dead_letter": True}),
    )
    assert (link.protocol, link.endpoints) == ("kafka", ("api", "bus"))
    assert rules(lambda: connection(protocol="HTTP/2")) == {"invalid_protocol"}
    assert rules(lambda: connection(critical="yes")) == {"not_a_boolean"}
    assert Decimal(link.configuration.get("retries")) == 5  # type: ignore[arg-type]
