"""Storage: how fast written data grows, what a retention period holds, and when a store without
one would fill (from empty: what is already stored is not known)."""

from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.domain.capacity.results import Estimate, Source

from .engine import ModelInputs, ModelMeta, ModelOutput
from .estimates import DEMAND_INCOMPLETE, declared, estimate, number, quantity

WRITES = {"write_operation_rate", "event_rate"}
MS_PER_SECOND = 1000


class StorageGrowth:
    """``storage_growth`` = writes per second * ``workload.request_payload`` (* ``replication_factor``
    for queues); with ``retention_seconds``: ``storage`` = growth * retention, against the
    declared ``storage_bytes``; without: ``time_to_full`` = storage_bytes / growth."""

    meta = ModelMeta(
        "storage-growth",
        1,
        "Storage growth",
        "Bytes written per second, and what the retention keeps or when the store fills.",
        frozenset({NodeKind.DATABASE, NodeKind.STORAGE, NodeKind.QUEUE}),
        ("storage", "storage_growth", "time_to_full"),
        configuration=("storage_bytes",),
        workload=("request_payload",),
        limitations=(
            "Every write adds one payload: in-place updates, compression, indexes and overhead are ignored.",
            "Without a retention, fill time is counted from empty: the data already stored is not known.",
        ),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        node, meta = inputs.node, self.meta
        capacity = Decimal(declared(node, "storage_bytes") or 0)
        limit = estimate(
            meta,
            node,
            "storage",
            quantity(capacity, "B"),
            Source.DECLARED,
            "configuration.storage_bytes",
            missing=("configuration.storage_bytes",),
        )
        growth, why = self._growth(inputs)
        if growth is None:
            unknown = estimate(
                meta, node, "storage_growth", None, Source.UNKNOWN, "writes * payload", missing=why
            )
            return ModelOutput(limits=(limit,), resources=(unknown,))
        resources: list[Estimate] = [growth[0]]
        per_second = growth[1]
        retention = declared(node, "retention_seconds")
        if retention is not None:
            resources.append(
                estimate(
                    meta,
                    node,
                    "storage",
                    quantity(per_second * retention, "B"),
                    Source.MODEL_ESTIMATE,
                    "storage_growth * retention_seconds",
                    inputs=(
                        ("storage_growth_bytes_per_second", number(per_second)),
                        ("retention_seconds", number(retention)),
                    ),
                    missing=("magnitude",),
                )
            )
        elif per_second > 0:
            days = capacity / per_second / 86_400
            resources.append(
                estimate(
                    meta,
                    node,
                    "time_to_full",
                    quantity(days, "d"),
                    Source.MODEL_ESTIMATE,
                    "storage_bytes / storage_growth, from empty",
                    inputs=(
                        ("storage_bytes", number(capacity)),
                        ("storage_growth_bytes_per_second", number(per_second)),
                    ),
                    missing=("magnitude",),
                )
            )
        return ModelOutput(limits=(limit,), resources=tuple(resources))

    def _growth(self, inputs: ModelInputs) -> tuple[tuple[Estimate, Decimal] | None, tuple[str, ...]]:
        node = inputs.node
        if not inputs.demand_complete:
            return None, (DEMAND_INCOMPLETE,)
        if any(d.resource == "operation_rate" for d in inputs.demand):
            return None, ("configuration.access",)  # reads and writes are not told apart
        writes = sum((d.quantity.canonical for d in inputs.demand if d.resource in WRITES), Decimal(0))
        payload = inputs.context.workload.request_payload
        if payload is None:  # checked before running; kept explicit
            return None, ("workload.request_payload",)
        copies = declared(node, "replication_factor") if node.kind is NodeKind.QUEUE else None
        per_second = writes * payload.canonical * (copies or 1)
        evidence = [("writes_per_second", number(writes)), ("payload_bytes", number(payload.canonical))]
        if copies is not None:
            evidence.append(("replication_factor", number(copies)))
        growth = estimate(
            self.meta,
            node,
            "storage_growth",
            quantity(per_second, "B/s"),
            Source.MODEL_ESTIMATE,
            "writes per second * payload" + (" * replication_factor" if copies is not None else ""),
            inputs=evidence,
            missing=("magnitude",),
        )
        return (growth, per_second), ()


MODELS = (StorageGrowth(),)
