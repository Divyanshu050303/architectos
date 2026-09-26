"""Monthly usage from the capacity analysis a cost analysis cites: never recomputed, never guessed.

The capacity analysis states per-second demand at the workload's **design** (peak) rate; demand is
linear in the rate, so the sustained demand is design demand x (average rate / peak rate), and a
month's usage is that x the operating hours x 3600. A batch's design rate is its average; any other
workload must state its average rate, or usage is unknown (``workload.average_rate``). Only the
declared operating hours carry demand.

- **Work** (requests, events, operations): the demand reaching the component. Demand the analysis
  reports as incomplete (a lower bound) is not used.
- **Transfer** and **ingestion** (GB): the egress or ingress bytes of the ``network-bandwidth``
  estimate.
- **Stored volume** (GB-month): the ``storage-growth`` estimate of what the retention keeps.

Each usage keeps its provenance: the capacity analysis, its result fingerprint, and the capacity
model (and version) behind the number. Without a capacity analysis, usage is unknown and names it.
"""

from dataclasses import dataclass
from decimal import Decimal

from core.architecture_ir.node import Node
from core.domain.capacity.results import ComponentResult, Estimate
from core.domain.capacity.units import Dimension, unit_for
from core.domain.cost.capacity import CapacityBasis
from core.domain.cost.money import arithmetic
from core.domain.engine_results import Evidence
from core.domain.requirements.value_objects import decimal_to_str

from .context import CostContext

SECONDS_PER_HOUR = 3600
BYTES_PER_GB = Decimal(10) ** 9  # decimal gigabytes, as providers bill
WORK = frozenset({Dimension.REQUEST_RATE, Dimension.OPERATION_RATE, Dimension.EVENT_RATE})


@dataclass(frozen=True, slots=True)
class Usage:
    quantity: Decimal | None  # per month
    missing: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()


def _provenance(basis: CapacityBasis) -> tuple[Evidence, ...]:
    return (
        Evidence("capacity_analysis", str(basis.analysis_id)),
        Evidence("capacity_result", basis.result_fingerprint),
    )


def _sustained(
    basis: CapacityBasis, design: Decimal, per_month: Decimal
) -> tuple[Decimal | None, Evidence | None]:
    ratio, why = basis.average_ratio
    if ratio is None:
        return None, None
    with arithmetic():
        return design * ratio * per_month, Evidence("average_ratio", f"{decimal_to_str(ratio)} ({why})")


def _start(context: CostContext, node: Node, needs: str) -> tuple[CapacityBasis, ComponentResult] | Usage:
    """The capacity analysis and its result for ``node``, or the unknown usage saying what is missing."""
    basis = context.capacity
    if basis is None:
        return Usage(None, (needs,))
    component = basis.component(node.id)
    if component is None:
        return Usage(None, ("capacity.component",), _provenance(basis))
    return basis, component


def _seconds(context: CostContext) -> Decimal:
    with arithmetic():
        return context.request.operating_hours_per_month * SECONDS_PER_HOUR


def work(node: Node, context: CostContext, needs: str) -> Usage:
    """Units of work reaching the component per month."""
    found = _start(context, node, needs)
    if isinstance(found, Usage):
        return found
    basis, component = found
    rates = [d for d in component.demand if unit_for(d.quantity.unit).dimension in WORK]
    if not basis.demand_known(node.id):
        return Usage(None, ("capacity.demand",), _provenance(basis))
    if len({unit_for(d.quantity.unit).dimension for d in rates}) > 1:  # e.g. requests and events
        return Usage(None, ("capacity.mixed_work_units",), _provenance(basis))
    per_second = sum((d.quantity.canonical for d in rates), Decimal(0))
    monthly, ratio = _sustained(basis, per_second, _seconds(context))
    if monthly is None or ratio is None:
        return Usage(None, ("workload.average_rate",), _provenance(basis))
    return Usage(
        monthly,
        evidence=(
            *_provenance(basis),
            Evidence("design_work_per_second", decimal_to_str(per_second)),
            ratio,
            Evidence("operating_hours", decimal_to_str(context.request.operating_hours_per_month)),
        ),
    )


def _estimate(component: ComponentResult, resource: str) -> Estimate | None:
    return next((e for e in component.resources if e.resource == resource), None)


def _model(basis: CapacityBasis, estimate: Estimate) -> Evidence:
    version = estimate.model_version or basis.model_version(estimate.model_id or "")
    return Evidence("capacity_model", f"{estimate.model_id}@{version}")


def transfer(node: Node, context: CostContext, needs: str, direction: str) -> Usage:
    """GB per month leaving (``egress``) or entering (``ingress``) the component."""
    found = _start(context, node, needs)
    if isinstance(found, Usage):
        return found
    basis, component = found
    estimate = _estimate(component, "bandwidth")
    if estimate is None or estimate.quantity is None:
        why = tuple(f"capacity.{m}" for m in (estimate.missing if estimate else ())) or (
            "capacity.bandwidth",
        )
        return Usage(None, why, _provenance(basis))
    label = f"{direction}_bytes_per_second"
    per_second = next((Decimal(e.value) for e in estimate.inputs if e.label == label), None)
    if per_second is None:
        return Usage(None, (f"capacity.{label}",), _provenance(basis))
    with arithmetic():
        per_month = _seconds(context) / BYTES_PER_GB
    monthly, ratio = _sustained(basis, per_second, per_month)
    if monthly is None or ratio is None:
        return Usage(None, ("workload.average_rate",), _provenance(basis))
    return Usage(
        monthly,
        evidence=(
            *_provenance(basis),
            _model(basis, estimate),
            Evidence(f"design_{label}", decimal_to_str(per_second)),
            ratio,
            Evidence("operating_hours", decimal_to_str(context.request.operating_hours_per_month)),
        ),
    )


def stored(node: Node, context: CostContext, needs: str) -> Usage:
    """GB kept on average over the month: what the retention keeps at the sustained rate."""
    found = _start(context, node, needs)
    if isinstance(found, Usage):
        return found
    basis, component = found
    estimate = _estimate(component, "storage")
    if estimate is None or estimate.quantity is None:
        why = tuple(f"capacity.{m}" for m in (estimate.missing if estimate else ())) or ("capacity.storage",)
        return Usage(None, why, _provenance(basis))
    with arithmetic():
        design_gb = estimate.quantity.canonical / BYTES_PER_GB
    monthly, ratio = _sustained(basis, design_gb, Decimal(1))
    if monthly is None or ratio is None:
        return Usage(None, ("workload.average_rate",), _provenance(basis))
    return Usage(
        monthly,
        evidence=(
            *_provenance(basis),
            _model(basis, estimate),
            Evidence("design_stored_bytes", decimal_to_str(estimate.quantity.canonical)),
            ratio,
        ),
    )
