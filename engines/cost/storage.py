"""Object and file storage: the stored volume (``storage_bytes``) per GB-month."""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import main_sku, on_premises, storage


class StorageModel:
    meta = CostModelMeta(
        id="storage.volume",
        version=1,
        name="Stored volume",
        description="Object or file storage per GB-month of the declared volume.",
        kinds=frozenset({NodeKind.STORAGE}),
        resources=("storage",),
        units=frozenset({PricingUnit.GB_MONTH}),
        configuration=("pricing_service", "pricing_sku", "region", "storage_bytes"),
        limitations=("Requests and retrievals against the storage are not priced.",),
    )

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]:
        if skipped := on_premises(node):
            return (skipped,)
        return (storage(node, context, main_sku(node, instance_class=False)),)
