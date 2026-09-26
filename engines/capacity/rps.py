"""Throughput models: how much work a component can take, from what the architecture declares."""

from core.architecture_ir.component import NodeKind
from core.domain.capacity.results import Source

from .engine import ModelInputs, ModelMeta, ModelOutput
from .estimates import declared, estimate, number, quantity, work_unit

DEPLOYED = frozenset(NodeKind) - {NodeKind.CLIENT, NodeKind.BOUNDARY}
RUNNING = frozenset(
    {
        NodeKind.SERVICE,
        NodeKind.WORKER,
        NodeKind.GATEWAY,
        NodeKind.LOAD_BALANCER,
        NodeKind.DATABASE,
        NodeKind.CACHE,
        NodeKind.QUEUE,
        NodeKind.OBSERVABILITY,
    }
)
LINEAR = (
    "Capacity is assumed to scale linearly with replicas: that is what declaring a per-replica "
    "throughput states. Coordination, shared resources and uneven load make real scaling sub-linear."
)


class DeclaredThroughput:
    """``work_rate`` limit = ``throughput_limit_per_second``, as declared (a total that does not
    change with replicas)."""

    meta = ModelMeta(
        "declared-throughput",
        1,
        "Declared throughput",
        "The total throughput the architecture declares for the component.",
        DEPLOYED,
        ("work_rate",),
        configuration=("throughput_limit_per_second",),
        limitations=("A declared figure: nothing here checks that the component achieves it.",),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        node = inputs.node
        value = declared(node, "throughput_limit_per_second")
        unit = work_unit(node, inputs.demand)
        amount = quantity(value, unit) if value is not None else None
        return ModelOutput(
            limits=(
                estimate(
                    self.meta,
                    node,
                    "work_rate",
                    amount,
                    Source.DECLARED,
                    "configuration.throughput_limit_per_second",
                    missing=("configuration.throughput_limit_per_second",),
                ),
            )
        )


class ReplicaThroughput:
    """``work_rate`` limit = ``throughput_per_replica_per_second * replicas`` (linear in replicas,
    stated)."""

    meta = ModelMeta(
        "replica-throughput",
        1,
        "Per-replica throughput",
        "Throughput per replica times the replica count.",
        RUNNING,
        ("work_rate",),
        configuration=("throughput_per_replica_per_second", "replicas"),
        limitations=(LINEAR,),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        node = inputs.node
        per_replica = declared(node, "throughput_per_replica_per_second")
        replicas = declared(node, "replicas")
        if per_replica is None or replicas is None:  # checked before running; kept explicit
            missing = ("configuration.throughput_per_replica_per_second", "configuration.replicas")
            limit = estimate(
                self.meta, node, "work_rate", None, Source.UNKNOWN, "not declared", missing=missing
            )
            return ModelOutput(limits=(limit,))
        amount = quantity(per_replica * replicas, work_unit(node, inputs.demand))
        evidence = (
            ("throughput_per_replica_per_second", number(per_replica)),
            ("replicas", number(replicas)),
        )
        return ModelOutput(
            limits=(
                estimate(
                    self.meta,
                    node,
                    "work_rate",
                    amount,
                    Source.MODEL_ESTIMATE,
                    "throughput_per_replica_per_second * replicas",
                    inputs=evidence,
                    missing=("magnitude",),
                ),
            ),
            notes=(LINEAR,),
        )


MODELS = (DeclaredThroughput(), ReplicaThroughput())
