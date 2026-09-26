"""Observability: a managed or serverless service by the data it ingests (per GB, from a capacity
analysis); a self-hosted one by instance hours, with provisioned storage when declared."""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit
from core.domain.cost.results import CostCategory

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import declares_storage, deployment, instance_hours, on_premises, storage, storage_sku, usage

INGESTED = "capacity.ingested_gb_per_month"
MANAGED = frozenset({"managed_service", "serverless"})


class ObservabilityModel:
    meta = CostModelMeta(
        id="observability.platform",
        version=1,
        name="Observability",
        description="Managed observability by data ingested; self-hosted by instance hours and storage.",
        kinds=frozenset({NodeKind.OBSERVABILITY}),
        resources=("ingestion", "instances", "storage"),
        units=frozenset({PricingUnit.GB, PricingUnit.INSTANCE_HOUR, PricingUnit.GB_MONTH}),
        configuration=("pricing_service", "pricing_sku", "region", "deployment_model", "replicas"),
        usage=(INGESTED,),
        limitations=("Managed observability needs its ingested volume from a capacity analysis.",),
    )

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]:
        if skipped := on_premises(node):
            return (skipped,)
        if deployment(node) in MANAGED:
            return (usage(node, context, "ingestion", CostCategory.OBSERVABILITY, PricingUnit.GB, INGESTED),)
        main = instance_hours(node, context, CostCategory.OBSERVABILITY)
        return (main, storage(node, context, storage_sku(node))) if declares_storage(node) else (main,)
