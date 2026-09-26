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

A path's availability is the **series** composition: the product of the availabilities of every
required component, each of which must be available for the request to succeed. It is known only
when every one of them is; the formula assumes their failures are independent (stated with every
estimate). Declared alternatives (redundancy groups) are composed by the paths' availability step.
"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.topology import Topology
from core.domain.capacity.results import Estimate, Source
from core.domain.capacity.units import rounded_text
from core.domain.engine_results import Evidence, Unsupported
from core.domain.numbers import arithmetic
from core.domain.reliability.results import PathResult
from core.domain.reliability.values import availability

from .context import ReliabilityContext
from .engine import ARCHITECTURE, OUT_OF_SCOPE, Progress, StepMeta, StepOutput

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


def closure(topology: Topology, entry: str) -> Closure:
    seen, order = {entry}, [entry]
    required: list[str] = []
    optional: list[str] = []
    queue = deque([entry])
    while queue:
        for connection in sorted(topology.outgoing(queue.popleft()), key=lambda c: c.id):
            match role(connection):
                case Role.REQUIRED:
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


def series(entry: str, components: tuple[str, ...], progress: Progress, meta: StepMeta) -> Estimate:
    """The product of the components' availabilities, or unknown naming who lacks one."""
    known: list[tuple[str, Decimal]] = []
    missing: list[str] = []
    for node_id in components:
        component = progress.component(node_id)
        estimate = component.estimate("availability") if component else None
        if estimate is None or estimate.quantity is None:
            missing.append(f"{node_id}.availability")
        else:
            known.append((node_id, estimate.quantity.value))
    evidence = (
        Evidence("assumption", INDEPENDENCE),
        *(Evidence(f"{n}.availability", rounded_text(v)) for n, v in known),
    )
    if missing or not components:
        return Estimate(entry, "availability", None, Source.UNKNOWN, SERIES, inputs=evidence,
                        missing=tuple(missing) or ("components",))  # fmt: skip
    product = Decimal(1)
    with arithmetic():
        for _, value in known:
            product *= value
    return Estimate(
        entry,
        "availability",
        availability(product),
        Source.MODEL_ESTIMATE,
        SERIES,
        meta.id,
        meta.version,
        evidence,
    )


class RequestPaths:
    meta = StepMeta(
        id="request-paths",
        version=1,
        name="Request paths",
        description="What each entry's requests need (required connections), and its series availability.",
        produces=("paths",),
        assumptions=(
            INDEPENDENCE,
            "A request or data access whose interaction is not stated waits for its target.",
        ),
        limitations=(
            "Asynchronous flows (publish, consume) are recorded, not composed: a broker decouples them.",
            "Declared alternatives are composed only where a redundancy group says so.",
        ),
    )

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        starts = entries(context)
        if not starts:
            message = "No entry: the architecture has no client, and the request names no entry."
            return StepOutput(unsupported=(Unsupported(ARCHITECTURE, "no_entry", message),))
        paths = []
        for entry in starts:
            found = closure(context.topology, entry)
            estimate = series(entry, found.components(context.topology), progress, self.meta)
            paths.append(PathResult(entry, found.node_ids, found.required, estimate, found.optional))
        return StepOutput(paths=tuple(paths))
