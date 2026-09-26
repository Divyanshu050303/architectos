"""Connections: how many a component's clients hold open, against the declared maximum."""

from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.domain.capacity.results import Source

from .engine import ModelInputs, ModelMeta, ModelOutput
from .estimates import declared, estimate, number, quantity


class ConnectionPool:
    """``connections`` demand = sum over the node's sources of ``pool_size * source replicas`` (a
    source's connections to the node share one pool: the largest declared ``pool_size`` counts);
    ``connections`` limit = ``max_connections``. A data-access source with no ``pool_size`` on any of
    its connections leaves the demand unknown."""

    meta = ModelMeta(
        "connection-pool",
        1,
        "Connection pools",
        "Connections the sources' pools hold open against the component's declared maximum.",
        frozenset({NodeKind.DATABASE, NodeKind.CACHE, NodeKind.GATEWAY, NodeKind.LOAD_BALANCER}),
        ("connections",),
        configuration=("max_connections",),
        limitations=(
            "Pools are assumed full (every pooled connection open): the worst case, not the average.",
        ),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        node, meta, topology = inputs.node, self.meta, inputs.context.topology
        # One pool per source: connections from the same source (reads, writes) share it, so the
        # largest pool_size declared between the two counts once.
        pools: dict[str, Decimal | None] = {}
        for connection in topology.incoming(node.id):
            pool = declared_connection(connection.configuration.get("pool_size"))
            if pool is None and connection.kind is not ConnectionKind.DATA_ACCESS:
                continue
            current = pools.get(connection.source_id)
            pools[connection.source_id] = pool if current is None else max(current, pool or current)
        total, peak = Decimal(0), Decimal(0)
        evidence: list[tuple[str, str]] = []
        missing: list[str] = []
        for source_id, pool in sorted(pools.items()):
            if pool is None:
                missing.append(f"{source_id}.pool_size")
                continue
            source = topology.node(source_id)
            replicas = declared(source, "replicas") if source is not None else None
            if replicas is None:
                missing.append(f"{source_id}.configuration.replicas")
                continue
            most = declared(source, "autoscaling_max_replicas") if source is not None else None
            total += pool * replicas
            peak += pool * max(replicas, most or replicas)
            evidence.append((source_id, f"{number(pool)} x {number(replicas)} replicas"))
        limit = estimate(
            meta,
            node,
            "connections",
            quantity(Decimal(declared(node, "max_connections") or 0), "connections"),
            Source.DECLARED,
            "configuration.max_connections",
            missing=("configuration.max_connections",),
        )
        used = estimate(
            meta,
            node,
            "connections",
            None if missing else quantity(total, "connections"),
            Source.MODEL_ESTIMATE,
            "sum of pool_size * source replicas",
            inputs=evidence,
            missing=missing,
        )
        notes: tuple[str, ...] = ()
        if not missing and peak > total:
            notes = (
                f"At the sources' autoscaling maximum, the pools would hold {number(peak)} connections.",
            )
        return ModelOutput(limits=(limit,), resources=(used,), notes=notes)


def declared_connection(value: object) -> Decimal | None:
    return Decimal(value) if isinstance(value, int) and not isinstance(value, bool) else None


MODELS = (ConnectionPool(),)
