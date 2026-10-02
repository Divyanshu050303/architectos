"""Migration risks, identified from the plan's own evidence — its changes, steps, data migrations,
compatibility questions, dependency findings and the request's stated constraints — never from a
probability or a score.

Each risk is ``confirmed`` when the evidence establishes the condition (a step requires downtime, a
decommissioning removes data nothing retains, a security setting is switched off), ``potential``
when it holds under a stated precondition (writes accepted after a copy begins, clients switched
back after the replacement accepted writes), and ``unknown`` when the evidence needed is missing (a
compatibility question nobody has answered, a step whose reversibility is not established). Each
names what it affects, its potential impact and a mitigation or review action.
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator

from core.domain.migrations.plans import PlanFinding
from core.domain.migrations.steps import CompatibilityCheck, DataMigration, MigrationStep, Risk, Trace
from core.domain.migrations.values import (
    CompatibilityStatus,
    DowntimeStatus,
    FindingType,
    Reversibility,
    RiskCategory,
    RiskStatus,
    StepType,
    TraceKind,
)

from .changes import OBSERVABILITY, SECURITY
from .data_migration import instances
from .patternbook import BLUE_GREEN_ID, ROLLING_ID
from .patterns import PlanningContext

C, S = RiskCategory, RiskStatus
PLANNER_TRACE = Trace(TraceKind.PATTERN, "migration_planner@1", "The plan's dependency analysis.")
ORDERING = frozenset(
    {FindingType.INVALID_DEPENDENCY, FindingType.DEPENDENCY_CYCLE, FindingType.MISSING_PREREQUISITE}
)
DOWN = frozenset({DowntimeStatus.KNOWN_DOWNTIME, DowntimeStatus.POTENTIAL_DOWNTIME})


def _patterns(step: MigrationStep) -> set[str]:
    return {t.reference.split("@")[0] for t in step.traces if t.kind is TraceKind.PATTERN}


class _Risks:
    def __init__(self, context: PlanningContext, steps: tuple[MigrationStep, ...]) -> None:
        self.context = context
        self.steps = {s.key: s for s in sorted(steps, key=lambda s: s.key)}
        self.constraints = context.request.constraints

    def removal(self, move: DataMigration) -> Risk:
        old = move.source_element_id or ""
        stated = move.scope is not None
        return Risk(
            f"data_loss:{old}",
            C.DATA_LOSS,
            S.POTENTIAL if stated else S.CONFIRMED,
            move.data_loss or f"Decommissioning {old} removes its data.",
            f"{old}'s data is lost.",
            move.traces,
            (old,),
            move.step_ids,
            preconditions=(f"The stated retention of {old}'s data is not carried out first.",)
            if stated
            else (),
            mitigation=f"State and carry out what of {old}'s data is retained before it is decommissioned.",
        )

    def data(self, moves: Iterable[DataMigration]) -> Iterator[Risk]:
        for move in moves:
            old, new = move.source_element_id or "", move.destination_element_id
            if new is None:
                yield self.removal(move)
                continue
            src, dst = instances(old, new)
            if f"replicate:{old}" in self.steps:
                target = self.context.node(self.context.target, new)
                mode = target.configuration.values.get("replication_mode") if target else None
                when = (
                    "Synchronous replication does not hold until the cutover."
                    if mode == "synchronous"
                    else f"Changes {src} accepts are not yet replicated at the cutover."
                )
            else:
                when = f"Writes reach {src} after the copy to {dst} begins."
            yield Risk(
                f"data_loss:{old}",
                C.DATA_LOSS,
                S.POTENTIAL,
                move.data_loss or when,
                f"Data {src} accepted would be missing from {dst}.",
                move.traces,
                (old, new),
                move.step_ids,
                preconditions=(when,),
                mitigation=f"Verify {dst}'s consistency with {src} before the cutover (consistency:{old}).",
            )
            cutover = self.steps.get(f"cutover:{old}")
            yield Risk(
                f"divergence:{old}",
                C.DATA_INCONSISTENCY,
                S.POTENTIAL,
                f"Switching {src}'s clients back after {dst} accepted writes makes {src} and {dst} diverge.",
                f"Writes made to {dst} after the cutover are missing from {src}.",
                move.traces,
                (old, new),
                (cutover.id,) if cutover else (),
                preconditions=(f"Clients are switched back to {src} after {dst} has accepted writes.",),
                mitigation=f"Reconcile, or knowingly discard, {dst}'s writes before switching back to {src}.",
            )

    def downtime(self) -> Iterator[Risk]:
        allowed, window = self.constraints.downtime_allowed, self.constraints.maintenance_window
        if allowed is True and window:
            mitigation = f"Carry it out within the stated maintenance window ({window})."
        elif allowed is True:
            mitigation = "State the maintenance window it is carried out in."
        else:
            mitigation = (
                "State whether downtime is allowed, or choose a strategy that keeps this step online."
            )
        for step in self.steps.values():
            if step.downtime not in DOWN:
                continue
            known = step.downtime is DowntimeStatus.KNOWN_DOWNTIME
            yield Risk(
                f"downtime:{step.key}",
                C.DOWNTIME,
                S.CONFIRMED if known else S.POTENTIAL,
                f"{step.key} {'requires' if known else 'may require'} downtime.",
                f"{', '.join(step.element_ids)} out of service or refusing writes during the step; how long "
                "is not modeled.",
                step.traces,
                step.element_ids,
                (step.id,),
                preconditions=() if known else (step.downtime_note or "Its conditions are not stated.",),
                mitigation=mitigation,
            )

    def reversibility(self) -> Iterator[Risk]:
        for step in self.steps.values():
            if step.reversibility is Reversibility.IRREVERSIBLE:
                yield Risk(
                    f"irreversible:{step.key}",
                    C.IRREVERSIBLE_CHANGE,
                    S.CONFIRMED,
                    f"{step.key} cannot be reversed.",
                    "What it removes cannot be restored by this plan.",
                    step.traces,
                    step.element_ids,
                    (step.id,),
                    mitigation=(
                        "Confirm before it that everything to keep is retained and nothing depends on it."
                    ),
                )
            elif step.reversibility is Reversibility.UNKNOWN:
                yield Risk(
                    f"rollback:{step.key}",
                    C.ROLLBACK_LIMITATION,
                    S.UNKNOWN,
                    f"Whether {step.key} can be reversed is not established.",
                    "Recovering from a failure at this step may not be possible.",
                    step.traces,
                    step.element_ids,
                    (step.id,),
                    mitigation="A person assesses how this step would be recovered from before approval.",
                )

    def strategy(self) -> Iterator[Risk]:
        for step in self.steps.values():
            patterns, element = _patterns(step), ", ".join(step.element_ids)
            common = (step.traces, step.element_ids, (step.id,))
            if step.type is StepType.CONFIGURE and ROLLING_ID in patterns:
                yield Risk(
                    f"capacity:{step.key}",
                    C.CAPACITY_EXHAUSTION,
                    S.POTENTIAL,
                    f"{element} runs one instance short while the roll-out replaces it.",
                    f"{element} is overloaded during the roll-out.",
                    *common,
                    preconditions=(f"The remaining instances of {element} cannot carry its load.",),
                    mitigation="Attach a capacity analysis of the source with one instance fewer.",
                )
            elif step.type in {StepType.REPLICATE, StepType.BACKFILL} and step.downtime not in DOWN:
                yield Risk(
                    f"capacity:{step.key}",
                    C.CAPACITY_EXHAUSTION,
                    S.POTENTIAL,
                    f"{step.key} adds load to the source while it serves its clients.",
                    "The source slows down or fails while its data is copied.",
                    *common,
                    preconditions=("The source has no headroom for the copy (not modeled).",),
                    mitigation="Review the source's capacity for the copy before it starts.",
                )
            elif step.type is StepType.PROVISION and BLUE_GREEN_ID in patterns:
                yield Risk(
                    f"cost:{step.key}",
                    C.COST_INCREASE,
                    S.POTENTIAL,
                    f"Two environments of {element} run until the previous one is retired.",
                    "Operating cost rises while both run; by how much is not modeled.",
                    *common,
                    preconditions=("Both environments are billed while they run.",),
                    mitigation="Attach a cost analysis of the target with the second environment.",
                )

    @staticmethod
    def compatibility(checks: Iterable[CompatibilityCheck]) -> Iterator[Risk]:
        grouped: dict[str, list[CompatibilityCheck]] = defaultdict(list)
        for check in checks:
            if check.status is not CompatibilityStatus.VERIFIED:
                grouped[check.element_ids[0] if check.element_ids else check.key].append(check)
        for element, found in sorted(grouped.items()):
            incompatible = any(c.status is CompatibilityStatus.INCOMPATIBLE for c in found)
            traces = sorted({t for c in found for t in c.traces}, key=lambda t: (t.kind.value, t.reference))
            yield Risk(
                f"compatibility:{element}",
                C.COMPATIBILITY_FAILURE,
                S.CONFIRMED if incompatible else S.UNKNOWN,
                f"{len(found)} compatibility question(s) about {element} are "
                f"{'answered incompatible' if incompatible else 'not answered'}.",
                f"Clients or data of {element} may not work after the migration.",
                tuple(traces),
                tuple({e for c in found for e in c.element_ids}),
                mitigation="Answer each question (" + ", ".join(c.key for c in found) + ") with evidence.",
            )

    def settings(self) -> Iterator[Risk]:
        """A security or observability setting switched off, or no longer declared."""
        groups = (
            (SECURITY, C.SECURITY_REGRESSION, "security"),
            (OBSERVABILITY, C.OBSERVABILITY_GAP, "observability"),
        )
        for change in sorted(self.context.plannable(), key=lambda c: c.ref):
            for field in change.changed("configuration"):
                name = field.field.removeprefix("configuration.")
                if field.before is not True or field.after is True:
                    continue
                off = field.after is False
                for group, category, word in groups:
                    if name in group:
                        yield Risk(
                            f"{word}:{change.element_id}:{name}",
                            category,
                            S.CONFIRMED if off else S.UNKNOWN,
                            f"{name} of {change.element_id} is "
                            f"{'switched off' if off else 'no longer declared'} in the target.",
                            f"The {word} control {name} no longer holds for {change.element_id}.",
                            (Trace(TraceKind.CHANGE, change.ref, change.label),),
                            (change.element_id,),
                            mitigation=f"Confirm the change of {name} is intended, or keep it in the target.",
                        )

    @staticmethod
    def ordering(findings: Iterable[PlanFinding]) -> Iterator[Risk]:
        for finding in findings:
            if finding.type in ORDERING:
                yield Risk(
                    f"ordering:{finding.type.value}:{finding.key}"[:128],
                    C.DEPENDENCY_ORDERING,
                    S.CONFIRMED,
                    finding.message,
                    "Steps may be carried out before what they depend on.",
                    finding.traces or (PLANNER_TRACE,),
                    finding.element_ids,
                    finding.step_ids,
                    mitigation="; ".join(finding.missing) or "Correct the dependencies.",
                )


def risks(
    context: PlanningContext,
    steps: tuple[MigrationStep, ...],
    data: Iterable[DataMigration],
    compatibility: Iterable[CompatibilityCheck],
    findings: Iterable[PlanFinding],
) -> tuple[Risk, ...]:
    """The plan's risks, each from its evidence — never scored."""
    found = _Risks(context, steps)
    every = [
        *found.data(data),
        *found.downtime(),
        *found.reversibility(),
        *found.strategy(),
        *found.compatibility(compatibility),
        *found.settings(),
        *found.ordering(findings),
    ]
    return tuple(sorted(every, key=lambda r: r.key))
