"""Queues and brokers: broker instance hours, and provisioned storage when declared; serverless
queues are billed by requests, which only a capacity analysis can supply."""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit
from core.domain.cost.results import CostCategory

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import declares_storage, instance_hours, on_premises, serverless, storage, storage_sku, usage
from .usage import work

REQUESTS = "capacity.requests_per_month"


class MessagingModel:
    meta = CostModelMeta(
        id="messaging.brokers",
        version=1,
        name="Queues and brokers",
        description="Broker instance hours and their provisioned storage; serverless queues by requests.",
        kinds=frozenset({NodeKind.QUEUE}),
        resources=("instances", "storage", "requests"),
        units=frozenset({PricingUnit.INSTANCE_HOUR, PricingUnit.GB_MONTH, PricingUnit.REQUEST}),
        configuration=(
            "pricing_service",
            "pricing_sku or instance_class",
            "region",
            "replicas",
            "storage_bytes",
            "pricing_storage_sku",
        ),
        usage=(REQUESTS,),
        limitations=(
            "Storage is priced only when the queue declares it (storage_bytes or pricing_storage_sku).",
        ),
    )

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]:
        if skipped := on_premises(node):
            return (skipped,)
        if serverless(node):
            return (
                usage(
                    node,
                    context,
                    "requests",
                    CostCategory.MESSAGING,
                    PricingUnit.REQUEST,
                    work(node, context, REQUESTS),
                ),
            )
        main = instance_hours(node, context, CostCategory.MESSAGING)
        return (main, storage(node, context, storage_sku(node))) if declares_storage(node) else (main,)
