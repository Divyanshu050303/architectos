"""Networking: a load balancer's hourly charge, and a CDN's data transfer (per GB), which only a
capacity analysis can supply."""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit
from core.domain.cost.results import CostCategory

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import hourly, on_premises, usage

TRANSFER = "capacity.transfer_gb_per_month"


class NetworkingModel:
    meta = CostModelMeta(
        id="networking.edge",
        version=1,
        name="Load balancers and CDNs",
        description="A load balancer's hourly charge for every operating hour; a CDN's data transfer.",
        kinds=frozenset({NodeKind.LOAD_BALANCER, NodeKind.CDN}),
        resources=("hours", "data_transfer"),
        units=frozenset({PricingUnit.HOUR, PricingUnit.GB}),
        configuration=("pricing_service", "pricing_sku", "region", "replicas (load balancers)"),
        usage=(TRANSFER,),
        limitations=(
            "A load balancer component is one load balancer unless replicas says otherwise.",
            "Traffic processed by a load balancer is not priced.",
        ),
    )

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]:
        if skipped := on_premises(node):
            return (skipped,)
        if node.kind is NodeKind.CDN:
            return (usage(node, context, "data_transfer", CostCategory.NETWORK, PricingUnit.GB, TRANSFER),)
        return (hourly(node, context, CostCategory.NETWORK),)
