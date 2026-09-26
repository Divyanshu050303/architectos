"""CPU: what the work costs in cores, from a declared per-unit cost, against the declared cores."""

from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.domain.capacity.results import Estimate, Source

from .engine import ModelInputs, ModelMeta, ModelOutput
from .estimates import DEMAND_INCOMPLETE, declared, estimate, number, quantity, work

COMPUTE = frozenset({NodeKind.SERVICE, NodeKind.WORKER, NodeKind.GATEWAY})


class CpuDemand:
    """``cpu`` demand = work per second * ``cpu_core_seconds_per_request`` (cores);
    ``cpu`` limit = ``replicas * cpu_limit_cores``."""

    meta = ModelMeta(
        "cpu-demand",
        1,
        "CPU demand",
        "Cores the work needs, from a declared CPU cost per unit of work, against the declared limit.",
        COMPUTE,
        ("cpu",),
        configuration=("cpu_core_seconds_per_request",),
        limitations=(
            "A constant cost per unit of work: contention, garbage collection and warm-up are not modeled.",
        ),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        node, meta = inputs.node, self.meta
        cost = declared(node, "cpu_core_seconds_per_request") or Decimal(0)
        load = work(inputs.demand)
        used = quantity(load * cost, "cores") if inputs.demand_complete else None
        demand = estimate(
            meta,
            node,
            "cpu",
            used,
            Source.MODEL_ESTIMATE,
            "work per second * cpu_core_seconds_per_request",
            inputs=(("work_per_second", number(load)), ("cpu_core_seconds_per_request", number(cost))),
            missing=(DEMAND_INCOMPLETE,),
        )
        return ModelOutput(limits=(self._limit(inputs),), resources=(demand,))

    def _limit(self, inputs: ModelInputs) -> Estimate:
        node = inputs.node
        replicas, cores = declared(node, "replicas"), declared(node, "cpu_limit_cores")
        missing = [
            f"configuration.{k}" for k, v in (("replicas", replicas), ("cpu_limit_cores", cores)) if v is None
        ]
        if replicas is None or cores is None:
            return estimate(
                self.meta, node, "cpu", None, Source.UNKNOWN, "replicas * cpu_limit_cores", missing=missing
            )
        return estimate(
            self.meta,
            node,
            "cpu",
            quantity(replicas * cores, "cores"),
            Source.MODEL_ESTIMATE,
            "replicas * cpu_limit_cores",
            inputs=(("replicas", number(replicas)), ("cpu_limit_cores", number(cores))),
            missing=("magnitude",),
        )


MODELS = (CpuDemand(),)
