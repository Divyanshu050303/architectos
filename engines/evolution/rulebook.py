"""The shipped evolution rules (version 1). Each proposes only configuration changes to existing
elements, with values that come from the evidence (a capacity model's stated replicas or cores) or
from a documented, minimal rule (one more replica removes a single instance; a boolean control set to
true) — never an invented target, mechanism or pattern.

- ``scale-replicas``: the replicas a capacity model states would bring a resource within its target.
- ``scale-cpu``: the CPU cores per replica the cpu-demand model states.
- ``add-replica``: a second replica for a component reported as a single point of failure (or with
  no redundancy) that runs one declared replica.
- ``require-tls``: ``tls`` on a connection reported carrying sensitive data unencrypted.
- ``encrypt-at-rest``: ``encryption_at_rest`` on a store reported holding sensitive data unencrypted.
- ``enable-signal``: logs, traces or a health check on a component reported declaring it absent.

Not proposed (reported instead): an authentication or authorization mechanism (choosing one is a
design decision), metric or alert kinds (which to emit is not stated), redundancy placement across
zones or regions, and every structural change.
"""

from decimal import Decimal, InvalidOperation

from core.architecture_ir.configuration import ConfigValue
from core.domain.engine_results import Evidence
from core.domain.evolution.candidates import Candidate, Effect, RuleRef
from core.domain.evolution.results import EvolutionFinding, FindingType
from core.domain.evolution.triggers import Trigger
from core.domain.evolution.values import Basis, CandidateCategory, GoalType
from core.domain.evolution.values import EvidenceSource as S
from core.domain.simulations.scenarios import ConfigurationChange, specs_of

from .rules import Outcome, Registry, RuleContext, RuleMeta

C, G, B = CandidateCategory, GoalType, Basis
CAPACITY_GOALS = (G.INCREASE_WORKLOAD, G.SATISFY_REQUIREMENT)
FINDING_GOALS = (G.ADDRESS_FINDING, G.SATISFY_REQUIREMENT)
SIGNALS = {"logs_absent": "logs", "traces_absent": "traces", "health_check_absent": "health_check"}
SIGNAL_NAMES = {"logs": "logs", "traces": "traces", "health_check": "a health check"}


def _candidate(  # noqa: PLR0913 -- one argument per part of the proposal
    meta: RuleMeta,
    trigger: Trigger,
    context: RuleContext,
    changes: tuple[ConfigurationChange, ...],
    *,
    title: str,
    description: str,
    rationale: str,
    benefits: tuple[Effect, ...] = (),
    tradeoffs: tuple[Effect, ...] = (),
    complexity: tuple[Effect, ...] = (),
    migration: tuple[Effect, ...] = (),
    risks: tuple[Effect, ...] = (),
    missing: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        rule=RuleRef(meta.id, meta.version),
        baseline=context.baseline,
        category=meta.category,
        title=title,
        description=description,
        changes=changes,
        goals=(trigger.goal,),
        rationale=rationale,
        evidence=(trigger.evidence,),
        benefits=benefits,
        tradeoffs=tradeoffs,
        complexity=complexity,
        migration=migration,
        risks=risks,
        missing=missing,
    )


def _not_proposed(trigger: Trigger, reason: str, missing: tuple[str, ...] = ()) -> EvolutionFinding:
    return EvolutionFinding(
        FindingType.NO_APPLICABLE_RULE,
        reason,
        trigger.goal,
        (trigger.element_id,),
        (trigger.evidence,),
        missing,
    )


def _number(raw: str | None) -> Decimal | None:
    try:
        return Decimal(raw) if raw is not None else None
    except InvalidOperation:
        return None


def _declared(value: ConfigValue | None) -> Decimal | None:
    return Decimal(value) if isinstance(value, int | Decimal) and not isinstance(value, bool) else None


def _applies(prop: str, kind: str) -> bool:
    return any(kind in s.applies_to for s in specs_of(prop))


def _modeled(trigger: Trigger, code: str, statement: str) -> Effect:
    """A benefit the capacity model itself states, with the model's facts as its evidence."""
    return Effect("capacity", code, statement, B.MODELED, trigger.facts)


# --- capacity -------------------------------------------------------------------------------------


class ScaleReplicas:
    meta = RuleMeta(
        id="scale-replicas",
        version=1,
        name="Scale out to the replicas a capacity model states",
        description="Where a capacity model states how many replicas bring a resource within its "
        "target utilization, propose that replica count.",
        category=C.SCALING,
        triggers=((S.CAPACITY, "work_rate"), (S.CAPACITY, "cpu")),
        goals=CAPACITY_GOALS,
        evidence=(S.CAPACITY,),
        properties=("replicas",),
        conditions=("A component whose capacity is stated per replica (a horizontal scaling option).",),
        preconditions=(
            "The component exists, runs a declared number of replicas, and the model's current count "
            "is the declared one.",
            "The stated count is above the declared one.",
        ),
        benefits=("The capacity model states the resource is within its target at that count.",),
        tradeoffs=("More instances run and are billed.",),
        unsupported=(
            "Scaling a model does not state (no implicit linear scaling).",
            "Where the added load goes downstream (evaluated by capacity on the candidate).",
        ),
        validation=(
            "The replica count respects the component's declared minimum, maximum and autoscaling bounds.",
        ),
        analyses=(S.CAPACITY, S.COST, S.RELIABILITY),
    )

    def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Outcome, ...]:
        if trigger.fact("scaling") != "horizontal":
            return ()
        node = context.ir.node(trigger.element_id)
        current, required = _number(trigger.fact("current")), _number(trigger.fact("required"))
        if node is None or not _applies("replicas", node.kind) or current is None or required is None:
            return (_not_proposed(trigger, f"{trigger.element_id} has no replicas to scale."),)
        declared = _declared(node.configuration.get("replicas"))
        if declared != current:
            return (
                _not_proposed(
                    trigger,
                    f"The capacity evidence counts {current} replicas of {node.id}, which declares "
                    f"{declared if declared is not None else 'none'}: the evidence does not describe it.",
                    (f"{node.id}.configuration.replicas",) if declared is None else (),
                ),
            )
        if required <= current or required != required.to_integral_value():
            return ()
        count = int(required)
        model = trigger.fact("model") or "capacity"
        return (
            _candidate(
                self.meta,
                trigger,
                context,
                (ConfigurationChange(node.id, "replicas", count),),
                title=f"Run {node.id} with {count} replicas",
                description=f"Raise {node.id} from {int(current)} to {count} replicas, the count the "
                f"{model} model states for {trigger.code}.",
                rationale=f"The {model} model ({trigger.fact('basis') or 'as stated'}) states {count} "
                f"replicas bring {trigger.code} of {node.id} within the target utilization.",
                benefits=(
                    _modeled(
                        trigger,
                        "within_target",
                        f"{trigger.code} of {node.id} within the target utilization at {count} replicas.",
                    ),
                ),
                tradeoffs=(
                    Effect(
                        "cost",
                        "more_instances",
                        f"{count - int(current)} more instances run and are billed.",
                        B.RULE,
                    ),
                ),
                complexity=(
                    Effect(
                        "operations",
                        "more_replicas",
                        "More replicas to deploy, observe and keep consistent.",
                        B.CONSIDERATION,
                    ),
                ),
                migration=(
                    Effect(
                        "migration",
                        "stateless_required",
                        f"Scaling out assumes {node.id} holds no state a single replica owns; scaling back "
                        "reverses it.",
                        B.CONSIDERATION,
                    ),
                ),
                risks=(
                    Effect(
                        "capacity",
                        "downstream_load",
                        "Dependencies may receive more concurrent connections and requests.",
                        B.CONSIDERATION,
                    ),
                ),
            ),
        )


class ScaleCpu:
    meta = RuleMeta(
        id="scale-cpu",
        version=1,
        name="Scale up to the CPU cores the cpu-demand model states",
        description="Where the cpu-demand model states the cores per replica that bring CPU within its "
        "target utilization, propose that CPU limit.",
        category=C.SCALING,
        triggers=((S.CAPACITY, "cpu"),),
        goals=CAPACITY_GOALS,
        evidence=(S.CAPACITY,),
        properties=("cpu_limit_cores",),
        conditions=("A component declaring its replicas and CPU limit (a vertical scaling option).",),
        preconditions=(
            "The component exists and declares the CPU limit the model read.",
            "The stated cores are above the declared limit.",
        ),
        benefits=("The cpu-demand model states CPU is within its target at that limit.",),
        tradeoffs=(
            "Larger instances are billed; instance sizes have an upper limit the model does not state.",
        ),
        unsupported=("Instance types and their sizes (the model states cores, not a product).",),
        validation=("The CPU limit is a valid positive number of cores.",),
        analyses=(S.CAPACITY, S.COST),
    )

    def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Outcome, ...]:
        if trigger.fact("scaling") != "vertical":
            return ()
        node = context.ir.node(trigger.element_id)
        current, required = _number(trigger.fact("current")), _number(trigger.fact("required"))
        if node is None or current is None or required is None:
            return (_not_proposed(trigger, f"{trigger.element_id} has no CPU limit to scale."),)
        if _declared(node.configuration.get("cpu_limit_cores")) != current:
            return (
                _not_proposed(
                    trigger,
                    f"The capacity evidence reads a CPU limit of {current} cores for {node.id}, which "
                    "declares another: the evidence does not describe it.",
                ),
            )
        if required <= current:
            return ()
        return (
            _candidate(
                self.meta,
                trigger,
                context,
                (ConfigurationChange(node.id, "cpu_limit_cores", required),),
                title=f"Give {node.id} {required} CPU cores per replica",
                description=f"Raise {node.id}'s CPU limit from {current} to {required} cores per replica, "
                "as the cpu-demand model states.",
                rationale=f"The cpu-demand model ({trigger.fact('basis') or 'as stated'}) states {required} "
                f"cores per replica bring CPU of {node.id} within the target utilization.",
                benefits=(
                    _modeled(
                        trigger, "within_target", f"CPU of {node.id} within the target at {required} cores."
                    ),
                ),
                tradeoffs=(
                    Effect("cost", "larger_instances", "Larger instances are billed.", B.RULE),
                    Effect(
                        "capacity",
                        "size_limit",
                        "Scaling up stops at the largest instance available, which the model does not state.",
                        B.CONSIDERATION,
                    ),
                ),
                migration=(
                    Effect(
                        "migration",
                        "restart",
                        "Changing an instance's size usually restarts it.",
                        B.CONSIDERATION,
                    ),
                ),
            ),
        )


# --- reliability ----------------------------------------------------------------------------------


class AddReplica:
    meta = RuleMeta(
        id="add-replica",
        version=1,
        name="Add a second replica to a single instance",
        description="Where the Reliability Engine reports a component as a single point of failure (or "
        "without redundancy) and it runs one declared replica, propose a second one.",
        category=C.REDUNDANCY,
        triggers=((S.RELIABILITY, "single_point_of_failure"), (S.RELIABILITY, "no_redundancy")),
        goals=(*FINDING_GOALS, G.AVAILABILITY_OBJECTIVE),
        evidence=(S.RELIABILITY,),
        properties=("replicas",),
        conditions=("A component the Reliability Engine reports as a single instance.",),
        preconditions=("The component declares exactly one replica.",),
        benefits=("The component no longer runs as a single instance.",),
        tradeoffs=("One more instance runs and is billed.",),
        unsupported=(
            "Whether the loss of one replica is tolerated: that depends on declared failover and "
            "failure independence, evaluated by the Reliability Engine on the candidate.",
            "Placing replicas in different zones or regions.",
        ),
        validation=("The replica count respects the component's declared bounds.",),
        analyses=(S.RELIABILITY, S.COST, S.CAPACITY),
    )

    def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Outcome, ...]:
        node = context.ir.node(trigger.element_id)
        if node is None or not _applies("replicas", node.kind):
            return (_not_proposed(trigger, f"{trigger.element_id} has no replicas to add."),)
        declared = _declared(node.configuration.get("replicas"))
        if declared is None:
            return (
                _not_proposed(
                    trigger,
                    f"{node.id} does not declare its replicas: whether it runs a single instance is unknown.",
                    (f"{node.id}.configuration.replicas",),
                ),
            )
        if declared != 1:
            return (
                _not_proposed(
                    trigger,
                    f"{node.id} already declares {declared} replicas: the finding rests on something a "
                    "replica count does not change (placement, failover or independence).",
                ),
            )
        undeclared = tuple(
            f"{node.id}.configuration.{p}"
            for p in ("failover_mode", "failure_independence")
            if node.configuration.get(p) is None
        )
        return (
            _candidate(
                self.meta,
                trigger,
                context,
                (ConfigurationChange(node.id, "replicas", 2),),
                title=f"Run {node.id} with a second replica",
                description=f"Raise {node.id} from 1 to 2 replicas, so it no longer runs as a single "
                "instance.",
                rationale=f"The Reliability Engine reports {node.id} as a single instance ({trigger.code}); "
                "one more replica is the smallest change that removes that.",
                benefits=(
                    Effect(
                        "reliability",
                        "no_single_instance",
                        f"{node.id} no longer runs as a single instance.",
                        B.RULE,
                    ),
                ),
                tradeoffs=(
                    Effect("cost", "more_instances", "One more instance runs and is billed.", B.RULE),
                ),
                complexity=(
                    Effect(
                        "operations",
                        "replicated_state",
                        "State held by the component must be replicated or shared between replicas.",
                        B.CONSIDERATION,
                    ),
                ),
                risks=(
                    Effect(
                        "reliability",
                        "shared_failure_domain",
                        "Two replicas in one failure domain (zone, host) can still fail together.",
                        B.CONSIDERATION,
                    ),
                ),
                missing=undeclared,
            ),
        )


# --- security -------------------------------------------------------------------------------------


class RequireTls:
    meta = RuleMeta(
        id="require-tls",
        version=1,
        name="Encrypt a sensitive flow in transit",
        description="Where the Security Engine reports a connection carrying sensitive data with tls "
        "false, propose tls on that connection.",
        category=C.SECURITY_CONTROL,
        triggers=((S.SECURITY, "unencrypted_data_in_transit"),),
        goals=FINDING_GOALS,
        evidence=(S.SECURITY,),
        properties=("tls",),
        conditions=("A connection declaring tls false that carries data declared sensitive.",),
        preconditions=("The connection exists and declares tls false.",),
        benefits=("The finding's condition (tls false on a sensitive flow) no longer holds.",),
        tradeoffs=("Certificates must be issued, rotated and trusted by both ends.",),
        unsupported=("Which protocol version, cipher or certificate authority (not modeled).",),
        validation=("tls is a property of connections.",),
        analyses=(S.SECURITY,),
    )

    def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Outcome, ...]:
        connection = context.ir.connection(trigger.element_id)
        if connection is None or connection.configuration.get("tls") is not False:
            return (_not_proposed(trigger, f"{trigger.element_id} is not a connection declaring tls false."),)
        return (
            _candidate(
                self.meta,
                trigger,
                context,
                (ConfigurationChange(connection.id, "tls", True),),
                title=f"Encrypt {connection.id} in transit",
                description=f"Declare tls on {connection.id} "
                f"({connection.source_id} → {connection.target_id}).",
                rationale=f"The Security Engine reports {connection.id} carries sensitive data with tls "
                "false.",
                benefits=(
                    Effect(
                        "security",
                        "encrypted_in_transit",
                        f"{connection.id} no longer carries sensitive data unencrypted in the model.",
                        B.RULE,
                    ),
                ),
                tradeoffs=(
                    Effect(
                        "operations",
                        "certificates",
                        "Certificates must be issued, rotated and trusted by both ends.",
                        B.CONSIDERATION,
                    ),
                ),
                migration=(
                    Effect(
                        "migration",
                        "both_ends",
                        "Both ends must switch together, or accept both during the change.",
                        B.CONSIDERATION,
                    ),
                ),
            ),
        )


class EncryptAtRest:
    meta = RuleMeta(
        id="encrypt-at-rest",
        version=1,
        name="Encrypt a sensitive store at rest",
        description="Where the Security Engine reports a store holding sensitive data with "
        "encryption_at_rest false, propose encryption at rest.",
        category=C.SECURITY_CONTROL,
        triggers=((S.SECURITY, "unencrypted_data_at_rest"),),
        goals=FINDING_GOALS,
        evidence=(S.SECURITY,),
        properties=("encryption_at_rest",),
        conditions=("A store declaring encryption_at_rest false that holds data declared sensitive.",),
        preconditions=("The store exists and declares encryption_at_rest false.",),
        benefits=("The finding's condition no longer holds.",),
        tradeoffs=("Keys must be managed, and existing data re-encrypted.",),
        unsupported=("Key management and algorithms (not modeled).",),
        validation=("encryption_at_rest is a property of stores.",),
        analyses=(S.SECURITY,),
    )

    def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Outcome, ...]:
        node = context.ir.node(trigger.element_id)
        if node is None or node.configuration.get("encryption_at_rest") is not False:
            return (
                _not_proposed(
                    trigger, f"{trigger.element_id} is not a store declaring encryption_at_rest false."
                ),
            )
        return (
            _candidate(
                self.meta,
                trigger,
                context,
                (ConfigurationChange(node.id, "encryption_at_rest", True),),
                title=f"Encrypt {node.id} at rest",
                description=f"Declare encryption at rest for {node.id}.",
                rationale=f"The Security Engine reports {node.id} holds sensitive data unencrypted at rest.",
                benefits=(
                    Effect(
                        "security",
                        "encrypted_at_rest",
                        f"{node.id} no longer holds sensitive data unencrypted in the model.",
                        B.RULE,
                    ),
                ),
                tradeoffs=(
                    Effect(
                        "operations", "key_management", "Encryption keys must be managed.", B.CONSIDERATION
                    ),
                ),
                migration=(
                    Effect(
                        "migration",
                        "reencrypt",
                        "Existing data, backups and snapshots must be re-encrypted.",
                        B.CONSIDERATION,
                    ),
                ),
            ),
        )


# --- observability --------------------------------------------------------------------------------


class EnableSignal:
    meta = RuleMeta(
        id="enable-signal",
        version=1,
        name="Emit a signal a component declares absent",
        description="Where the Observability Engine reports a component declaring logs, traces or a "
        "health check absent where they matter, propose emitting it.",
        category=C.INSTRUMENTATION,
        triggers=tuple((S.OBSERVABILITY, code) for code in SIGNALS),
        goals=(*FINDING_GOALS, G.OBSERVABILITY_COVERAGE),
        evidence=(S.OBSERVABILITY,),
        properties=tuple(SIGNALS.values()),
        conditions=("A component declaring the signal false where coverage is required.",),
        preconditions=("The component exists and declares the signal false.",),
        benefits=("The component emits the signal in the model.",),
        tradeoffs=("Telemetry volume and its storage grow.",),
        unsupported=(
            "Metric and alert kinds (which to emit is not stated): reported, not proposed.",
            "Whether the telemetry is collected: a path to an observability component is needed.",
        ),
        validation=("The signal is a property of the component's kind.",),
        analyses=(S.OBSERVABILITY,),
    )

    def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Outcome, ...]:
        prop = SIGNALS[trigger.code]
        node = context.ir.node(trigger.element_id)
        if node is None or node.configuration.get(prop) is not False:
            return (_not_proposed(trigger, f"{trigger.element_id} does not declare {prop} false."),)
        name = SIGNAL_NAMES[prop]
        return (
            _candidate(
                self.meta,
                trigger,
                context,
                (ConfigurationChange(node.id, prop, True),),
                title=f"Emit {name} from {node.id}",
                description=f"Declare {prop} for {node.id}.",
                rationale=f"The Observability Engine reports {node.id} declares {name} absent "
                f"({trigger.code}).",
                benefits=(
                    Effect(
                        "observability",
                        f"{prop}_modeled",
                        f"{node.id} emits {name} in the model.",
                        B.RULE,
                    ),
                ),
                tradeoffs=(
                    Effect(
                        "cost", "telemetry_volume", "Telemetry volume and its storage grow.", B.CONSIDERATION
                    ),
                ),
                risks=(
                    Effect(
                        "observability",
                        "collection_path",
                        "Emitted telemetry is collected only if a connection to an observability "
                        "component declares it.",
                        B.CONSIDERATION,
                        (Evidence("signal", prop),),
                    ),
                ),
            ),
        )


RULES = (ScaleReplicas(), ScaleCpu(), AddReplica(), RequireTls(), EncryptAtRest(), EnableSignal())


def default_registry() -> Registry:
    return Registry(RULES)
