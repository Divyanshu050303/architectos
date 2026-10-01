"""Deterministic step generation: the classified changes, the chosen strategy and the templates turned
into structured, traceable migration steps — proposals in words, never commands, never a record
that anything happened.

For every plannable change, the chosen strategy's steps are used when it covers the element (a
rolling roll-out, a blue-green switch, a replication then cutover), the templates' otherwise (the
in-place approach): reconfigure, provision, decommission after verification, reroute. A stateful
replacement not covered by replication is carried out offline, with writes stopped — stated as
known downtime, never hidden.

Every step names the changes it addresses and the pattern that produced it; its id is derived from
its key (``configure:api``, ``cutover:db``), so identical inputs give identical steps. Dependencies
are explicit: a connection after its new endpoints, a cutover after the verification it needs,
decommissioning after the traffic and data have moved away, connections before their components.
Each step's downtime, traffic and availability are then stated against the request's constraints
(``availability``), the data each stateful change moves is described (``data_migration``), the
compatibility questions are raised (``compatibility``), the dependencies are checked and the steps
sequenced (``sequencing``): what is missing is a finding on the plan, never repaired silently.
Finally the risks are identified from all of it (``risk``), the high-impact steps' rollback
considered (``rollback``) and the verification checkpoints stated (``checkpoints``).
Changes needing manual interpretation or no pattern supports are findings, never invented steps.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from core.architecture_ir.diff import ChangeKind
from core.domain.migrations.changes import Aspect, MigrationChange
from core.domain.migrations.errors import InvalidMigrationRequest
from core.domain.migrations.plans import MAX_FINDINGS, MAX_STEPS, MigrationProposal, StrategyOption
from core.domain.migrations.steps import MigrationStep, Trace, step_id
from core.domain.migrations.values import DowntimeStatus, Reversibility, StepType, TraceKind

from .availability import availability
from .checkpoints import checkpoints
from .compatibility import compatibility
from .data_migration import data_migrations
from .patternbook import BLUE_GREEN_ID, REPLICATION_ID, ROLLING_ID, stateful_replacements
from .patterns import PatternMeta, PlanningContext, Registry, choose, strategy_options
from .risk import risks
from .rollback import rollbacks
from .sequencing import sequence

PLANNER = ("migration_planner", 1)
T, D, V = StepType, DowntimeStatus, Reversibility


@dataclass
class _Draft:
    key: str
    type: StepType
    title: str
    outcome: str
    completion: tuple[str, ...]
    traces: tuple[Trace, ...]
    after: list[str] = field(default_factory=list)  # step keys
    fields: dict[str, Any] = field(default_factory=dict)


class _Steps:
    """The steps of one plan, by key; dependencies are keys until the plan is built."""

    def __init__(self) -> None:
        self.drafts: dict[str, _Draft] = {}

    def add(
        self,
        key: str,
        type_: StepType,
        title: str,
        outcome: str,
        completion: Iterable[str],
        traces: Iterable[Trace],
        *,
        after: Iterable[str] = (),
        **fields: Any,
    ) -> None:
        self.drafts[key] = _Draft(
            key, type_, title, outcome, tuple(completion), tuple(traces), list(after), fields
        )

    def depend(self, key: str, *on: str) -> None:
        """``key`` waits for every step of ``on`` that exists."""
        if key in self.drafts:
            self.drafts[key].after.extend(k for k in on if k in self.drafts and k != key)

    def build(self) -> tuple[MigrationStep, ...]:
        return tuple(
            MigrationStep(
                key=d.key,
                type=d.type,
                title=d.title,
                outcome=d.outcome,
                traces=d.traces,
                completion=d.completion,
                depends_on=tuple(step_id(k) for k in sorted(set(d.after))),
                **d.fields,
            )
            for d in self.drafts.values()
        )


@dataclass(frozen=True)
class _Plan:
    context: PlanningContext
    registry: Registry
    strategy: StrategyOption | None

    def meta(self, pattern_id: str) -> PatternMeta:
        pattern = self.registry.get(pattern_id)
        if pattern is None:  # a programming error: the book ships every pattern used here
            raise LookupError(pattern_id)
        return pattern.meta

    def covered(self, pattern_id: str, element_id: str) -> bool:
        """Whether the chosen strategy is ``pattern_id`` and covers the element."""
        strategy = self.strategy
        if strategy is None or strategy.pattern.split("@")[0] != pattern_id:
            return False
        return element_id in strategy.subjects


class _Generator:
    def __init__(self, plan: _Plan) -> None:
        self.plan = plan
        self.steps = _Steps()
        self.context = plan.context
        self.changes = {c.ref: c for c in plan.context.plannable()}
        self.pairs = stateful_replacements(plan.context)
        self.replaced = {old for old, _ in self.pairs} | {new for _, new in self.pairs}

    def traces(self, pattern_id: str, *changes: MigrationChange) -> tuple[Trace, ...]:
        found = tuple(Trace(TraceKind.CHANGE, c.ref, c.label) for c in changes)
        return (*found, self.plan.meta(pattern_id).trace())

    def change(self, element_id: str, kind: ChangeKind) -> MigrationChange | None:
        return self.changes.get(f"node:{element_id}:{kind.value}")

    # --- nodes and connections added ------------------------------------------------------------

    def provision(self, change: MigrationChange) -> None:
        eid, traces = change.element_id, self.traces("provision", change)
        if change.element == "connection":
            conn = next(c for c in self.context.target.connections if c.id == eid)
            self.steps.add(
                f"provision:{eid}",
                T.PROVISION,
                f"Establish the connection {eid}",
                f"{conn.source_id} reaches {conn.target_id} as the target describes.",
                (f"{eid} is configured as in the target revision.",),
                traces,
                element_ids=(eid, conn.source_id, conn.target_id),
                preconditions=(f"{conn.source_id} and {conn.target_id} exist and are verified.",),
                reversibility=V.REVERSIBLE,
            )
            self.steps.add(
                f"verify:{eid}",
                T.VERIFY,
                f"Verify the connection {eid}",
                f"{conn.source_id} communicates with {conn.target_id} as intended.",
                (f"Requests over {eid} succeed with the target configuration.",),
                traces,
                after=(f"provision:{eid}",),
                element_ids=(eid,),
                downtime=D.MODELED_ONLINE,
                reversibility=V.REVERSIBLE,
            )
            return
        label = change.label or eid
        self.steps.add(
            f"provision:{eid}",
            T.PROVISION,
            f"Provision {label}",
            f"{eid} exists with the target revision's technology and resources.",
            (f"{eid} is running and reachable by nothing yet.",),
            traces,
            element_ids=(eid,),
            preconditions=("The target revision validates.",),
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
        )
        self.steps.add(
            f"configure:{eid}",
            T.CONFIGURE,
            f"Configure {label}",
            f"{eid} has the target revision's configuration.",
            (f"Every setting of {eid} in the target revision is in effect.",),
            traces,
            after=(f"provision:{eid}",),
            element_ids=(eid,),
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
        )
        self.steps.add(
            f"verify:{eid}",
            T.VERIFY,
            f"Verify {label} before it is used",
            f"{eid} is known to work before any traffic or data is routed to it.",
            (f"{eid} passes its verification before anything depends on it.",),
            traces,
            after=(f"configure:{eid}",),
            element_ids=(eid,),
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
        )

    # --- nodes and connections modified ---------------------------------------------------------

    def reconfigure(self, change: MigrationChange) -> None:
        eid, label = change.element_id, change.label or change.element_id
        if self.plan.covered(BLUE_GREEN_ID, eid):
            self.blue_green(change)
            return
        if self.plan.covered(ROLLING_ID, eid):
            self.steps.add(
                f"configure:{eid}",
                T.CONFIGURE,
                f"Roll out {label}'s target configuration one instance at a time",
                f"Every instance of {eid} runs the target configuration.",
                (f"All instances of {eid} run the target configuration and pass their health check.",),
                self.traces(ROLLING_ID, change),
                element_ids=(eid,),
                preconditions=(
                    f"At least one other instance of {eid} serves while one is replaced.",
                    "Old and new versions are compatible while both run.",
                ),
                downtime=D.MODELED_ONLINE,
                downtime_note="Online while one instance at a time is replaced, as declared.",
                reversibility=V.REVERSIBLE,
            )
        else:
            settings = tuple(
                f"{f.field} is {f.after}." for f in change.fields if f.field.startswith("configuration.")
            )
            self.steps.add(
                f"configure:{eid}",
                T.CONFIGURE,
                f"Apply {label}'s target configuration",
                f"{eid} runs with the target revision's configuration.",
                settings or (f"{eid} matches the target revision.",),
                self.traces("reconfigure", change),
                element_ids=(eid,),
                preconditions=("The target revision validates.",),
                reversibility=V.CONDITIONALLY_REVERSIBLE,
            )
        self.steps.add(
            f"verify:{eid}",
            T.VERIFY,
            f"Verify {label} with its target configuration",
            f"{eid} behaves as intended with the target configuration.",
            (f"{eid} passes its verification with the target configuration.",),
            self.traces("reconfigure", change),
            after=(f"configure:{eid}",),
            element_ids=(eid,),
            reversibility=V.REVERSIBLE,
        )

    def blue_green(self, change: MigrationChange) -> None:
        eid, label = change.element_id, change.label or change.element_id
        traces = self.traces(BLUE_GREEN_ID, change)
        routers = tuple(n.id for n in self.context.incoming(self.context.target, eid))
        self.steps.add(
            f"provision:{eid}:green",
            T.PROVISION,
            f"Provision {label}'s target environment beside the current one",
            f"A second {eid} environment runs the target configuration, receiving no traffic.",
            (f"The new {eid} environment runs the target configuration.",),
            traces,
            element_ids=(eid,),
            inputs=(f"Capacity for a second {eid} environment (not modeled).",),
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
        )
        self.steps.add(
            f"verify:{eid}:green",
            T.VERIFY,
            f"Verify {label}'s target environment before switching",
            f"The new {eid} environment is known to work.",
            ("It passes its verification without traffic.",),
            traces,
            after=(f"provision:{eid}:green",),
            element_ids=(eid,),
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
        )
        self.steps.add(
            f"cutover:{eid}",
            T.CUTOVER,
            f"Switch traffic to {label}'s target environment",
            f"{', '.join(routers)} route {eid}'s traffic to the new environment.",
            ("Traffic reaches only the new environment.",),
            traces,
            after=(f"verify:{eid}:green",),
            element_ids=(eid, *routers),
            preconditions=("The current environment is retained.",),
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
            manual_verification=True,
        )
        self.steps.add(
            f"verify:{eid}",
            T.VERIFY,
            f"Verify {label} under traffic",
            f"{eid} behaves as intended under traffic.",
            (f"{eid} passes its verification under traffic.",),
            traces,
            after=(f"cutover:{eid}",),
            element_ids=(eid,),
            reversibility=V.REVERSIBLE,
        )
        self.steps.add(
            f"decommission:{eid}:blue",
            T.DECOMMISSION,
            f"Retire {label}'s previous environment",
            f"Only the target environment of {eid} runs.",
            ("The previous environment is removed.",),
            traces,
            after=(f"verify:{eid}",),
            element_ids=(eid,),
            preconditions=(
                "The target environment is verified under traffic; switching back is no longer needed.",
            ),
            downtime=D.MODELED_ONLINE,
            reversibility=V.CONDITIONALLY_REVERSIBLE,
        )

    def reroute(self, change: MigrationChange) -> None:
        eid, traces = change.element_id, self.traces("reroute", change)
        conn = next(c for c in self.context.target.connections if c.id == eid)
        self.steps.add(
            f"prepare:{eid}",
            T.PREPARE,
            f"Prepare to reroute {eid}",
            f"Everything {eid}'s new route needs is in place.",
            (f"{conn.target_id} is ready to receive {eid}.",),
            traces,
            element_ids=(eid,),
            reversibility=V.REVERSIBLE,
        )
        self.steps.add(
            f"cutover:{eid}",
            T.CUTOVER,
            f"Reroute {eid}",
            f"{conn.source_id} reaches {conn.target_id} over {eid} as the target describes.",
            (f"Requests over {eid} follow the target route.",),
            traces,
            after=(f"prepare:{eid}",),
            element_ids=(eid, conn.source_id, conn.target_id),
            reversibility=V.CONDITIONALLY_REVERSIBLE,
            manual_verification=True,
        )
        self.steps.add(
            f"verify:{eid}",
            T.VERIFY,
            f"Verify {eid} on its new route",
            f"{eid} works on its target route.",
            (f"Requests over {eid} succeed on the target route.",),
            traces,
            after=(f"cutover:{eid}",),
            element_ids=(eid,),
            reversibility=V.REVERSIBLE,
        )
        self.steps.depend(f"prepare:{eid}", f"verify:{conn.target_id}", f"verify:{conn.source_id}")

    # --- stateful replacements ------------------------------------------------------------------

    def replace(self, old: str, new: str) -> None:
        """Move a stateful component's data to its replacement: by replication when the chosen
        strategy covers it, offline with writes stopped otherwise."""
        changes = sorted(
            (c for c in self.changes.values() if c.element == "node" and c.element_id in {old, new}),
            key=lambda c: c.ref,
        )
        replicated = self.plan.covered(REPLICATION_ID, new)
        traces = self.traces(REPLICATION_ID if replicated else "in_place", *changes)
        same = old == new
        ready = f"provision:{new}:target" if same else f"verify:{new}"
        if same:  # the same component, another technology: its replacement is provisioned beside it
            self.steps.add(
                f"provision:{new}:target",
                T.PROVISION,
                f"Provision {new}'s target technology beside the source",
                f"A {new} instance with the target technology runs, holding no data yet.",
                ("It is running, empty, and reachable by nothing yet.",),
                traces,
                element_ids=(new,),
                downtime=D.MODELED_ONLINE,
                reversibility=V.REVERSIBLE,
            )
        scope = f"The data of {old} to move (its scope, as stated in the request's data requirements)."
        if replicated:
            self._replicate(old, new, ready, scope, traces)
            downtime, note = (
                D.POTENTIAL_DOWNTIME,
                "Writes pause for the cutover itself; how long is not modeled.",
            )
        else:
            self._copy_offline(old, new, ready, scope, traces)
            downtime, note = D.KNOWN_DOWNTIME, f"Writes to {old} remain stopped until the cutover completes."
        self._cut_over(old, new, same, downtime, note, traces)

    def _replicate(self, old: str, new: str, ready: str, scope: str, traces: tuple[Trace, ...]) -> None:
        self.steps.add(
            f"replicate:{old}",
            T.REPLICATE,
            f"Replicate {old}'s data to {new}",
            f"Changes to {old} are replicated to {new}.",
            (f"Replication from {old} to {new} is running.",),
            traces,
            after=(ready,),
            element_ids=(old, new),
            inputs=("The replication method and its configuration.",),
            data_impact=f"{new} receives {old}'s changes.",
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
        )
        self.steps.add(
            f"backfill:{old}",
            T.BACKFILL,
            f"Copy {old}'s existing data to {new}",
            f"{new} holds {old}'s existing data.",
            (f"The initial copy of {old} is complete.",),
            traces,
            after=(f"replicate:{old}",),
            element_ids=(old, new),
            inputs=(scope,),
            data_impact=f"{old}'s existing data is copied to {new}.",
            downtime=D.MODELED_ONLINE,
            reversibility=V.REVERSIBLE,
        )

    def _copy_offline(self, old: str, new: str, ready: str, scope: str, traces: tuple[Trace, ...]) -> None:
        self.steps.add(
            f"prepare:{old}:freeze",
            T.PREPARE,
            f"Stop writes to {old}",
            f"No write reaches {old} while its data is copied.",
            (f"Writes to {old} are stopped.",),
            traces,
            after=(ready,),
            element_ids=(old,),
            downtime=D.KNOWN_DOWNTIME,
            downtime_note=f"Writes to {old} stop until the cutover; how long is not modeled.",
            reversibility=V.REVERSIBLE,
            manual_verification=True,
        )
        self.steps.add(
            f"backfill:{old}",
            T.BACKFILL,
            f"Copy {old}'s data to {new}",
            f"{new} holds all of {old}'s data.",
            (f"The copy of {old} to {new} is complete.",),
            traces,
            after=(f"prepare:{old}:freeze",),
            element_ids=(old, new),
            inputs=(scope,),
            data_impact=f"{old}'s data is copied to {new} offline.",
            downtime=D.KNOWN_DOWNTIME,
            downtime_note=f"Writes to {old} remain stopped.",
            reversibility=V.REVERSIBLE,
            manual_verification=True,
        )

    def _cut_over(
        self, old: str, new: str, same: bool, downtime: DowntimeStatus, note: str, traces: tuple[Trace, ...]
    ) -> None:
        self.steps.add(
            f"verify:{old}:consistency",
            T.VERIFY,
            f"Verify {new} holds {old}'s data",
            f"{new}'s data is consistent with {old}'s.",
            ("A consistency check of the copied data passes.",),
            traces,
            after=(f"backfill:{old}",),
            element_ids=(old, new),
            inputs=("How consistency is checked (not modeled).",),
            reversibility=V.REVERSIBLE,
            manual_verification=True,
        )
        self.steps.add(
            f"cutover:{old}",
            T.CUTOVER,
            f"Switch {old}'s clients to {new}",
            f"The clients of {old} read and write {new}.",
            (f"No client uses {old}.",),
            traces,
            after=(f"verify:{old}:consistency",),
            element_ids=(old, new),
            preconditions=(f"{old} is retained until {new} is verified under traffic.",),
            downtime=downtime,
            downtime_note=note,
            reversibility=V.CONDITIONALLY_REVERSIBLE,
            manual_verification=True,
        )
        self.steps.add(
            f"verify:{new}:traffic",
            T.VERIFY,
            f"Verify {new} under traffic",
            f"{new} serves its clients as intended.",
            (f"{new} passes its verification under traffic.",),
            traces,
            after=(f"cutover:{old}",),
            element_ids=(new,),
            reversibility=V.REVERSIBLE,
            manual_verification=True,
        )
        if same:  # the source technology's instance is retired once the target is verified
            self.steps.add(
                f"decommission:{old}:source",
                T.DECOMMISSION,
                f"Retire {old}'s source technology",
                f"Only {new}'s target technology runs.",
                (f"The source instance of {old} is removed.",),
                traces,
                after=(f"verify:{new}:traffic",),
                element_ids=(old,),
                preconditions=(
                    f"{new} is verified under traffic; {old}'s data is retained as the request requires.",
                ),
                data_impact=f"The source copy of {old}'s data is removed unless retained.",
                reversibility=V.IRREVERSIBLE,
                manual_verification=True,
            )

    # --- removed nodes and connections ----------------------------------------------------------

    def decommission(self, change: MigrationChange) -> None:
        eid, traces = change.element_id, self.traces("decommission", change)
        label, stateful = change.label or eid, change.stateful
        self.steps.add(
            f"verify:{eid}:unused",
            T.VERIFY,
            f"Verify nothing depends on {label}",
            f"No remaining element uses {eid}.",
            (f"No traffic or data reaches {eid}.",),
            traces,
            element_ids=(eid,),
            reversibility=V.REVERSIBLE,
            manual_verification=stateful,
        )
        self.steps.add(
            f"decommission:{eid}",
            T.DECOMMISSION,
            f"Decommission {label}",
            f"{eid} no longer exists.",
            (f"{eid} is removed.",),
            traces,
            after=(f"verify:{eid}:unused",),
            element_ids=(eid,),
            preconditions=(f"{eid}'s data is retained or deliberately discarded, as decided.",)
            if stateful
            else (),
            data_impact=f"{eid}'s data is removed unless retained." if stateful else None,
            reversibility=V.IRREVERSIBLE if stateful else V.CONDITIONALLY_REVERSIBLE,
            manual_verification=stateful,
        )

    # --- ordering across elements ---------------------------------------------------------------

    def order(self) -> None:
        source, target = self.context.source, self.context.target
        for conn in target.connections:  # a connection after its new endpoints
            self.steps.depend(f"provision:{conn.id}", f"verify:{conn.source_id}", f"verify:{conn.target_id}")
        for old, new in self.pairs:  # clients switch once the new connections work; old ones retire after
            self.steps.depend(
                f"cutover:{old}", *(f"verify:{c.id}" for c in target.connections if c.target_id == new)
            )
            self.steps.depend(f"verify:{old}:unused", f"cutover:{old}", f"verify:{new}:traffic")
            for conn in source.connections:
                if old in {conn.source_id, conn.target_id}:
                    self.steps.depend(f"verify:{conn.id}:unused", f"cutover:{old}")
        for conn in source.connections:  # connections are retired before their components
            for endpoint in (conn.source_id, conn.target_id):
                self.steps.depend(f"verify:{endpoint}:unused", f"decommission:{conn.id}")
        for change in self.changes.values():  # a rerouted connection leaves its old endpoint first
            for moved in change.changed("source_id") + change.changed("target_id"):
                self.steps.depend(f"verify:{moved.before}:unused", f"verify:{change.element_id}")

    def generate(self) -> tuple[MigrationStep, ...]:
        for change in sorted(self.changes.values(), key=lambda c: c.sort_key):
            if change.element == "node" and change.element_id in self.replaced:
                continue  # carried out with its replacement's data move
            if change.change is ChangeKind.ADDED:
                self.provision(change)
            elif change.change is ChangeKind.REMOVED:
                self.decommission(change)
            elif change.element == "connection" and {Aspect.ROUTING, Aspect.PROTOCOL} & set(change.aspects):
                self.reroute(change)
            else:
                self.reconfigure(change)
        for old, new in self.pairs:
            added = self.change(new, ChangeKind.ADDED)
            if added is not None:
                self.provision(added)
            self.replace(old, new)
            removed = self.change(old, ChangeKind.REMOVED)
            if removed is not None:
                self.decommission(removed)
        self.order()
        return self.steps.build()


def _too_large(limit: int) -> InvalidMigrationRequest:
    """A transition too large to plan as one migration: refused, never truncated."""
    return InvalidMigrationRequest(details={"field": "target", "reason": "too_many_changes", "limit": limit})


def generate(context: PlanningContext, registry: Registry) -> MigrationProposal:
    """The plan's steps for one exact source and target: deterministic for identical inputs. A
    transition needing more steps or findings than one plan holds is refused (split it into
    several migrations), before any further analysis."""
    if len(context.analysis.findings) > MAX_FINDINGS // 2:
        raise _too_large(MAX_FINDINGS // 2)
    options = strategy_options(context, registry)
    chosen, choice_findings = choose(options, context.request.strategy)
    steps = _Generator(_Plan(context, registry, chosen)).generate()
    if len(steps) > MAX_STEPS:
        raise _too_large(MAX_STEPS)
    analysis = context.analysis
    steps, downtime = availability(context, steps)
    data, data_findings = data_migrations(context, steps)
    ordered = sequence(steps, analysis)
    questions = compatibility(context, steps)
    findings = (*analysis.findings, *choice_findings, *downtime, *data_findings, *ordered.findings)
    recovery = rollbacks(steps, data)
    return MigrationProposal(
        source=analysis.source,
        target=analysis.target,
        steps=steps,
        data_migrations=data,
        compatibility=questions,
        risks=risks(context, steps, data, questions, findings),
        checkpoints=checkpoints(context, steps, data, recovery),
        rollbacks=recovery,
        findings=findings,
        assumptions=tuple(a.statement for a in context.request.assumptions),
        strategy=chosen.pattern.split("@")[0] if chosen and steps else None,
        models={**registry.versions(), PLANNER[0]: PLANNER[1]},
        diff_summary=analysis.diff_summary,
        alternatives=options,
        sequence=ordered.sequence,
    )
