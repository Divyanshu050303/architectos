"""Editing commands: pure, ordered, all or nothing (Architecture IR phase 3)."""

import dataclasses
import uuid
from decimal import Decimal

import pytest

from core.architecture_ir.commands import (
    MAX_COMMANDS,
    AddConnection,
    AddNode,
    ChangeReplicas,
    InvalidArchitectureCommand,
    RemoveConnections,
    RemoveNodes,
    RenameNode,
    UpdateConfiguration,
    apply_commands,
)
from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.traceability import Assumption, DecisionRef

from .builders import api_and_postgres, connection, llm, node

EDIT = Provenance(ProvenanceSource.USER_EDIT, actor="user:ada")


def refused(ir: object, *commands: object) -> dict[str, object]:
    with pytest.raises(InvalidArchitectureCommand) as raised:
        apply_commands(ir, commands)  # type: ignore[arg-type]
    details: dict[str, object] = raised.value.details
    return details


def test_commands_produce_a_new_architecture_and_leave_the_old_one_alone() -> None:
    before = api_and_postgres()
    after = apply_commands(
        before,
        [
            AddNode(node("cache", NodeKind.CACHE, name="Cache")),
            AddConnection(connection("api-cache", "api", "cache", protocol="redis")),
            RenameNode("api", "Checkout API"),
            ChangeReplicas("api", 5),
            UpdateConfiguration("db", {"backup_enabled": True, "multi_az": None}),
        ],
    )
    assert before == api_and_postgres()
    assert [n.id for n in after.nodes] == ["api", "cache", "db", "web"]
    api, db = after.node("api"), after.node("db")
    assert api is not None
    assert db is not None
    assert (api.name, api.configuration.get("replicas")) == ("Checkout API", 5)
    assert api.configuration.get("cpu_request_cores") == Decimal("0.5")  # merged, not replaced
    assert db.configuration.values == {"storage_bytes": 100 * 10**9, "backup_enabled": True}


def test_edits_are_stamped_with_their_provenance() -> None:
    after = apply_commands(
        api_and_postgres(),
        [ChangeReplicas("api", 2), RenameNode("db", "Primary DB"), AddNode(node("q", NodeKind.QUEUE))],
        provenance=EDIT,
    )
    api, db, queue = after.node("api"), after.node("db"), after.node("q")
    assert api is not None
    assert db is not None
    assert queue is not None
    assert api.field_provenance == {"configuration.replicas": EDIT}
    assert db.field_provenance == {"name": EDIT}
    assert queue.provenance == EDIT


def test_a_known_value_replaces_an_unknown_one_and_clearing_drops_its_provenance() -> None:
    discovered = Provenance(ProvenanceSource.TERRAFORM, verified=True)
    ir = api_and_postgres()
    db = ir.node("db")
    assert db is not None
    ir = dataclasses.replace(
        ir,
        nodes=tuple(
            dataclasses.replace(
                n,
                configuration=Configuration(
                    {**db.configuration.values, "replicas": 2}, unknown={"backup_enabled"}
                ),
                field_provenance={"configuration.replicas": discovered},
            )
            if n.id == "db"
            else n
            for n in ir.nodes
        ),
    )
    after = apply_commands(ir, [UpdateConfiguration("db", {"backup_enabled": True, "replicas": None})])
    edited = after.node("db")
    assert edited is not None
    assert edited.configuration.get("backup_enabled") is True
    assert edited.configuration.unknown == frozenset()
    assert edited.configuration.get("replicas") is None
    assert edited.field_provenance == {}


def test_removing_a_node_removes_its_connections_and_its_mentions() -> None:
    decision = uuid.uuid4()
    ir = dataclasses.replace(
        api_and_postgres(),
        assumptions=(Assumption("read-heavy", "Reads dominate.", llm(), ("db", "api-db", "api")),),
        decisions=(DecisionRef(decision, ("db",)),),
    )
    after = apply_commands(ir, [RemoveNodes(("db",))])
    assert after.node("db") is None
    assert [c.id for c in after.connections] == ["web-api"]
    assert after.assumptions[0].subject_ids == ("api",)
    assert after.decisions[0].subject_ids == ()


def test_a_boundary_with_contents_cannot_be_removed() -> None:
    ir = api_and_postgres()
    ir = dataclasses.replace(
        ir,
        nodes=(
            node("vpc", NodeKind.BOUNDARY),
            *(dataclasses.replace(n, parent_id="vpc") if n.id == "db" else n for n in ir.nodes),
        ),
    )
    assert refused(ir, RemoveNodes(("vpc",))) == {
        "index": 0,
        "command": "remove_nodes",
        "reason": "boundary_not_empty",
        "element_id": "vpc",
    }
    assert apply_commands(ir, [RemoveNodes(("vpc", "db"))]).node("vpc") is None  # together is fine


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (AddNode(node("db", NodeKind.CACHE)), "duplicate_id"),
        (AddNode(node("api-db", NodeKind.CACHE)), "duplicate_id"),  # ids are shared by all elements
        (AddConnection(connection("x", "api", "ghost")), "unknown_node"),
        (RemoveNodes(("ghost",)), "unknown_node"),
        (RemoveConnections(("ghost",)), "unknown_connection"),
        (RenameNode("ghost", "x"), "unknown_node"),
        (ChangeReplicas("ghost", 1), "unknown_node"),
    ],
)
def test_commands_that_do_not_fit_are_refused_by_index(command: object, reason: str) -> None:
    details = refused(api_and_postgres(), RenameNode("api", "First"), command)
    assert (details["index"], details["reason"]) == (1, reason)


def test_all_or_nothing_and_the_result_must_be_valid() -> None:
    ir = api_and_postgres()
    with pytest.raises(InvalidArchitecture) as raised:
        apply_commands(ir, [ChangeReplicas("api", -1)])
    assert raised.value.violations[0].field == "configuration.replicas"
    with pytest.raises(InvalidArchitecture):
        apply_commands(ir, [RenameNode("api", "  ")])
    with pytest.raises(InvalidArchitecture):  # the same connection twice
        apply_commands(ir, [AddConnection(connection("api-db-2", kind=ConnectionKind.DATA_ACCESS))])
    assert refused(ir) == {"index": None, "reason": "empty"}
    assert refused(ir, *([RenameNode("api", "x")] * (MAX_COMMANDS + 1)))["reason"] == "too_many"


def test_setting_what_is_already_there_changes_nothing() -> None:
    ir = api_and_postgres()
    same = apply_commands(ir, [RenameNode("api", "Orders API"), ChangeReplicas("api", 3)], provenance=EDIT)
    assert same == ir  # no provenance stamped for a non-change
