"""Verification checkpoints: the conditions to verify before the migration proceeds, each saying what
is verified, the expected condition, its status and its basis — modeled by an engine, verified by a
person, or observable only at runtime — and whether it blocks progression.

At planning time nothing has run: a checkpoint an engine can evaluate is ``not_run`` until a stored
analysis of it is attached (the target's validation, capacity, security and observability); one a
person must confirm (data consistency, cutover and rollback prerequisites, every manually verified
step) is ``manual_verification_required``; one observed only on the running system (health checks)
is ``not_run``; one whose threshold nobody states (replication lag) ``cannot_evaluate``. A
checkpoint is never ``pass`` without current evidence, and a planning-time pass is never proof of
runtime success.
"""

from collections.abc import Iterable, Iterator

from core.architecture_ir.diff import ChangeKind
from core.domain.migrations.changes import Aspect
from core.domain.migrations.steps import (
    Checkpoint,
    DataMigration,
    MigrationStep,
    RollbackConsideration,
    Trace,
)
from core.domain.migrations.values import MAX_ITEMS, CheckpointBasis, CheckpointStatus, StepType, TraceKind

from .patternbook import BLUE_GREEN_ID, ROLLING_ID
from .patterns import PlanningContext

S, B = CheckpointStatus, CheckpointBasis
PLANNER_TRACE = Trace(
    TraceKind.PATTERN, "migration_planner@1", "Every plan verifies its target before proceeding."
)
# (engine, the aspects that call for it, blocking, what is verified, the expected condition)
ENGINE_CHECKS = (
    (
        "validation",
        frozenset(Aspect),
        True,
        "The target revision validates.",
        "The validation engine reports no error for the target revision.",
    ),
    (
        "capacity",
        frozenset({Aspect.SCALING, Aspect.RESOURCES, Aspect.PROVISIONING, Aspect.DECOMMISSIONING}),
        False,
        "The target's capacity is sufficient under the modeled workload.",
        "The capacity analysis of the target finds no component over its modeled capacity.",
    ),
    (
        "security",
        frozenset({Aspect.SECURITY, Aspect.PROVISIONING, Aspect.PROTOCOL}),
        False,
        "The target's security controls are configured.",
        "The security analysis of the target finds no control missing that the source had.",
    ),
    (
        "observability",
        frozenset({Aspect.OBSERVABILITY, Aspect.PROVISIONING}),
        False,
        "The target's required observability signals are available.",
        "The observability analysis of the target finds every required signal declared.",
    ),
)


def _patterns(step: MigrationStep) -> set[str]:
    return {t.reference.split("@")[0] for t in step.traces if t.kind is TraceKind.PATTERN}


class _Checkpoints:
    def __init__(
        self,
        context: PlanningContext,
        steps: tuple[MigrationStep, ...],
        data: tuple[DataMigration, ...],
        rollbacks: tuple[RollbackConsideration, ...],
    ) -> None:
        self.context = context
        self.steps = {s.key: s for s in sorted(steps, key=lambda s: s.key)}
        self.by_id = {s.id: s for s in steps}
        self.data = data
        self.rollbacks = rollbacks

    def engines(self) -> Iterator[Checkpoint]:
        """The target-wide checks an engine evaluates: not run until its analysis is attached."""
        relevant = self.context.analysis.relevant
        aspects = {a for c in relevant for a in c.aspects}
        traces = (PLANNER_TRACE, *(Trace(TraceKind.CHANGE, c.ref, c.label) for c in relevant))
        for engine, triggers, blocking, subject, expected in ENGINE_CHECKS:
            if aspects & triggers:
                yield Checkpoint(
                    f"{engine}:target", subject, expected, S.NOT_RUN, B.MODELED,
                    traces[:MAX_ITEMS], blocking=blocking,
                )  # fmt: skip

    def data_moves(self) -> Iterator[Checkpoint]:
        for move in self.data:
            old, new = move.source_element_id, move.destination_element_id
            if new is None:
                continue
            verify = self.steps.get(f"verify:{old}:consistency")
            yield Checkpoint(
                f"consistency:{old}",
                f"The consistency of {new}'s data with {old}'s.",
                " ".join(move.verification),
                S.MANUAL_VERIFICATION_REQUIRED,
                B.MANUAL,
                move.traces,
                step_ids=(verify.id,) if verify else (),
            )
            if move.replication:
                replicate = self.steps.get(f"replicate:{old}")
                yield Checkpoint(
                    f"replication_lag:{old}",
                    f"The replication lag from {old} to {new} before the cutover.",
                    "Within an explicitly stated threshold: none is stated, and lag is observed only "
                    "at runtime.",
                    S.CANNOT_EVALUATE,
                    B.RUNTIME_OBSERVED,
                    move.traces,
                    step_ids=(replicate.id,) if replicate else (),
                )

    def health(self) -> Iterator[Checkpoint]:
        """Health checks are observed on the running system: never passed at planning time."""
        added = {c.element_id for c in self.context.analysis.relevant if c.change is ChangeKind.ADDED}
        strategic = {  # the elements a rolling or blue-green strategy carries out
            e
            for s in self.steps.values()
            if _patterns(s) & {ROLLING_ID, BLUE_GREEN_ID}
            for e in s.element_ids
        }
        for step in self.steps.values():
            if step.type is not StepType.VERIFY or len(step.element_ids) != 1:
                continue
            [element] = step.element_ids
            node = self.context.node(self.context.target, element)
            if (
                node is None
                or step.key != f"verify:{element}"
                or not (element in strategic or element in added)
            ):
                continue
            declared = node.configuration.values.get("health_check") is True
            expected = (
                f"{element}'s health check passes."
                if declared
                else f"{element} passes its verification (no health check is declared)."
            )
            yield Checkpoint(
                f"health:{step.key}",
                f"The health of {element}.",
                expected,
                S.NOT_RUN,
                B.RUNTIME_OBSERVED,
                step.traces,
                step_ids=(step.id,),
            )

    def manual(self) -> Iterator[Checkpoint]:
        cutover = {m.source_element_id: m.cutover for m in self.data if m.destination_element_id}
        for step in self.steps.values():
            if step.type is StepType.CUTOVER:
                old = step.key.removeprefix("cutover:")
                expected = cutover.get(old) or step.preconditions or step.completion
                yield Checkpoint(
                    f"cutover:{step.key}",
                    f"The prerequisites of {step.key}.",
                    " ".join(expected),
                    S.MANUAL_VERIFICATION_REQUIRED,
                    B.MANUAL,
                    step.traces,
                    step_ids=(step.id,),
                )
            elif step.manual_verification:
                yield Checkpoint(
                    f"manual:{step.key}",
                    f"The outcome of {step.key}.",
                    " ".join(step.completion),
                    S.MANUAL_VERIFICATION_REQUIRED,
                    B.MANUAL,
                    step.traces,
                    step_ids=(step.id,),
                )

    def rollback(self) -> Iterator[Checkpoint]:
        for consideration in self.rollbacks:
            if not consideration.retained:
                continue
            step = self.by_id[consideration.step_id]
            yield Checkpoint(
                f"rollback:{step.key}",
                f"The prerequisites of recovering from {step.key}.",
                "Retained: " + " ".join(consideration.retained),
                S.MANUAL_VERIFICATION_REQUIRED,
                B.MANUAL,
                consideration.traces,
                step_ids=(step.id,),
            )


def checkpoints(
    context: PlanningContext,
    steps: tuple[MigrationStep, ...],
    data: tuple[DataMigration, ...],
    rollbacks: Iterable[RollbackConsideration],
) -> tuple[Checkpoint, ...]:
    """The plan's verification checkpoints — none passed at planning time."""
    found = _Checkpoints(context, steps, data, tuple(rollbacks))
    every = [*found.engines(), *found.data_moves(), *found.health(), *found.manual(), *found.rollback()]
    return tuple(sorted(every, key=lambda c: c.key))
