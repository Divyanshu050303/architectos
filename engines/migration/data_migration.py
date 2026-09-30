"""Data migration requirements: what moving — or removing — a stateful component's data involves.

For every stateful replacement (a technology change of the same component, or one removed and one
added of the same kind) the plan states the source and destination, the scope as the request
states it, the method its steps follow (replication then cutover, or an offline copy with writes
stopped), the initial copy, the replication requirements, how consistency is verified before the
cutover, the cutover's prerequisites, what must be retained, the rollback implications and where
data could be lost. A stateful component removed without a replacement states that its data is
removed unless retained.

Volume, throughput and duration are never stated: the architecture does not model them, so they
are listed as missing and the duration is unevaluable. A scope (or, for a removal, a retention)
the request does not state is a ``missing_information`` finding: a person must state it.
"""

from collections.abc import Iterable

from core.architecture_ir.diff import ChangeKind
from core.domain.migrations.plans import PlanFinding
from core.domain.migrations.steps import DataMigration, MigrationStep, Trace
from core.domain.migrations.values import FindingType, StepType, TraceKind

from .patternbook import stateful_replacements
from .patterns import PlanningContext


def _traces(steps: Iterable[MigrationStep], stated: tuple[str, ...], element: str) -> tuple[Trace, ...]:
    found = {t for s in steps for t in s.traces if t.kind in {TraceKind.CHANGE, TraceKind.PATTERN}}
    if stated:
        found.add(Trace(TraceKind.CONSTRAINT, f"data_requirements.{element}", "; ".join(stated)))
    return tuple(sorted(found, key=lambda t: (t.kind.value, t.reference)))


class _Data:
    def __init__(self, context: PlanningContext, steps: tuple[MigrationStep, ...]) -> None:
        self.context = context
        self.steps = steps
        self.keys = {s.key for s in steps}
        self.stated: dict[str, list[str]] = {}
        for requirement in context.request.data_requirements:
            self.stated.setdefault(requirement.element_id, []).append(requirement.statement)

    def statements(self, *elements: str) -> tuple[str, ...]:
        return tuple(s for e in dict.fromkeys(elements) for s in self.stated.get(e, ()))

    def concerning(self, element: str) -> tuple[MigrationStep, ...]:
        """The steps that move or remove ``element``'s data (not those provisioning its replacement)."""
        return tuple(s for s in self.steps if element in s.element_ids and s.type is not StepType.PROVISION)

    def move(self, old: str, new: str) -> tuple[DataMigration, PlanFinding | None]:
        replicated = f"replicate:{old}" in self.keys
        target = self.context.node(self.context.target, new)
        mode = target.configuration.values.get("replication_mode") if target else None
        stated = self.statements(old, new)
        steps = self.concerning(old)
        missing = [
            f"The volume of {old}'s data (not modeled): the copy's duration cannot be evaluated.",
            "The throughput of the copy (not modeled).",
            f"How the consistency of {new} with {old} is checked.",
        ]
        if not stated:
            missing[:0] = [
                f"The scope of {old}'s data to move, stated as a data requirement.",
                f"How long {old}'s data must be retained once {new} serves.",
            ]
        if replicated:
            method = f"Replication from {old} to {new}, an initial copy, then a cutover."
            replication: str | None = (
                f"{new} declares replication_mode {mode}: {old}'s changes are captured and applied to "
                f"{new} until the cutover. How they are captured is not modeled."
            )
            cutover = f"Replication from {old} to {new} has caught up; its lag is not modeled."
            if mode == "synchronous":
                data_loss = (
                    "Synchronous replication is declared; that it holds until the cutover must be verified."
                )
            else:
                data_loss = (
                    f"With asynchronous replication, changes {old} accepted but not yet replicated at the "
                    f"cutover would be missing from {new}; the lag is not modeled."
                )
            rollback = (
                f"Until {old} is retired, its clients can be switched back to it; changes written to "
                f"{new} after the cutover are not in {old} unless replicated back."
            )
        else:
            method = f"An offline copy from {old} to {new} with writes to {old} stopped, then a cutover."
            replication = None
            cutover = f"Writes to {old} are still stopped."
            data_loss = (
                f"Writes {old} accepts after the copy begins would be missing from {new}: the plan relies "
                "on writes staying stopped until the cutover."
            )
            rollback = (
                f"Until {old} is retired, its clients can be switched back to it; writes made to {new} "
                f"after the cutover are not in {old}."
            )
        migration = DataMigration(
            key=f"data:{old}",
            traces=_traces(steps, stated, old),
            source_element_id=old,
            destination_element_id=new,
            scope="; ".join(stated) if stated else None,
            method=method,
            backfill=f"An initial copy of {old}'s existing data to {new}.",
            replication=replication,
            cutover=(f"{new} holds {old}'s data and its consistency check has passed.", cutover),
            step_ids=tuple(s.id for s in steps),
            verification=(f"A consistency check of {new} against {old} passes before the cutover.",),
            retention=(f"{old}'s data is retained until {new} is verified under traffic.",),
            rollback=rollback,
            data_loss=data_loss,
            missing=tuple(missing),
        )
        if stated:
            return migration, None
        return migration, PlanFinding(
            FindingType.MISSING_INFORMATION,
            f"data_scope:{old}",
            f"The request does not state which of {old}'s data moves to {new}, or what is retained.",
            element_ids=(old, new),
            step_ids=migration.step_ids,
            missing=(f"A data requirement for {old}: its scope, retention and verification.",),
            traces=migration.traces,
        )

    def removal(self, old: str) -> tuple[DataMigration, PlanFinding | None]:
        stated = self.statements(old)
        steps = self.concerning(old)
        migration = DataMigration(
            key=f"data:{old}",
            traces=_traces(steps, stated, old),
            source_element_id=old,
            scope="; ".join(stated) if stated else None,
            step_ids=tuple(s.id for s in steps),
            rollback=f"Once {old} is decommissioned, this plan cannot recover its data.",
            data_loss=f"Decommissioning {old} removes its data unless it is retained first.",
            missing=()
            if stated
            else (f"Whether {old}'s data must be retained, and how, before it is removed.",),
        )
        if stated:
            return migration, None
        return migration, PlanFinding(
            FindingType.MISSING_INFORMATION,
            f"data_retention:{old}",
            f"{old} is removed with its data, and the request does not state what is retained.",
            element_ids=(old,),
            step_ids=migration.step_ids,
            missing=(f"A data requirement for {old}: what is retained before it is removed.",),
            traces=migration.traces,
        )


def data_migrations(
    context: PlanningContext, steps: tuple[MigrationStep, ...]
) -> tuple[tuple[DataMigration, ...], tuple[PlanFinding, ...]]:
    """The data each stateful replacement moves and each stateful removal discards, with what is
    missing to plan it with confidence."""
    data = _Data(context, steps)
    pairs = stateful_replacements(context)
    paired = {old for old, _ in pairs}
    removed = sorted(
        c.element_id
        for c in context.plannable()
        if c.element == "node"
        and c.stateful
        and c.change is ChangeKind.REMOVED
        and c.element_id not in paired
    )
    results = [data.move(old, new) for old, new in pairs] + [data.removal(old) for old in removed]
    return tuple(m for m, _ in results), tuple(f for _, f in results if f is not None)
