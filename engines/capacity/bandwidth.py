"""Network: bytes per second in and out, from the workload's payload sizes, against a declared
bandwidth."""

from decimal import Decimal

from core.domain.capacity.results import Source

from .engine import ModelInputs, ModelMeta, ModelOutput
from .estimates import DEMAND_INCOMPLETE, declared, estimate, number, quantity, work
from .rps import DEPLOYED


class NetworkBandwidth:
    """``bandwidth`` = work per second * ``request_payload`` + requests per second *
    ``response_payload``; limit = ``network_bandwidth_bytes_per_second``."""

    meta = ModelMeta(
        "network-bandwidth",
        1,
        "Network bandwidth",
        "Bytes per second the work moves in and out, against a declared bandwidth.",
        DEPLOYED,
        ("bandwidth",),
        workload=("request_payload",),
        limitations=(
            "Every unit of work carries the workload's payload sizes; protocol overhead is not modeled.",
        ),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        node, meta, load = inputs.node, self.meta, inputs.context.workload
        declared_limit = declared(node, "network_bandwidth_bytes_per_second")
        limit = estimate(
            meta,
            node,
            "bandwidth",
            quantity(declared_limit, "B/s") if declared_limit is not None else None,
            Source.DECLARED,
            "configuration.network_bandwidth_bytes_per_second",
            missing=("configuration.network_bandwidth_bytes_per_second",),
        )
        requests = sum(
            (d.quantity.canonical for d in inputs.demand if d.resource == "request_rate"), Decimal(0)
        )
        missing: list[str] = [] if inputs.demand_complete else [DEMAND_INCOMPLETE]
        if requests and load.response_payload is None:
            missing.append("workload.response_payload")
        request_size = load.request_payload.canonical if load.request_payload else Decimal(0)
        response_size = load.response_payload.canonical if load.response_payload else Decimal(0)
        ingress, egress = work(inputs.demand) * request_size, requests * response_size
        used = estimate(
            meta,
            node,
            "bandwidth",
            None if missing else quantity(ingress + egress, "B/s"),
            Source.MODEL_ESTIMATE,
            "work * request_payload + requests * response_payload",
            inputs=(
                ("ingress_bytes_per_second", number(ingress)),
                ("egress_bytes_per_second", number(egress)),
            ),
            missing=missing or ("magnitude",),
        )
        return ModelOutput(limits=(limit,), resources=(used,))


MODELS = (NetworkBandwidth(),)
