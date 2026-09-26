"""Third-party services: a fixed monthly subscription, priced only through an explicit mapping
(``pricing_service`` and ``pricing_sku``): an unmapped one is an unknown cost, never 0."""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit
from core.domain.cost.results import CostCategory

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import monthly


class ExternalModel:
    meta = CostModelMeta(
        id="external.subscription",
        version=1,
        name="Third-party subscriptions",
        description="A third-party service's fixed monthly subscription.",
        kinds=frozenset({NodeKind.EXTERNAL}),
        resources=("subscription",),
        units=frozenset({PricingUnit.MONTH}),
        configuration=("pricing_service", "pricing_sku", "region"),
        limitations=("Usage-based fees of third-party services are not priced.",),
    )

    def charges(self, node: Node, context: CostContext) -> tuple[Charge | NotPriced, ...]:
        return (monthly(node, context, CostCategory.MANAGED_SERVICE),)
