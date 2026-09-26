"""Read-only graph queries over an architecture, for the engines (capacity, reliability, security,
cost, observability, simulation, evolution).

Engines read the canonical IR through this index instead of building their own graph structures:
one model, indexed once, every lookup by stable id. Results are in canonical (id) order, so an
engine's output is deterministic too.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable

from .component import NodeKind
from .dependency import ConnectionKind
from .edge import Connection
from .model import ArchitectureIR
from .node import Node


class Topology:
    def __init__(self, ir: ArchitectureIR) -> None:
        self.ir = ir
        self._nodes = {n.id: n for n in ir.nodes}
        self._connections = {c.id: c for c in ir.connections}
        self._outgoing: dict[str, list[Connection]] = defaultdict(list)
        self._incoming: dict[str, list[Connection]] = defaultdict(list)
        self._children: dict[str, list[Node]] = defaultdict(list)
        for connection in ir.connections:  # already in id order
            self._outgoing[connection.source_id].append(connection)
            self._incoming[connection.target_id].append(connection)
        for node in ir.nodes:
            if node.parent_id is not None:
                self._children[node.parent_id].append(node)

    # --- elements --------------------------------------------------------------------------------

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def connection(self, connection_id: str) -> Connection | None:
        return self._connections.get(connection_id)

    def nodes_of_kind(self, *kinds: NodeKind) -> tuple[Node, ...]:
        return tuple(n for n in self.ir.nodes if n.kind in kinds)

    def components(self) -> tuple[Node, ...]:
        """Every node that is a component (not a boundary)."""
        return tuple(n for n in self.ir.nodes if n.kind is not NodeKind.BOUNDARY)

    # --- connections -----------------------------------------------------------------------------

    def outgoing(self, node_id: str, *kinds: ConnectionKind) -> tuple[Connection, ...]:
        return _of_kind(self._outgoing.get(node_id, ()), kinds)

    def incoming(self, node_id: str, *kinds: ConnectionKind) -> tuple[Connection, ...]:
        return _of_kind(self._incoming.get(node_id, ()), kinds)

    def successors(self, node_id: str, *kinds: ConnectionKind) -> tuple[Node, ...]:
        """Nodes this node initiates connections to (it depends on them)."""
        return self._distinct(c.target_id for c in self.outgoing(node_id, *kinds))

    def predecessors(self, node_id: str, *kinds: ConnectionKind) -> tuple[Node, ...]:
        """Nodes that initiate connections to this node (they depend on it)."""
        return self._distinct(c.source_id for c in self.incoming(node_id, *kinds))

    def reachable_from(self, node_id: str, *kinds: ConnectionKind) -> tuple[Node, ...]:
        """Everything this node depends on, directly or not (itself excluded; cycles are fine)."""
        return self._closure(node_id, lambda n: self.successors(n, *kinds))

    def dependents_of(self, node_id: str, *kinds: ConnectionKind) -> tuple[Node, ...]:
        """Everything that depends on this node, directly or not: what its failure can affect."""
        return self._closure(node_id, lambda n: self.predecessors(n, *kinds))

    # --- containment -----------------------------------------------------------------------------

    def children(self, boundary_id: str) -> tuple[Node, ...]:
        return tuple(self._children.get(boundary_id, ()))

    def ancestors(self, node_id: str) -> tuple[Node, ...]:
        """The boundaries containing this node, innermost first."""
        found: list[Node] = []
        current = self._nodes.get(node_id)
        while current is not None and current.parent_id is not None:
            parent = self._nodes.get(current.parent_id)
            if parent is None or parent in found:
                break
            found.append(parent)
            current = parent
        return tuple(found)

    # --- helpers ---------------------------------------------------------------------------------

    def _closure(self, start: str, step: Callable[[str], tuple[Node, ...]]) -> tuple[Node, ...]:
        seen: set[str] = {start}
        frontier = [start]
        while frontier:
            for nxt in step(frontier.pop()):
                if nxt.id not in seen:
                    seen.add(nxt.id)
                    frontier.append(nxt.id)
        seen.discard(start)
        return tuple(n for n in self.ir.nodes if n.id in seen)

    def _distinct(self, ids: Iterable[str]) -> tuple[Node, ...]:
        wanted = set(ids)
        return tuple(n for n in self.ir.nodes if n.id in wanted)


def _of_kind(connections: Iterable[Connection], kinds: tuple[ConnectionKind, ...]) -> tuple[Connection, ...]:
    return tuple(c for c in connections if not kinds or c.kind in kinds)
