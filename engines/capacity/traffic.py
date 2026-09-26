"""Demand propagation: how the workload's demand reaches each node and connection.

Model ``traffic`` (version 1), deterministic and explicit:

- **Entries.** The workload arrives at its entry nodes: the ones the request names, else the
  architecture's clients. Each entry emits the workload's design rate (the peak rate, or a batch's
  records per second) as units of work per second: requests (request/response), events (event
  stream) or operations (batch).
- **Flows.** Traffic follows ``request``, ``data_access`` and ``publish`` connections from source to
  target, and ``consume`` connections from the broker to the consumer. ``replication`` and
  ``dependency`` connections carry no workload demand.
- **Multipliers, never assumed.** A connection carries ``work * traffic_ratio * calls_per_request``
  of its upstream node's work, then ``* (1 - cache_hit_ratio)`` when a cache answers part of it, and
  ``* read_ratio`` or ``* (1 - read_ratio)`` when it carries only reads or only writes (``access``).
  At least one of ``traffic_ratio`` and ``calls_per_request`` must be declared; the other then
  counts as 1. A connection with neither is not guessed at: it carries no computed demand, it is
  reported (``routing_unspecified``), and every node after it has an incomplete (lower-bound) demand.
- **Units of work.** A node's work is the sum of the rates arriving over its inbound flows, all
  in one unit (requests, operations or events). Work arriving in different units is never added
  up: the node is reported (``mixed_work_units``) and its demand, and everything after it, is
  incomplete. Demand beyond what a quantity holds (10^15 per second) is reported
  (``demand_overflow``), never raised. What a connection delivers is in the
  unit of its kind: requests (``request``), operations (``data_access``; reads and writes apart when
  ``access`` says so) or events (``publish``, ``consume``).
- **Topology.** Nodes are processed in topological order (ties by id). Nodes on or after a traffic
  cycle have no defined demand (``cyclic_traffic``). A node that sends traffic without receiving
  any and is not an entry is a source outside the workload (``unmodeled_source``): what it sends is
  unknown, and nodes after it are incomplete.

Every demand value records its path (upstream node, connection, downstream node) and the factors
applied, so a node's demand can be traced back hop by hop to the entries.
"""

import heapq
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.domain.capacity.errors import InvalidCapacityConfig, InvalidQuantity
from core.domain.capacity.results import Demand, Evidence, Unsupported
from core.domain.capacity.units import Quantity, rounded_text
from core.domain.capacity.workload import WorkloadProfile, WorkloadType

from .context import CapacityContext
from .engine import Propagation

MODEL = ("traffic", 1)
FORWARD = frozenset({ConnectionKind.REQUEST, ConnectionKind.DATA_ACCESS, ConnectionKind.PUBLISH})
_ENTRY_UNITS = {
    WorkloadType.REQUEST_RESPONSE: ("request_rate", "requests/second"),
    WorkloadType.EVENT_STREAM: ("event_rate", "events/second"),
    WorkloadType.BATCH: ("operation_rate", "operations/second"),
}


@dataclass(frozen=True, slots=True)
class Flow:
    connection: Connection
    upstream: str
    downstream: str


def flows(connections: Iterable[Connection]) -> list[Flow]:
    """The connections that carry workload demand, oriented in the direction demand travels."""
    found = []
    for c in connections:  # id order
        if c.kind in FORWARD:
            found.append(Flow(c, c.source_id, c.target_id))
        elif c.kind is ConnectionKind.CONSUME:  # the consumer pulls from the broker
            found.append(Flow(c, c.target_id, c.source_id))
    return found


def _resource(connection: Connection) -> tuple[str, str]:
    match connection.kind:
        case ConnectionKind.REQUEST:
            return "request_rate", "requests/second"
        case ConnectionKind.DATA_ACCESS:
            access = connection.configuration.get("access")
            name = {"read": "read_operation_rate", "write": "write_operation_rate"}.get(
                str(access), "operation_rate"
            )
            return name, "operations/second"
        case _:  # publish, consume
            return "event_rate", "events/second"


def _number(value: Decimal) -> str:
    return rounded_text(value)


def multiplier(
    connection: Connection, workload: WorkloadProfile
) -> tuple[Decimal, list[Evidence], list[str]]:
    """The factor this connection applies to its upstream work, the evidence for it, and what is
    missing to know it (when something is, the factor is meaningless)."""
    config = connection.configuration
    ratio, calls = config.get("traffic_ratio"), config.get("calls_per_request")
    if ratio is None and calls is None:
        return Decimal(0), [], ["configuration.traffic_ratio"]
    factor = Decimal(1)
    evidence: list[Evidence] = []
    for name, value in (("traffic_ratio", ratio), ("calls_per_request", calls)):
        if value is not None:
            factor *= Decimal(value)  # type: ignore[arg-type]
            evidence.append(Evidence(name, _number(Decimal(value))))  # type: ignore[arg-type]
    hit = config.get("cache_hit_ratio")
    if hit is not None:
        factor *= 1 - Decimal(hit)  # type: ignore[arg-type]
        evidence.append(Evidence("cache_hit_ratio", _number(Decimal(hit))))  # type: ignore[arg-type]
    access = config.get("access")
    if access in ("read", "write"):
        if workload.read_ratio is None:
            return Decimal(0), [], ["workload.read_ratio"]
        share = workload.read_ratio if access == "read" else 1 - workload.read_ratio
        factor *= share
        evidence.append(Evidence("read_ratio" if access == "read" else "write_ratio", _number(share)))
    return factor, evidence, []


def _entries(context: CapacityContext) -> list[str]:
    ir = context.ir
    requested = context.request.entries
    if requested is None:
        return [n.id for n in ir.nodes if n.kind is NodeKind.CLIENT]
    known = {n.id for n in ir.nodes}
    unknown = sorted(set(requested) - known)
    if unknown:
        raise InvalidCapacityConfig(details={"reason": "unknown_entry", "node_id": unknown[0]})
    return list(requested)


def propagate(context: CapacityContext) -> Propagation:  # noqa: PLR0912, PLR0915 -- one documented algorithm
    ir, workload = context.ir, context.workload
    entries = _entries(context)
    if not entries:
        return Propagation(
            unsupported=(
                Unsupported(
                    "workload",
                    "no_entry",
                    "The workload enters through clients (or the entries the request names); the "
                    "architecture has none, so no demand could be propagated.",
                ),
            )
        )
    resource, unit = _ENTRY_UNITS[workload.type]
    total = workload.design_rate
    all_flows = flows(ir.connections)
    outgoing: dict[str, list[Flow]] = defaultdict(list)
    indegree: dict[str, int] = {n.id: 0 for n in ir.nodes}
    for flow in all_flows:
        outgoing[flow.upstream].append(flow)
        indegree[flow.downstream] += 1
    receiving = {flow.downstream for flow in all_flows}

    unsupported: list[Unsupported] = []
    entry_set = set(entries)
    shares = [
        Decimal(f.connection.configuration.get("traffic_ratio"))  # type: ignore[arg-type]
        for e in entries
        for f in outgoing[e]
        if f.connection.configuration.get("traffic_ratio") is not None
    ]
    if sum(shares, Decimal(0)) > 1:
        unsupported.append(
            Unsupported(
                "workload",
                "entry_shares_exceed_total",
                "The traffic ratios leaving the entries add up to more than the whole workload.",
            )
        )

    work: dict[str, Decimal] = defaultdict(Decimal)
    incomplete: set[str] = set()
    node_demand: dict[str, list[Demand]] = defaultdict(list)
    connection_demand: list[Demand] = []
    heap = [node_id for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(heap)
    while heap:
        node_id = heapq.heappop(heap)
        if node_id in entry_set:
            base: Decimal | None = total + work[node_id]
        elif node_id not in receiving and outgoing[node_id]:
            base = None  # sends traffic the workload does not describe
            unsupported.append(
                Unsupported(
                    node_id,
                    "unmodeled_source",
                    f"{node_id} sends traffic without receiving any from the workload; how much is unknown.",
                )
            )
        else:
            base = work[node_id]
        if len({d.quantity.dimension for d in node_demand.get(node_id, ())}) > 1:
            # Requests, operations and events arriving together are not one quantity: they are
            # never summed, so what this node sends on is unknown.
            base = None
            incomplete.add(node_id)
            unsupported.append(
                Unsupported(
                    node_id,
                    "mixed_work_units",
                    f"{node_id} receives work in different units (requests, operations or events); "
                    "they are not added up, so its demand and what it sends on are unknown.",
                )
            )
        for flow in outgoing[node_id]:
            downstream = flow.downstream
            if base is None:
                incomplete.add(downstream)
            else:
                factor, evidence, missing = multiplier(flow.connection, workload)
                if missing:
                    incomplete.add(downstream)
                    unsupported.append(
                        Unsupported(
                            flow.connection.id,
                            "routing_unspecified",
                            f"How much of {node_id}'s work uses {flow.connection.id} is not declared "
                            "(traffic_ratio or calls_per_request); it is not assumed.",
                            tuple(missing),
                        )
                    )
                else:
                    amount = base * factor
                    name, symbol = _resource(flow.connection)
                    path = (node_id, flow.connection.id, downstream)
                    factors = (Evidence("upstream_work_per_second", _number(base)), *evidence)
                    try:
                        quantity = Quantity.rounded(amount, symbol)
                    except InvalidQuantity:  # beyond 10^15 per second: not a number worth stating
                        incomplete.add(downstream)
                        unsupported.append(
                            Unsupported(
                                flow.connection.id,
                                "demand_overflow",
                                f"The demand {flow.connection.id} would carry is beyond what a quantity "
                                "holds (10^15 per second); check its multipliers.",
                            )
                        )
                    else:
                        connection_demand.append(Demand(flow.connection.id, name, quantity, path, factors))
                        node_demand[downstream].append(Demand(downstream, name, quantity, path, factors))
                        work[downstream] += amount
                if node_id in incomplete:
                    incomplete.add(downstream)
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                heapq.heappush(heap, downstream)

    for node_id in sorted(n for n, degree in indegree.items() if degree > 0):
        unsupported.append(
            Unsupported(
                node_id,
                "cyclic_traffic",
                f"{node_id} is on or after a cycle of traffic: its demand has no defined value.",
            )
        )
        node_demand.pop(node_id, None)
        incomplete.add(node_id)
    cyclic = {n for n, degree in indegree.items() if degree > 0}
    connection_demand = [d for d in connection_demand if d.path[2] not in cyclic]
    entry_demand = [
        Demand(
            e,
            resource,
            Quantity.rounded(total, unit),
            (e,),
            (Evidence("workload_design_rate", _number(total)),),
        )
        for e in entries
        if context.topology.node(e) is not None and context.topology.node(e).kind is not NodeKind.CLIENT  # type: ignore[union-attr]
    ]
    for demand in entry_demand:
        node_demand[demand.element_id].insert(0, demand)
    return Propagation(
        nodes={n: tuple(d) for n, d in sorted(node_demand.items())},
        connections=tuple(connection_demand),
        unsupported=tuple(unsupported),
        incomplete=frozenset(incomplete),
    )
