"""Shared helpers for capacity models: building estimates the same way, reading a node's work, and
the unit a node's work is counted in."""

from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.capacity.errors import InvalidQuantity
from core.domain.capacity.results import Demand, Estimate, Evidence, Source
from core.domain.capacity.units import Dimension, Quantity
from core.domain.requirements.value_objects import decimal_to_str

if TYPE_CHECKING:  # the engine imports the headroom step, which uses these helpers
    from .engine import ModelMeta

_UNIT_BY_DIMENSION = {
    Dimension.REQUEST_RATE: "requests/second",
    Dimension.OPERATION_RATE: "operations/second",
    Dimension.EVENT_RATE: "events/second",
}
_UNIT_BY_KIND = {
    NodeKind.DATABASE: "operations/second",
    NodeKind.CACHE: "operations/second",
    NodeKind.STORAGE: "operations/second",
    NodeKind.QUEUE: "events/second",
    NodeKind.WORKER: "events/second",
}
# Demand only means something when it is complete: a lower bound is not a total.
DEMAND_INCOMPLETE = "demand"


def work(demand: Iterable[Demand]) -> Decimal:
    """Units of work per second arriving at a node: one per request, event or operation."""
    return sum((d.quantity.canonical for d in demand), Decimal(0))


def work_unit(node: Node, demand: Iterable[Demand]) -> str:
    """The unit a node's work is counted in: the one its demand arrives in when there is only one,
    else the unit natural to its kind (requests/second for services, gateways, balancers, …)."""
    dimensions = {d.quantity.dimension for d in demand}
    if len(dimensions) == 1:
        return _UNIT_BY_DIMENSION[dimensions.pop()]
    return _UNIT_BY_KIND.get(node.kind, "requests/second")


def number(value: Decimal) -> str:
    return decimal_to_str(Quantity.rounded(value, "ratio").value)


def quantity(value: Decimal, unit: str) -> Quantity | None:
    """``value`` as a quantity, or None when it is beyond what a quantity holds (10^15)."""
    try:
        return Quantity.rounded(value, unit)
    except InvalidQuantity:
        return None


def estimate(
    meta: ModelMeta,
    node: Node,
    resource: str,
    value: Quantity | None,
    source: Source,
    basis: str,
    *,
    inputs: Iterable[tuple[str, str]] = (),
    missing: Iterable[str] = (),
) -> Estimate:
    """A known estimate, or an unknown one naming what it lacks. Unknown is never zero."""
    return Estimate(
        node.id,
        resource,
        value if source is not Source.UNKNOWN else None,
        source if value is not None else Source.UNKNOWN,
        basis,
        meta.id,
        meta.version,
        tuple(Evidence(label, text) for label, text in inputs),
        tuple(missing) if value is None or source is Source.UNKNOWN else (),
    )


def declared(node: Node, key: str) -> Decimal | None:
    value = node.configuration.get(key)
    return Decimal(value) if isinstance(value, int | Decimal) and not isinstance(value, bool) else None
