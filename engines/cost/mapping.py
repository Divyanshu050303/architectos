"""How a component maps to prices, read from the architecture itself and never guessed.

- **Provider**: the project's cloud provider (the framework reads it from the context).
- **Service**: ``pricing_service``. Never inferred from the component's kind or technology.
- **SKU** of the main resource: ``pricing_sku`` (an *explicit mapping*), else ``instance_class``
  compared exactly with the snapshot's SKUs (an *instance-class match*), else unmapped. Storage
  has its own ``pricing_storage_sku``.
- **Region**: the component's ``region``, else that of the innermost boundary around it that
  declares one (the architecture's own placement), else unknown.
- **Conditions**: ``pricing_conditions`` (e.g. ``on_demand``), required on the price.
- **Instances**: ``replicas``. Autoscaling bounds are not averaged and nothing is defaulted.

Every charge carries its mapping as evidence (``mapping``, ``instances``, ``operating_hours``), and
the framework adds where the SKU and region were read, so each line can be traced to the
configuration behind it. A component that runs on premises is not priced (``NotPriced``).
"""

from decimal import Decimal

from core.architecture_ir.node import Node
from core.domain.cost.money import arithmetic
from core.domain.cost.pricing import SKU, PricingUnit
from core.domain.cost.results import CostCategory, CostKind
from core.domain.engine_results import Evidence
from core.domain.requirements.value_objects import decimal_to_str

from .calculator import Charge, NotPriced
from .context import CostContext

SERVICE = "pricing_service"
MAIN_SKU = "pricing_sku"
STORAGE_SKU = "pricing_storage_sku"
CONDITIONS = "pricing_conditions"
BYTES_PER_GB = Decimal(10) ** 9  # decimal gigabytes, as providers bill


def _text(node: Node, name: str) -> str | None:
    value = node.configuration.get(name)
    return value if isinstance(value, str) else None


def deployment(node: Node) -> str | None:
    return _text(node, "deployment_model")


def serverless(node: Node) -> bool:
    return deployment(node) == "serverless"


def on_premises(node: Node) -> NotPriced | None:
    if deployment(node) != "on_premises":
        return None
    return NotPriced(
        "on_premises", "The component runs on premises: it is not priced from a cloud price list."
    )


def region(node: Node, context: CostContext) -> tuple[str | None, str]:
    """The region and where it was read."""
    if (own := _text(node, "region")) is not None:
        return own, "configuration.region"
    for boundary in context.topology.ancestors(node.id):
        if (inherited := _text(boundary, "region")) is not None:
            return inherited, f"boundary.{boundary.id}.configuration.region"
    return None, "configuration.region"


def main_sku(node: Node, *, instance_class: bool = True) -> tuple[str | None, str, Evidence]:
    """The main resource's SKU, where it was read, and how it was mapped."""
    if (explicit := _text(node, MAIN_SKU)) is not None:
        return explicit, f"configuration.{MAIN_SKU}", Evidence("mapping", "explicit")
    configured = _text(node, "instance_class") if instance_class else None
    if configured is not None and SKU.fullmatch(configured):
        return configured, "configuration.instance_class", Evidence("mapping", "instance_class")
    return None, f"configuration.{MAIN_SKU}", Evidence("mapping", "unmapped")


def storage_sku(node: Node) -> tuple[str | None, str, Evidence]:
    explicit = _text(node, STORAGE_SKU)
    return (
        explicit,
        f"configuration.{STORAGE_SKU}",
        Evidence("mapping", "explicit" if explicit else "unmapped"),
    )


def _count(node: Node, name: str) -> int | None:
    value = node.configuration.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def charge(  # noqa: PLR0913 -- one charge, stated in full
    node: Node,
    context: CostContext,
    resource: str,
    category: CostCategory,
    kind: CostKind,
    unit: PricingUnit,
    quantity: Decimal | None,
    sku: tuple[str | None, str, Evidence],
    *,
    missing: tuple[str, ...] = (),
    evidence: tuple[Evidence, ...] = (),
) -> Charge:
    where, region_from = region(node, context)
    conditions = node.configuration.get(CONDITIONS)
    return Charge(
        resource,
        category,
        kind,
        unit,
        quantity,
        _text(node, SERVICE),
        sku[0],
        where,
        frozenset(conditions) if isinstance(conditions, tuple) else frozenset(),
        missing=missing,
        assumptions=(sku[2], *evidence),
        sku_from=sku[1],
        region_from=region_from,
    )


def _hours(context: CostContext) -> Evidence:
    return Evidence("operating_hours", decimal_to_str(context.request.operating_hours_per_month))


def instance_hours(node: Node, context: CostContext, category: CostCategory) -> Charge:
    """Instances x operating hours, at the instance's hourly price."""
    replicas = _count(node, "replicas")
    if replicas is None:
        return charge(
            node, context, "instances", category, CostKind.FIXED, PricingUnit.INSTANCE_HOUR, None,
            main_sku(node), missing=("configuration.replicas",),
        )  # fmt: skip
    with arithmetic():
        hours = replicas * context.request.operating_hours_per_month
    return charge(
        node, context, "instances", category, CostKind.FIXED, PricingUnit.INSTANCE_HOUR, hours,
        main_sku(node),
        evidence=(Evidence("instances", f"{replicas} (configuration.replicas)"), _hours(context)),
    )  # fmt: skip


def hourly(node: Node, context: CostContext, category: CostCategory) -> Charge:
    """A fixed hourly charge (e.g. a load balancer) for every operating hour: one per component,
    or ``replicas`` of them when declared."""
    replicas = _count(node, "replicas")
    count, source = (replicas, "configuration.replicas") if replicas is not None else (1, "one per component")
    with arithmetic():
        hours = count * context.request.operating_hours_per_month
    return charge(
        node, context, "hours", category, CostKind.FIXED, PricingUnit.HOUR, hours, main_sku(node),
        evidence=(Evidence("instances", f"{count} ({source})"), _hours(context)),
    )  # fmt: skip


def monthly(node: Node, context: CostContext, category: CostCategory) -> Charge:
    """A fixed charge per month (e.g. a subscription), whatever the operating hours."""
    return charge(
        node, context, "subscription", category, CostKind.FIXED, PricingUnit.MONTH, Decimal(1),
        main_sku(node, instance_class=False),
    )  # fmt: skip


def storage(node: Node, context: CostContext, sku: tuple[str | None, str, Evidence]) -> Charge:
    """Provisioned storage (``storage_bytes``) per GB-month."""
    size = _count(node, "storage_bytes")
    if size is None:
        return charge(
            node, context, "storage", CostCategory.STORAGE, CostKind.FIXED, PricingUnit.GB_MONTH, None, sku,
            missing=("configuration.storage_bytes",),
        )  # fmt: skip
    with arithmetic():
        gb = size / BYTES_PER_GB
    return charge(
        node, context, "storage", CostCategory.STORAGE, CostKind.FIXED, PricingUnit.GB_MONTH, gb, sku,
        evidence=(Evidence("storage", f"{size} bytes (configuration.storage_bytes)"),),
    )  # fmt: skip


def declares_storage(node: Node) -> bool:
    return any(node.configuration.get(name) is not None for name in ("storage_bytes", STORAGE_SKU))


def usage(
    node: Node, context: CostContext, resource: str, category: CostCategory, unit: PricingUnit, needs: str
) -> Charge:
    """A usage-priced resource: its quantity comes from a capacity analysis (``needs``); until one
    provides it, the line is unknown and says so."""
    return charge(
        node, context, resource, category, CostKind.USAGE, unit, None, main_sku(node, instance_class=False),
        missing=(needs,),
    )  # fmt: skip
