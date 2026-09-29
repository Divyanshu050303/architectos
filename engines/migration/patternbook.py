"""The migration patterns this ArchitectOS ships.

Templates (one change each): ``reconfigure``, ``provision``, ``decommission``, ``reroute``.
Strategies (the plan as a whole), supported only when the architecture declares their
prerequisites: ``in_place`` (the documented default), ``rolling``, ``blue_green``,
``replication_cutover``. Registered as unsupported, so a request for them is answered rather than
fabricated: ``canary``, ``expand_contract``, ``strangler``.

Prerequisites are read from what the source and target revisions declare (replicas, health checks,
a routing component in front, a replication mode) — never assumed, and never from a technology's
name. Durations, data volumes and success probabilities are never stated.
"""

from dataclasses import dataclass

from core.architecture_ir.component import NodeKind
from core.architecture_ir.diff import ChangeKind
from core.architecture_ir.node import Node
from core.domain.evolution.values import EvidenceSource
from core.domain.migrations.changes import Aspect, MigrationChange
from core.domain.migrations.values import DowntimeStatus, Reversibility, RiskCategory, StepType

from .patterns import Assessment, PatternKind, PatternMeta, PlanningContext, Registry

A, K, R, T = Aspect, ChangeKind, RiskCategory, StepType
S = EvidenceSource
STATELESS = frozenset({NodeKind.SERVICE.value, NodeKind.WORKER.value, NodeKind.GATEWAY.value})
ROUTERS = frozenset({NodeKind.LOAD_BALANCER, NodeKind.GATEWAY})
REPLICATING = frozenset({"asynchronous", "synchronous"})
MIN_ROLLING_REPLICAS = 2  # one replaced while another serves
CONFIGURATION_ASPECTS = (
    A.CONFIGURATION,
    A.RESOURCES,
    A.SCALING,
    A.SECURITY,
    A.OBSERVABILITY,
    A.TECHNOLOGY,
    A.PLACEMENT,
)


def _replicas(node: Node | None) -> int:
    value = node.configuration.values.get("replicas") if node else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _stateless_modified(context: PlanningContext) -> tuple[str, ...]:
    return tuple(
        c.element_id
        for c in context.plannable()
        if c.element == "node" and c.change is K.MODIFIED and c.kind in STATELESS
    )


def stateful_replacements(context: PlanningContext) -> tuple[tuple[str, str], ...]:
    """(source node, target node) pairs whose data must move: a stateful node changing technology
    (the same id), or a stateful node removed while exactly one of the same kind is added."""
    plannable = context.plannable()
    pairs = [
        (c.element_id, c.element_id)
        for c in plannable
        if c.element == "node" and c.stateful and c.change is K.MODIFIED and A.TECHNOLOGY in c.aspects
    ]
    removed = [c for c in plannable if c.element == "node" and c.stateful and c.change is K.REMOVED]
    added = [c for c in plannable if c.element == "node" and c.stateful and c.change is K.ADDED]
    for old in removed:
        same_kind = [new for new in added if new.kind == old.kind]
        if len(same_kind) == 1:  # one replacement: anything else is for a person to pair
            pairs.append((old.element_id, same_kind[0].element_id))
    return tuple(sorted(pairs))


# --- templates -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Template:
    meta: PatternMeta

    def matching(self, context: PlanningContext) -> tuple[MigrationChange, ...]:
        """The changes it carries out. A stateful technology change moves data: a strategy carries it
        out, not a reconfiguration."""
        moved = {old for old, new in stateful_replacements(context) if old == new}
        return tuple(
            c
            for c in context.plannable()
            if c.change in self.meta.changes
            and set(c.aspects) & set(self.meta.aspects)
            and not (self.meta.id == "reconfigure" and c.element_id in moved)
        )

    def assess(self, context: PlanningContext) -> Assessment:
        subjects = tuple(c.element_id for c in self.matching(context))
        return Assessment(bool(subjects), subjects)


RECONFIGURE = Template(
    PatternMeta(
        id="reconfigure",
        version=1,
        name="Reconfigure a component or connection",
        description="Apply the target's configuration to an existing element, then verify it.",
        kind=PatternKind.TEMPLATE,
        changes=(K.MODIFIED,),
        aspects=CONFIGURATION_ASPECTS,
        evidence=(S.VALIDATION,),
        preconditions=("The target configuration validates.",),
        capabilities=(),
        steps=(T.CONFIGURE, T.VERIFY),
        dependency_rules=("Verification follows the configuration change.",),
        risks=(R.DOWNTIME, R.COMPATIBILITY_FAILURE),
        tradeoffs=(),
        downtime=DowntimeStatus.UNKNOWN,
        reversibility=Reversibility.CONDITIONALLY_REVERSIBLE,
        rollback="Restore the previous values of the changed settings.",
        data=None,
        validation=("The target revision's validation has no blocking finding for the element.",),
        unsupported=("A stateful component's technology change (it moves data: see the strategies).",),
    )
)
PROVISION = Template(
    PatternMeta(
        id="provision",
        version=1,
        name="Provision a new component or connection",
        description="Provision an element the target adds, configure it, and verify it before it is used.",
        kind=PatternKind.TEMPLATE,
        changes=(K.ADDED,),
        aspects=(A.PROVISIONING,),
        evidence=(S.VALIDATION,),
        preconditions=("Its configuration in the target validates.",),
        capabilities=(),
        steps=(T.PROVISION, T.CONFIGURE, T.VERIFY),
        dependency_rules=(
            "A connection is provisioned after both of its endpoints.",
            "Nothing is routed to a new component before it is verified.",
        ),
        risks=(R.CAPACITY_EXHAUSTION, R.COST_INCREASE),
        tradeoffs=(),
        downtime=DowntimeStatus.MODELED_ONLINE,
        reversibility=Reversibility.REVERSIBLE,
        rollback="Remove the new element before anything depends on it.",
        data=None,
        validation=("The target revision validates.",),
        unsupported=("Populating a new stateful component with existing data (see the strategies).",),
    )
)
DECOMMISSION = Template(
    PatternMeta(
        id="decommission",
        version=1,
        name="Decommission after verification",
        description="Retire an element the target removes, only after verifying nothing still depends on it.",
        kind=PatternKind.TEMPLATE,
        changes=(K.REMOVED,),
        aspects=(A.DECOMMISSIONING,),
        evidence=(S.VALIDATION,),
        preconditions=("No remaining element depends on it.",),
        capabilities=(),
        steps=(T.VERIFY, T.DECOMMISSION),
        dependency_rules=(
            "A component is decommissioned after the connections to it.",
            "Decommissioning follows every step that moves traffic or data away from it.",
        ),
        risks=(R.DATA_LOSS, R.IRREVERSIBLE_CHANGE),
        tradeoffs=(),
        downtime=DowntimeStatus.UNKNOWN,
        reversibility=Reversibility.UNKNOWN,
        rollback="Re-provision it from the source revision; its data only if it was retained.",
        data="A stateful component's data is retained or deliberately discarded, as a person decides.",
        validation=("The target revision validates without it.",),
        unsupported=(),
    )
)
REROUTE = Template(
    PatternMeta(
        id="reroute",
        version=1,
        name="Reroute a connection",
        description="Point a connection at its target endpoints or protocol, then verify it.",
        kind=PatternKind.TEMPLATE,
        changes=(K.MODIFIED,),
        aspects=(A.ROUTING, A.PROTOCOL),
        evidence=(S.VALIDATION,),
        preconditions=("Its new endpoint is provisioned and verified.",),
        capabilities=(),
        steps=(T.PREPARE, T.CUTOVER, T.VERIFY),
        dependency_rules=("The cutover follows the verification of its new endpoint.",),
        risks=(R.COMPATIBILITY_FAILURE, R.DOWNTIME),
        tradeoffs=(),
        downtime=DowntimeStatus.UNKNOWN,
        reversibility=Reversibility.CONDITIONALLY_REVERSIBLE,
        rollback="Point the connection back at its previous endpoint while it is retained.",
        data=None,
        validation=("The target revision validates.",),
        unsupported=(),
    )
)


# --- strategies ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InPlace:
    meta = PatternMeta(
        id="in_place",
        version=1,
        name="In place",
        description="Carry out every change on the components as they are, one after the other; data moves "
        "offline where a stateful component is replaced.",
        kind=PatternKind.STRATEGY,
        changes=(K.ADDED, K.REMOVED, K.MODIFIED),
        aspects=tuple(Aspect),
        evidence=(S.VALIDATION,),
        preconditions=(),
        capabilities=(),
        steps=(T.PREPARE, T.CONFIGURE, T.VERIFY),
        dependency_rules=("The changes follow the templates' order.",),
        risks=(R.DOWNTIME,),
        tradeoffs=(
            "Simplest to carry out; each component is changed while it runs or is stopped.",
            "Downtime depends on each component and is not modeled; replacing a stateful component stops "
            "writes while its data is copied.",
        ),
        downtime=DowntimeStatus.UNKNOWN,
        reversibility=Reversibility.CONDITIONALLY_REVERSIBLE,
        rollback="Restore the source configuration; a replaced stateful component only while its data is "
        "kept.",
        data="Stateful replacements copy data offline, with writes stopped.",
        validation=("The target revision validates.",),
        unsupported=(),
    )

    def assess(self, context: PlanningContext) -> Assessment:
        subjects = tuple(c.element_id for c in context.plannable())
        return Assessment(
            bool(subjects), subjects, reasons=() if subjects else ("There is nothing to plan.",)
        )


@dataclass(frozen=True, slots=True)
class Rolling:
    meta = PatternMeta(
        id="rolling",
        version=1,
        name="Rolling",
        description="Replace a stateless component's instances one at a time, each verified healthy before "
        "the next.",
        kind=PatternKind.STRATEGY,
        changes=(K.MODIFIED,),
        aspects=CONFIGURATION_ASPECTS,
        evidence=(S.VALIDATION, S.RELIABILITY),
        preconditions=(
            "At least 2 replicas declared in the source and the target.",
            "A health check declared in the target.",
        ),
        capabilities=("horizontal_scaling",),
        steps=(T.PREPARE, T.CONFIGURE, T.VERIFY),
        dependency_rules=("Each instance is verified healthy before the next one is replaced.",),
        risks=(R.COMPATIBILITY_FAILURE, R.CAPACITY_EXHAUSTION),
        tradeoffs=(
            "Online while one instance at a time is replaced; capacity is reduced by one instance meanwhile.",
            "Old and new versions run side by side during the roll-out: both must be compatible.",
        ),
        downtime=DowntimeStatus.MODELED_ONLINE,
        reversibility=Reversibility.REVERSIBLE,
        rollback="Roll the remaining instances back to the source configuration.",
        data=None,
        validation=("The target revision validates.",),
        unsupported=("Stateful components (their data is not rolled).",),
    )

    def assess(self, context: PlanningContext) -> Assessment:
        subjects = _stateless_modified(context)
        if not subjects:
            return Assessment(False, reasons=("No stateless component is modified.",))
        missing = []
        for node_id in subjects:
            before, after = context.node(context.source, node_id), context.node(context.target, node_id)
            if min(_replicas(before), _replicas(after)) < MIN_ROLLING_REPLICAS:
                missing.append(f"replicas of {node_id}: at least 2 declared in the source and the target.")
            if not (after and after.configuration.values.get("health_check") is True):
                missing.append(f"health_check of {node_id} declared true in the target.")
        return Assessment(True, subjects, tuple(missing))


@dataclass(frozen=True, slots=True)
class BlueGreen:
    meta = PatternMeta(
        id="blue_green",
        version=1,
        name="Blue-green",
        description="Run the target configuration of a stateless component beside the source, verify it, "
        "then switch traffic at the component routing to it.",
        kind=PatternKind.STRATEGY,
        changes=(K.MODIFIED,),
        aspects=CONFIGURATION_ASPECTS,
        evidence=(S.VALIDATION, S.CAPACITY, S.COST),
        preconditions=("A load balancer or gateway routes to the component in the target.",),
        capabilities=("load_balancing",),
        steps=(T.PROVISION, T.VERIFY, T.CUTOVER, T.VERIFY, T.DECOMMISSION),
        dependency_rules=(
            "Traffic switches only after the new environment is verified.",
            "The old environment is retained until the new one is verified under traffic.",
        ),
        risks=(R.COST_INCREASE, R.CAPACITY_EXHAUSTION),
        tradeoffs=(
            "Online switch with a retained fallback; both environments run (and are billed) during the "
            "switch.",
            "Needs capacity for a second environment; that capacity is not modeled here.",
        ),
        downtime=DowntimeStatus.MODELED_ONLINE,
        reversibility=Reversibility.REVERSIBLE,
        rollback="Switch traffic back to the retained environment.",
        data=None,
        validation=("The target revision validates.",),
        unsupported=("Stateful components (two environments would hold diverging data).",),
    )

    def assess(self, context: PlanningContext) -> Assessment:
        subjects = _stateless_modified(context)
        if not subjects:
            return Assessment(False, reasons=("No stateless component is modified.",))
        missing = tuple(
            f"A load balancer or gateway in front of {node_id} in the target, to switch traffic between the "
            "two environments."
            for node_id in subjects
            if not any(n.kind in ROUTERS for n in context.incoming(context.target, node_id))
        )
        return Assessment(True, subjects, missing)


@dataclass(frozen=True, slots=True)
class ReplicationCutover:
    meta = PatternMeta(
        id="replication_cutover",
        version=1,
        name="Replication, then cutover",
        description="Replicate a stateful component's data to its replacement, verify consistency, then cut "
        "writes over; the source is retained until the target is verified.",
        kind=PatternKind.STRATEGY,
        changes=(K.MODIFIED, K.ADDED, K.REMOVED),
        aspects=(A.TECHNOLOGY, A.PROVISIONING, A.DECOMMISSIONING),
        evidence=(S.VALIDATION, S.RELIABILITY),
        preconditions=("The target declares how data is replicated (replication_mode).",),
        capabilities=("read_replication",),
        steps=(T.PROVISION, T.REPLICATE, T.BACKFILL, T.VERIFY, T.CUTOVER, T.VERIFY, T.DECOMMISSION),
        dependency_rules=(
            "Replication starts after the target is provisioned; the backfill after replication.",
            "The cutover follows a verified consistency check.",
            "The source is decommissioned only after the target is verified under traffic.",
        ),
        risks=(R.DATA_INCONSISTENCY, R.DATA_LOSS, R.ROLLBACK_LIMITATION, R.DOWNTIME),
        tradeoffs=(
            "Writes stop only for the cutover itself; how long is not modeled.",
            "Replication must exist between the source and the target technologies.",
            "After writes reach the target, switching back to the source loses them unless they are "
            "replicated back.",
        ),
        downtime=DowntimeStatus.POTENTIAL_DOWNTIME,
        reversibility=Reversibility.CONDITIONALLY_REVERSIBLE,
        rollback="Route back to the source before any write reaches the target.",
        data="Initial copy (backfill), continuous replication, consistency verification before the cutover.",
        validation=("The target revision validates.",),
        unsupported=("Replicating between technologies no declared method connects.",),
    )

    def assess(self, context: PlanningContext) -> Assessment:
        pairs = stateful_replacements(context)
        if not pairs:
            return Assessment(False, reasons=("No stateful component is replaced.",))
        missing = []
        for _, target_id in pairs:
            node = context.node(context.target, target_id)
            if not (node and node.configuration.values.get("replication_mode") in REPLICATING):
                missing.append(
                    f"replication_mode of {target_id} in the target (asynchronous or synchronous): how data "
                    "is replicated from the source before the cutover."
                )
        subjects = tuple(sorted({i for pair in pairs for i in pair}))
        return Assessment(True, subjects, tuple(missing))


@dataclass(frozen=True, slots=True)
class Unsupported:
    meta: PatternMeta

    def assess(self, context: PlanningContext) -> Assessment:
        return Assessment(False, reasons=self.meta.unsupported)


def _unsupported(pattern_id: str, name: str, reason: str) -> Unsupported:
    return Unsupported(
        PatternMeta(
            id=pattern_id,
            version=1,
            name=name,
            description=f"{name} is not planned by ArchitectOS.",
            kind=PatternKind.STRATEGY,
            changes=(),
            aspects=(),
            evidence=(),
            preconditions=(),
            capabilities=(),
            steps=(),
            dependency_rules=(),
            risks=(),
            tradeoffs=(),
            downtime=DowntimeStatus.UNKNOWN,
            reversibility=Reversibility.UNKNOWN,
            rollback="Not planned.",
            data=None,
            validation=(),
            unsupported=(reason,),
            supported=False,
        )
    )


CANARY = _unsupported(
    "canary", "Canary", "Traffic weights and the share of requests per version are not modeled."
)
EXPAND_CONTRACT = _unsupported(
    "expand_contract", "Expand and contract", "Data schemas and their versions are not modeled."
)
STRANGLER = _unsupported(
    "strangler", "Strangler", "Service boundaries and the routing of individual capabilities are not modeled."
)


def default_registry() -> Registry:
    """A new registry each time: a registry can still be registered into, so none is shared."""
    return Registry(
        [
            RECONFIGURE,
            PROVISION,
            DECOMMISSION,
            REROUTE,
            InPlace(),
            Rolling(),
            BlueGreen(),
            ReplicationCutover(),
            CANARY,
            EXPAND_CONTRACT,
            STRANGLER,
        ]
    )
