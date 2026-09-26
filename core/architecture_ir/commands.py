"""Semantic edits of an architecture: every change a person (or an approved proposal) makes is one
of these commands, applied as a pure function to produce the next state.

They mirror the web app's editing commands (add, remove, connect, disconnect, rename, configure,
scale). Layout ("move") is not among them: it never changes the architecture.

Rules beyond the web app's:

- removing a node removes its connections, and drops it (and them) from the assumptions and
  decisions that referred to them; a boundary that still contains nodes cannot be removed;
- configuring sets or clears (``None``) known properties; a value that was unknown becomes known;
- with a ``provenance`` (e.g. ``user_edit`` by the editor), every field a command actually changes
  is stamped with it, and new elements without provenance receive it; setting a value to what it
  already is changes nothing.

Commands are applied in order, all or nothing: the first invalid command stops everything
(``InvalidArchitectureCommand`` names it by index), and the result must be a valid IR.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from core.domain.errors import DomainError

from .configuration import Configuration, ConfigValue
from .edge import Connection
from .model import ArchitectureIR
from .node import Node
from .provenance import Provenance

MAX_COMMANDS = 200


class InvalidArchitectureCommand(DomainError):
    """``details`` = {"index": n, "command": "remove_nodes", "reason": code, "element_id": id}."""

    code = "invalid_architecture_command"
    message = "An edit cannot be applied to this architecture."


@dataclass(frozen=True, slots=True)
class AddNode:
    node: Node


@dataclass(frozen=True, slots=True)
class RemoveNodes:
    node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AddConnection:
    connection: Connection


@dataclass(frozen=True, slots=True)
class RemoveConnections:
    connection_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RenameNode:
    node_id: str
    name: str


@dataclass(frozen=True, slots=True)
class UpdateConfiguration:
    node_id: str
    values: Mapping[str, ConfigValue | None]  # None clears the property


@dataclass(frozen=True, slots=True)
class ChangeReplicas:
    node_id: str
    replicas: int


type Command = (
    AddNode
    | RemoveNodes
    | AddConnection
    | RemoveConnections
    | RenameNode
    | UpdateConfiguration
    | ChangeReplicas
)

COMMAND_NAMES: dict[type[Any], str] = {
    AddNode: "add_node",
    RemoveNodes: "remove_nodes",
    AddConnection: "add_connection",
    RemoveConnections: "remove_connections",
    RenameNode: "rename_node",
    UpdateConfiguration: "update_configuration",
    ChangeReplicas: "change_replicas",
}


class _Refused(Exception):
    def __init__(self, reason: str, message: str, element_id: str | None = None) -> None:
        super().__init__(message)
        self.reason, self.message, self.element_id = reason, message, element_id


class _Draft:
    """The architecture being edited, as mutable collections (only inside ``apply_commands``)."""

    def __init__(self, ir: ArchitectureIR) -> None:
        self.nodes = {n.id: n for n in ir.nodes}
        self.connections = {c.id: c for c in ir.connections}
        self.assumptions = list(ir.assumptions)
        self.decisions = list(ir.decisions)

    def ids(self) -> set[str]:
        return {*self.nodes, *self.connections, *(a.id for a in self.assumptions)}

    def node(self, node_id: str) -> Node:
        node = self.nodes.get(node_id)
        if node is None:
            raise _Refused("unknown_node", f"There is no node {node_id!r}.", node_id)
        return node

    def forget(self, removed: set[str]) -> None:
        """Drop removed elements from the subjects of assumptions and decisions."""
        self.assumptions = [
            replace(a, subject_ids=tuple(s for s in a.subject_ids if s not in removed))
            for a in self.assumptions
        ]
        self.decisions = [
            replace(d, subject_ids=tuple(s for s in d.subject_ids if s not in removed))
            for d in self.decisions
        ]


def _stamp(node: Node, fields: Sequence[str], provenance: Provenance | None) -> Node:
    if provenance is None or not fields:
        return node
    return replace(node, field_provenance={**node.field_provenance, **dict.fromkeys(fields, provenance)})


def _configure(node: Node, values: Mapping[str, ConfigValue | None], provenance: Provenance | None) -> Node:
    current = node.configuration
    cleared = {k for k, v in values.items() if v is None}
    merged = {k: v for k, v in {**current.values, **values}.items() if k not in cleared and v is not None}
    configuration = Configuration(merged, frozenset(current.unknown) - set(values), current.extra)
    field_provenance = {
        k: v for k, v in node.field_provenance.items() if k.removeprefix("configuration.") not in cleared
    }
    updated = replace(node, configuration=configuration, field_provenance=field_provenance)
    changed = [
        f"configuration.{k}"
        for k in values
        if k not in cleared and (configuration.get(k) != current.get(k) or current.is_unknown(k))
    ]
    return _stamp(updated, changed, provenance)  # only what really changed is attributed


def _remove_nodes(draft: _Draft, node_ids: tuple[str, ...]) -> None:
    removed = set(node_ids)
    for node_id in node_ids:
        draft.node(node_id)
    for child in draft.nodes.values():
        if child.parent_id in removed and child.id not in removed:
            raise _Refused(
                "boundary_not_empty",
                f"{child.parent_id!r} still contains {child.id!r}; move or remove it first.",
                child.parent_id,
            )
    attached = {c.id for c in draft.connections.values() if {c.source_id, c.target_id} & removed}
    for node_id in removed:
        del draft.nodes[node_id]
    for connection_id in attached:
        del draft.connections[connection_id]
    draft.forget(removed | attached)


def _apply(draft: _Draft, command: Command, provenance: Provenance | None) -> None:
    match command:
        case AddNode(node=node):
            if node.id in draft.ids():
                raise _Refused("duplicate_id", f"The id {node.id!r} is already used.", node.id)
            draft.nodes[node.id] = replace(node, provenance=node.provenance or provenance)
        case RemoveNodes(node_ids=node_ids):
            _remove_nodes(draft, node_ids)
        case AddConnection(connection=connection):
            if connection.id in draft.ids():
                raise _Refused("duplicate_id", f"The id {connection.id!r} is already used.", connection.id)
            draft.node(connection.source_id)
            draft.node(connection.target_id)
            draft.connections[connection.id] = replace(
                connection, provenance=connection.provenance or provenance
            )
        case RemoveConnections(connection_ids=connection_ids):
            unknown = [c for c in connection_ids if c not in draft.connections]
            if unknown:
                raise _Refused("unknown_connection", f"There is no connection {unknown[0]!r}.", unknown[0])
            for connection_id in connection_ids:
                del draft.connections[connection_id]
            draft.forget(set(connection_ids))
        case RenameNode(node_id=node_id, name=name):
            before = draft.node(node_id)
            renamed = replace(before, name=name)
            draft.nodes[node_id] = _stamp(
                renamed, ["name"] if renamed.name != before.name else [], provenance
            )
        case UpdateConfiguration(node_id=node_id, values=values):
            draft.nodes[node_id] = _configure(draft.node(node_id), values, provenance)
        case ChangeReplicas(node_id=node_id, replicas=replicas):
            draft.nodes[node_id] = _configure(draft.node(node_id), {"replicas": replicas}, provenance)


def apply_commands(
    ir: ArchitectureIR, commands: Sequence[Command], *, provenance: Provenance | None = None
) -> ArchitectureIR:
    """``ir`` with every command applied in order; ``ir`` itself is never modified. Raises
    ``InvalidArchitectureCommand`` for a command that does not fit, ``InvalidArchitecture`` if the
    result is not a valid architecture."""
    if not commands:
        raise InvalidArchitectureCommand("No edits were given.", details={"index": None, "reason": "empty"})
    if len(commands) > MAX_COMMANDS:
        raise InvalidArchitectureCommand(
            f"At most {MAX_COMMANDS} edits can be applied at once.",
            details={"index": None, "reason": "too_many"},
        )
    draft = _Draft(ir)
    for index, command in enumerate(commands):
        try:
            _apply(draft, command, provenance)
        except _Refused as refused:
            raise InvalidArchitectureCommand(
                refused.message,
                details={
                    "index": index,
                    "command": COMMAND_NAMES.get(type(command), "unknown"),
                    "reason": refused.reason,
                    "element_id": refused.element_id,
                },
            ) from None
    return replace(
        ir,
        nodes=tuple(draft.nodes.values()),
        connections=tuple(draft.connections.values()),
        assumptions=tuple(draft.assumptions),
        decisions=tuple(draft.decisions),
    )
