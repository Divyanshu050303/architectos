"""Object and file storage per GB-month of what is stored: what the cited capacity analysis says the
retention keeps (at the sustained rate), else the declared volume (``storage_bytes``). Each line's
evidence says which."""

import dataclasses

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit
from core.domain.cost.results import CostCategory

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import main_sku, on_premises, storage, usage
from .usage import stored

STORED = "capacity.stored_gb"


class StorageModel:
    meta = CostModelMeta(
        id="storage.volume",
        version=1,
        name="Stored volume",
        description="Object or file storage per GB-month: the capacity analysis's volume, else the declared.",
        kinds=frozenset({NodeKind.STORAGE}),
        resources=("storage",),
        units=frozenset({PricingUnit.GB_MONTH}),
        configuration=("pricing_service", "pricing_sku", "region", "storage_bytes"),
        usage=(STORED,),
        limitations=(
            "Requests and retrievals against the storage are not priced.",
            "The capacity analysis's volume is what the retention keeps; data already stored is not known.",
        ),
    )

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]:
        if skipped := on_premises(node):
            return (skipped,)
        sku = main_sku(node, instance_class=False)
        if context.capacity is not None:
            measured = stored(node, context, STORED)
            if measured.quantity is not None:
                return (
                    usage(
                        node, context, "storage", CostCategory.STORAGE, PricingUnit.GB_MONTH, measured, sku
                    ),
                )
            if node.configuration.get("storage_bytes") is None:  # neither: say both
                missing = (*measured.missing, "configuration.storage_bytes")
                unknown = dataclasses.replace(measured, missing=missing)
                return (
                    usage(node, context, "storage", CostCategory.STORAGE, PricingUnit.GB_MONTH, unknown, sku),
                )
        return (storage(node, context, sku),)
