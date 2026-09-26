"""Compute: services, workers and gateways, billed by instance hours (replicas x operating hours);
serverless compute is billed by requests, which only a capacity analysis can supply."""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.cost.pricing import PricingUnit
from core.domain.cost.results import CostCategory

from .calculator import Charge, CostModelMeta, NotPriced
from .context import CostContext
from .mapping import instance_hours, on_premises, serverless, usage
from .usage import work

REQUESTS = "capacity.requests_per_month"


class ComputeModel:
    meta = CostModelMeta(
        id="compute.instances",
        version=1,
        name="Compute instances",
        description="Instance hours of services, workers and gateways at the instance's hourly price; "
        "serverless compute by requests.",
        kinds=frozenset({NodeKind.SERVICE, NodeKind.WORKER, NodeKind.GATEWAY}),
        resources=("instances", "requests"),
        units=frozenset({PricingUnit.INSTANCE_HOUR, PricingUnit.REQUEST}),
        configuration=("pricing_service", "pricing_sku or instance_class", "region", "replicas"),
        usage=(REQUESTS,),
        limitations=(
            "The replicas declared are billed; autoscaling bounds are not averaged.",
            "Serverless compute needs its request volume from a capacity analysis.",
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
                    CostCategory.COMPUTE,
                    PricingUnit.REQUEST,
                    work(node, context, REQUESTS),
                ),
            )
        return (instance_hours(node, context, CostCategory.COMPUTE),)
