"""Databases and caches: instance hours, and a database's provisioned storage per GB-month (its own
SKU). Serverless databases are billed by requests, which only a capacity analysis can supply."""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit
from core.domain.cost.results import CostCategory

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import instance_hours, on_premises, serverless, storage, storage_sku, usage
from .usage import work

REQUESTS = "capacity.requests_per_month"


class DatabaseModel:
    meta = CostModelMeta(
        id="database.instances",
        version=1,
        name="Database and cache instances",
        description="Instance hours of databases and caches, and a database's provisioned storage.",
        kinds=frozenset({NodeKind.DATABASE, NodeKind.CACHE}),
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
            "Every replica is billed at the same instance price (replicas include the primary).",
            "Backups, I/O and snapshots are not priced.",
        ),
    )

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]:
        if skipped := on_premises(node):
            return (skipped,)
        if serverless(node):
            main = usage(
                node,
                context,
                "requests",
                CostCategory.DATABASE,
                PricingUnit.REQUEST,
                work(node, context, REQUESTS),
            )
        else:
            main = instance_hours(node, context, CostCategory.DATABASE)
        if node.kind is NodeKind.CACHE:  # a cache's memory is its instance
            return (main,)
        return (main, storage(node, context, storage_sku(node)))
