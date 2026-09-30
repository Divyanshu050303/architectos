"""Rollback and recovery: for each high-impact step — a cutover, a decommissioning, a write freeze, a
data copy or replication, a reconfiguration of an existing component, and any step whose
reversibility is irreversible or unknown — how it could be recovered from, examined for what the
step actually does.

A consideration names its triggers, the recovery action in words, what must be retained for it to
remain possible, its preconditions, its data-consistency implications, how the recovery is verified
and its limitations. Switching clients back to a database after its replacement has accepted writes
makes the two diverge: that is stated, never hidden behind a generic "undo". A step that cannot be
reversed says why; one whose reversibility is not established says that. Every consideration is a
proposal for a person to review — never a command.
"""

from collections.abc import Callable, Iterable
from typing import Any

from core.domain.migrations.steps import DataMigration, MigrationStep, RollbackConsideration
from core.domain.migrations.values import Reversibility, StepType, TraceKind

from .patternbook import BLUE_GREEN_ID, ROLLING_ID

R = Reversibility


def _patterns(step: MigrationStep) -> set[str]:
    return {t.reference.split("@")[0] for t in step.traces if t.kind is TraceKind.PATTERN}


class _Rollback:
    def __init__(self, steps: tuple[MigrationStep, ...], data: Iterable[DataMigration]) -> None:
        self.keys = {s.key for s in steps}
        self.moves = {d.source_element_id: d for d in data if d.destination_element_id}

    @staticmethod
    def consider(step: MigrationStep, **fields: Any) -> RollbackConsideration:
        return RollbackConsideration(step.id, step.reversibility, step.traces, **fields)

    def cutover(self, step: MigrationStep) -> RollbackConsideration:
        old = step.key.removeprefix("cutover:")
        move = self.moves.get(old)
        if move is not None:
            new = move.destination_element_id
            return self.consider(
                step,
                triggers=(
                    f"{new} fails its verification under traffic.",
                    "Clients report errors after the switch.",
                ),
                action=f"Switch {old}'s clients back to {old}.",
                retained=(f"{old} and its data, until {new} is verified under traffic.",),
                preconditions=(
                    f"{old} has not been decommissioned.",
                    f"Writes {new} accepted since the cutover are reconciled with {old} or knowingly "
                    "discarded.",
                ),
                consistency=(
                    f"Writes {new} accepts after the cutover are not in {old}: switching back makes the two "
                    "diverge."
                ),
                verification=(f"{old}'s clients are served by {old} again.",),
                limitations=(f"Writes made to {new} after the cutover are lost to {old} unless reconciled.",),
            )
        if BLUE_GREEN_ID in _patterns(step):
            return self.consider(
                step,
                triggers=(f"{old} fails its verification under traffic.",),
                action=f"Route {old}'s traffic back to its previous environment.",
                retained=(f"The previous environment of {old}, until it is retired.",),
                preconditions=(f"The previous environment of {old} still runs.",),
                consistency="Whether the two environments share state is not modeled.",
                verification=(f"Traffic reaches only the previous environment of {old}.",),
                limitations=(f"Not possible once the previous environment of {old} is retired.",),
            )
        return self.consider(
            step,
            triggers=(f"{old} fails its verification on its new route.",),
            action=f"Restore the source route of {old}.",
            retained=(f"The source route's endpoints of {old}.",),
            preconditions=(f"The source route's endpoints of {old} still exist.",),
            verification=(f"Requests over {old} follow the source route again.",),
            limitations=(f"Not possible once the source route's endpoints of {old} are retired.",),
        )

    def decommission(self, step: MigrationStep) -> RollbackConsideration:
        target = ", ".join(step.element_ids)
        if step.reversibility is R.IRREVERSIBLE:
            return self.consider(
                step,
                retained=(f"Whatever of {target}'s data must be kept, retained before this step.",),
                preconditions=(f"Nothing depends on {target}.",),
                limitations=(f"Once removed, {target} and its data cannot be restored by this plan.",),
            )
        return self.consider(
            step,
            triggers=(f"Something still depends on {target} after it is removed.",),
            action=f"Provision {target} again as the source revision describes it.",
            preconditions=("The source revision's description of it is available.",),
            verification=(f"{target} serves as it did before.",),
            limitations=("Its runtime state (connections, caches, sessions) is not restored.",),
        )

    def freeze(self, step: MigrationStep) -> RollbackConsideration:
        old = step.key.removeprefix("prepare:").removesuffix(":freeze")
        return self.consider(
            step,
            triggers=("The copy cannot complete, or the migration is abandoned.",),
            action=f"Resume writes to {old}.",
            consistency=f"{old} remains authoritative: nothing has moved yet.",
            verification=(f"Writes to {old} succeed again.",),
        )

    def copy(self, step: MigrationStep) -> RollbackConsideration:
        old = step.key.split(":")[1]
        move = self.moves.get(old)
        new = move.destination_element_id if move else "the destination"
        return self.consider(
            step,
            triggers=("The copy or replication fails, or the migration is abandoned.",),
            action=f"Stop copying to {new} and discard its partial data.",
            consistency=f"{old} remains authoritative until the cutover.",
            verification=(f"{old} serves its clients unchanged.",),
        )

    def configure(self, step: MigrationStep) -> RollbackConsideration:
        target = ", ".join(step.element_ids)
        rolling = ROLLING_ID in _patterns(step)
        return self.consider(
            step,
            triggers=(f"{target} fails its verification with the target configuration.",),
            action=(
                f"Roll {target}'s source configuration back out one instance at a time."
                if rolling
                else f"Apply {target}'s source configuration again."
            ),
            preconditions=("The source revision's configuration is available.",),
            verification=(f"{target} passes its verification with the source configuration.",),
            limitations=(
                "Effects of the target configuration while it ran (e.g. data written) are not undone.",
            ),
        )

    def unknown(self, step: MigrationStep) -> RollbackConsideration:
        return self.consider(
            step,
            limitations=(f"Whether {step.key} can be reversed is not established; a person must assess it.",),
        )

    def irreversible(self, step: MigrationStep) -> RollbackConsideration:
        return self.consider(step, limitations=(f"{step.key} cannot be reversed.",))

    def handler(self, step: MigrationStep) -> Callable[[MigrationStep], RollbackConsideration] | None:
        """How the step is considered: by what it does, or by its reversibility alone."""
        existing = step.element_ids and not any(f"provision:{e}" in self.keys for e in step.element_ids)
        by_type = {
            StepType.CUTOVER: self.cutover,
            StepType.DECOMMISSION: self.decommission,
            StepType.BACKFILL: self.copy,
            StepType.REPLICATE: self.copy,
            StepType.PREPARE: self.freeze if step.key.endswith(":freeze") else None,
            StepType.CONFIGURE: self.configure if existing else None,
        }
        if step.reversibility is R.UNKNOWN:
            return self.unknown
        found = by_type.get(step.type)
        if found is None and step.reversibility is R.IRREVERSIBLE:
            return self.irreversible
        return found

    def of(self, step: MigrationStep) -> RollbackConsideration | None:
        handler = self.handler(step)
        return handler(step) if handler else None


def rollbacks(
    steps: tuple[MigrationStep, ...], data: Iterable[DataMigration]
) -> tuple[RollbackConsideration, ...]:
    """The rollback considerations of the plan's high-impact steps."""
    rollback = _Rollback(steps, data)
    found = (rollback.of(s) for s in sorted(steps, key=lambda s: s.key))
    return tuple(r for r in found if r is not None)
