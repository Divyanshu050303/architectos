"""Request paths: what a request entering the architecture needs, read from explicit connection
semantics only.

From each entry (the request's ``entries``, else every client), the **required** dependencies are
followed transitively, breadth first in connection id order, each node once (cycles are safe; the
work is bounded by the IR's size):

- ``request`` and ``data_access`` connections whose ``interaction`` is not ``asynchronous``: the
  source waits for the target. An unstated interaction is treated as waiting (a request expects a
  response) and reported (``unmodeled_dependency``);
- ``dependency`` connections: the source needs the target without talking to it;
- unless the connection says ``critical: false``.

**Optional** connections are recorded with the path but not followed: asynchronous requests,
``publish`` and ``consume`` (a broker decouples producer and consumer), and ``critical: false``.
``replication`` connections carry copies of data, not requests: they concern data loss, not paths.

How a path's availability composes from these semantics is ``failure_propagation.py``'s.
"""

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.topology import Topology

from .context import ReliabilityContext
from .engine import OUT_OF_SCOPE

WAITING = frozenset({ConnectionKind.REQUEST, ConnectionKind.DATA_ACCESS})
SERIES = "product of the availabilities of every required component (series: each must be available)"
INDEPENDENCE = "Failures of different components are independent (the series product assumes it)."


class Role(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NOT_A_DEPENDENCY = "not_a_dependency"


def role(connection: Connection) -> Role:
    if connection.kind is ConnectionKind.REPLICATION:
        return Role.NOT_A_DEPENDENCY
    if connection.critical is False:
        return Role.OPTIONAL
    if connection.kind is ConnectionKind.DEPENDENCY:
        return Role.REQUIRED
    if connection.kind in WAITING and connection.interaction is not Interaction.ASYNCHRONOUS:
        return Role.REQUIRED
    return Role.OPTIONAL  # publish, consume, asynchronous requests


def unstated(connection: Connection) -> bool:
    """A waiting connection whose interaction the architecture does not state."""
    return role(connection) is Role.REQUIRED and connection.kind in WAITING and connection.interaction is None


@dataclass(frozen=True, slots=True)
class Closure:
    entry: str
    node_ids: tuple[str, ...]  # breadth first from the entry, the entry first
    required: tuple[str, ...]  # connection ids followed
    optional: tuple[str, ...]  # connection ids recorded, not followed

    def components(self, topology: Topology) -> tuple[str, ...]:
        """The required nodes that can fail: not the entry's client, not boundaries."""
        return tuple(
            n
            for n in self.node_ids
            if (node := topology.node(n)) is not None and node.kind not in OUT_OF_SCOPE
        )


def closure(topology: Topology, entry: str, avoid: frozenset[str] = frozenset()) -> Closure:
    """What ``entry`` requires, never entering the nodes in ``avoid``."""
    seen, order = {entry}, [entry]
    required: list[str] = []
    optional: list[str] = []
    queue = deque([entry])
    while queue:
        for connection in sorted(topology.outgoing(queue.popleft()), key=lambda c: c.id):
            match role(connection):
                case Role.REQUIRED:
                    if connection.target_id in avoid:
                        continue
                    required.append(connection.id)
                    if connection.target_id not in seen:
                        seen.add(connection.target_id)
                        order.append(connection.target_id)
                        queue.append(connection.target_id)
                case Role.OPTIONAL:
                    optional.append(connection.id)
                case Role.NOT_A_DEPENDENCY:
                    pass
    return Closure(entry, tuple(order), tuple(sorted(required)), tuple(sorted(optional)))


def entries(context: ReliabilityContext) -> tuple[str, ...]:
    requested = context.request.entries
    if requested is not None:
        return requested
    return tuple(n.id for n in context.ir.nodes if n.kind is NodeKind.CLIENT)
