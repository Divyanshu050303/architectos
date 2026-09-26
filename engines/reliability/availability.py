"""Component reliability models: only calculations whose inputs and assumptions are declared.

Registered in this order (precedence: the first to establish a value stands):

1. ``declared-availability``: the component's declared ``availability`` (e.g. a provider's
   commitment), as declared.
2. ``declared-replica-availability``: one replica's declared ``replica_availability``.
3. ``mtbf-mttr``: one replica's steady-state availability ``MTBF / (MTBF + MTTR)``, from
   ``mtbf_seconds`` and ``mttr_seconds`` (both means, over the same period, of one replica; repair
   starts at failure). Undefined when both are 0.
4. ``replicas``: the component from its replicas' availability ``a``. One replica: ``a``. With
   ``n`` replicas of which ``k`` (``min_healthy_replicas``) must be healthy, the k-of-n formula
   ``sum_{i=k..n} C(n, i) a^i (1 - a)^(n - i)`` — only when failures are declared ``independent``
   and failover ``automatic`` (declared, not verified: failover time is not deducted). Correlated
   or undeclared independence, or manual or no failover, leave it unknown and say why.
5. ``recovery-time``: how long a failure interrupts the component: its declared failover time
   when it fails over (``manual`` or ``automatic`` with ``failover_seconds``), else its repair time
   (``mttr_seconds``).
6. ``data-loss-window``: the most recent data a failure of the primary can lose, the least of the
   declared mechanisms: synchronous replication (0), asynchronous replication
   (``replication_lag_seconds``), backups (``backup_interval_seconds``). Every mechanism is listed.

Not supported: failure rates (no conversion between failure rate, MTBF and availability is
assumed), provider SLAs that are not declared, partial degradation, and anything the architecture
does not declare: those stay unknown, never a typical value.
"""

import math
from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.domain.capacity.results import Estimate, Source
from core.domain.capacity.units import rounded_text
from core.domain.engine_results import Evidence
from core.domain.numbers import arithmetic
from core.domain.reliability.values import availability, seconds

from .engine import OUT_OF_SCOPE, ModelInputs, ModelMeta, ModelOutput

K = NodeKind
SCOPE = frozenset(NodeKind) - OUT_OF_SCOPE
RUNNING = frozenset(
    {K.SERVICE, K.WORKER, K.GATEWAY, K.LOAD_BALANCER, K.DATABASE, K.CACHE, K.QUEUE, K.OBSERVABILITY}
)
DATA = frozenset({K.DATABASE, K.CACHE, K.STORAGE, K.QUEUE})
MAX_REPLICAS = 1000  # beyond it the binomial sum is not calculated (reported as magnitude)


def _unknown(
    inputs: ModelInputs,
    resource: str,
    basis: str,
    missing: tuple[str, ...],
    evidence: tuple[Evidence, ...] = (),
) -> Estimate:
    return Estimate(inputs.node.id, resource, None, Source.UNKNOWN, basis, inputs=evidence, missing=missing)


class DeclaredAvailability:
    meta = ModelMeta(
        id="declared-availability",
        version=1,
        name="Declared availability",
        description="The component's declared availability (e.g. a provider's commitment).",
        kinds=SCOPE,
        resources=("availability",),
        configuration=("availability",),
        assumptions=("The declared value describes the component as a whole, as deployed.",),
        formula="configuration.availability",
        limitations=("A declared value is taken as stated: it is not verified.",),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        value = inputs.facts.number("availability")
        assert value is not None  # noqa: S101 -- required by the model's configuration
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id,
                    "availability",
                    availability(value),
                    Source.DECLARED,
                    self.meta.formula,
                    inputs=inputs.facts.evidence(["availability"]),
                ),
            )
        )


class DeclaredReplicaAvailability:
    meta = ModelMeta(
        id="declared-replica-availability",
        version=1,
        name="Declared replica availability",
        description="One replica's availability as declared.",
        kinds=RUNNING,
        resources=("replica_availability",),
        configuration=("replica_availability",),
        assumptions=("Every replica has the declared availability.",),
        formula="configuration.replica_availability",
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        value = inputs.facts.number("replica_availability")
        assert value is not None  # noqa: S101 -- required by the model's configuration
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id,
                    "replica_availability",
                    availability(value),
                    Source.DECLARED,
                    self.meta.formula,
                    inputs=inputs.facts.evidence(["replica_availability"]),
                ),
            )
        )


class MtbfMttr:
    meta = ModelMeta(
        id="mtbf-mttr",
        version=1,
        name="Availability from MTBF and MTTR",
        description="One replica's steady-state availability from its MTBF and MTTR.",
        kinds=RUNNING,
        resources=("replica_availability",),
        configuration=("mtbf_seconds", "mttr_seconds"),
        assumptions=(
            "Long-run (steady-state) average: MTBF and MTTR are means of one replica over the same period.",
            "MTBF is the mean up time between failures; repair starts at failure.",
        ),
        formula="mtbf_seconds / (mtbf_seconds + mttr_seconds)",
        unsupported=("MTBF and MTTR both 0 (undefined).", "Failure rates (no conversion is assumed)."),
        limitations=("An average: it says nothing about the length or timing of any one outage.",),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        mtbf, mttr = inputs.facts.number("mtbf_seconds"), inputs.facts.number("mttr_seconds")
        assert mtbf is not None  # noqa: S101 -- required by the model's configuration
        assert mttr is not None  # noqa: S101
        evidence = inputs.facts.evidence(["mtbf_seconds", "mttr_seconds"])
        if mtbf + mttr == 0:
            missing = ("defined.mtbf_seconds_plus_mttr_seconds",)
            return ModelOutput(
                (_unknown(inputs, "replica_availability", self.meta.formula, missing, evidence),)
            )
        with arithmetic():
            value = mtbf / (mtbf + mttr)
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id,
                    "replica_availability",
                    availability(value),
                    Source.MODEL_ESTIMATE,
                    self.meta.formula,
                    self.meta.id,
                    self.meta.version,
                    evidence,
                ),
            )
        )


def k_of_n(a: Decimal, n: int, k: int) -> Decimal:
    """The probability that at least ``k`` of ``n`` independent replicas, each available ``a``, are."""
    total = Decimal(0)
    with arithmetic():
        for i in range(k, n + 1):
            total += math.comb(n, i) * a**i * (1 - a) ** (n - i)
    return total


class Replicas:
    meta = ModelMeta(
        id="replicas",
        version=1,
        name="Replicated component",
        description="The component from its replicas: one replica, or at least k of n available.",
        kinds=RUNNING,
        resources=("availability",),
        configuration=("replicas",),
        assumptions=(
            "Replicas are identical, each with the replica availability.",
            "With several replicas: failures are independent (declared), and automatic failover moves work "
            "off a failed replica (declared, not verified); failover time is not deducted.",
        ),
        formula="n = 1: a; n > 1: sum over i = k..n of C(n, i) a^i (1 - a)^(n - i), k = min_healthy_replicas",
        unsupported=(
            "Correlated or undeclared failure independence.",
            "Manual or no failover, or failover mode not declared.",
            "min_healthy_replicas above replicas, or more than 1,000 replicas.",
        ),
        limitations=("Ignores detection and failover time, partial degradation and shared dependencies.",),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        facts, formula = inputs.facts, self.meta.formula
        n = facts.number("replicas")
        assert n is not None  # noqa: S101 -- required by the model's configuration
        replica = inputs.estimate("replica_availability")
        evidence = facts.evidence(
            ["replicas", "min_healthy_replicas", "failure_independence", "failover_mode"]
        )
        if replica is None or replica.quantity is None:
            missing = ("configuration.replica_availability",)
            return ModelOutput((_unknown(inputs, "availability", formula, missing, evidence),))
        a = replica.quantity.value
        evidence = (*evidence, Evidence("replica_availability", rounded_text(a)))
        if n == 1:
            return self._known(inputs, a, evidence)
        if n == 0:
            return ModelOutput((_unknown(inputs, "availability", formula, ("positive.replicas",), evidence),))
        k = facts.number("min_healthy_replicas")
        independence, failover = facts.known("failure_independence"), facts.known("failover_mode")
        why: tuple[str, ...] = ()
        if k is None:
            why = ("configuration.min_healthy_replicas",)
        elif k > n or n > MAX_REPLICAS:
            why = ("consistent.min_healthy_replicas" if k > n else "magnitude",)
        elif independence != "independent":
            why = (
                ("independent.failure_independence",)
                if independence == "correlated"
                else ("configuration.failure_independence",)
            )
        elif failover != "automatic":
            why = (
                ("automatic.failover_mode",)
                if failover in ("none", "manual")
                else ("configuration.failover_mode",)
            )
        if why:
            return ModelOutput((_unknown(inputs, "availability", formula, why, evidence),))
        assert k is not None  # noqa: S101 -- checked above
        return self._known(inputs, k_of_n(a, int(n), int(k)), evidence)

    def _known(self, inputs: ModelInputs, value: Decimal, evidence: tuple[Evidence, ...]) -> ModelOutput:
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id,
                    "availability",
                    availability(value),
                    Source.MODEL_ESTIMATE,
                    self.meta.formula,
                    self.meta.id,
                    self.meta.version,
                    evidence,
                ),
            )
        )


class RecoveryTime:
    meta = ModelMeta(
        id="recovery-time",
        version=1,
        name="Recovery time",
        description="How long a failure interrupts the component: its failover time, else its repair time.",
        kinds=RUNNING,
        resources=("recovery_time",),
        assumptions=(
            "A declared failover (manual or automatic) takes its declared time and works (not verified).",
            "Without failover, service returns when the failed replica is repaired (MTTR, a mean).",
        ),
        formula="failover_seconds when failover_mode is manual or automatic, else mttr_seconds",
        unsupported=("Neither a failover time nor a repair time declared.",),
        limitations=("A mean or a declared figure: individual incidents can take longer.",),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        facts = inputs.facts
        mode, failover, mttr = (
            facts.known("failover_mode"),
            facts.number("failover_seconds"),
            facts.number("mttr_seconds"),
        )
        evidence = facts.evidence(["failover_mode", "failover_seconds", "mttr_seconds"])
        if mode in ("manual", "automatic") and failover is not None:
            value, basis = failover, "failover_seconds (declared failover)"
        elif mttr is not None:
            value, basis = mttr, "mttr_seconds (repair)"
        else:
            missing = ("configuration.mttr_seconds",) + (
                ("configuration.failover_seconds",) if mode in ("manual", "automatic") else ()
            )
            return ModelOutput((_unknown(inputs, "recovery_time", self.meta.formula, missing, evidence),))
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id,
                    "recovery_time",
                    seconds(value),
                    Source.MODEL_ESTIMATE,
                    basis,
                    self.meta.id,
                    self.meta.version,
                    evidence,
                ),
            )
        )


class DataLossWindow:
    meta = ModelMeta(
        id="data-loss-window",
        version=1,
        name="Data loss window",
        description="The most recent data losing the primary can lose: the least declared mechanism.",
        kinds=DATA,
        resources=("data_loss_window",),
        assumptions=(
            "Synchronous replication loses nothing on failover to its replica (declared, not verified).",
            "Asynchronous replication loses up to its declared lag; a restore up to the backup interval.",
        ),
        formula="min(0 if replication_mode is synchronous, replication_lag_seconds, backup_interval_seconds)",
        unsupported=(
            "No replication or backup declared; asynchronous replication without a lag; backups without "
            "an interval.",
        ),
        limitations=(
            "Data corruption or region loss may need the backups even where replication is faster.",
        ),
    )

    def estimate(self, inputs: ModelInputs) -> ModelOutput:
        facts = inputs.facts
        names = ["replication_mode", "replication_lag_seconds", "backup_enabled", "backup_interval_seconds"]
        evidence = facts.evidence(names)
        windows: list[Decimal] = []
        mode = facts.known("replication_mode")
        if mode == "synchronous":
            windows.append(Decimal(0))
        elif mode == "asynchronous" and (lag := facts.number("replication_lag_seconds")) is not None:
            windows.append(lag)
        interval = facts.number("backup_interval_seconds")
        if interval is not None and facts.known("backup_enabled") is not False:
            windows.append(interval)
        if not windows:
            missing = ["configuration.backup_interval_seconds"]
            missing.append(
                "configuration.replication_lag_seconds"
                if mode == "asynchronous"
                else "configuration.replication_mode"
            )
            return ModelOutput(
                (_unknown(inputs, "data_loss_window", self.meta.formula, tuple(missing), evidence),)
            )
        return ModelOutput(
            (
                Estimate(
                    inputs.node.id,
                    "data_loss_window",
                    seconds(min(windows)),
                    Source.MODEL_ESTIMATE,
                    self.meta.formula,
                    self.meta.id,
                    self.meta.version,
                    evidence,
                ),
            )
        )


MODELS = (
    DeclaredAvailability(),
    DeclaredReplicaAvailability(),
    MtbfMttr(),
    Replicas(),
    RecoveryTime(),
    DataLossWindow(),
)
