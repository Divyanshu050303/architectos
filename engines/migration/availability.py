"""Downtime and availability: for each step, whether downtime is known, possible or modeled away, its
traffic-routing implications and its availability and recovery considerations — held against the
constraints the request states.

- A step's downtime is the planner's classification, refined only where the architecture declares
  more: an in-place change to a component with a single declared replica may take it out of
  service (``potential_downtime``). Nothing is ever made ``modeled_online`` here; a step is online
  only when its pattern's declared prerequisites support it.
- A step with known or potential downtime is checked against the request's constraints: when the
  request states that no downtime is allowed, it is a ``constraint_conflict``; when it does not
  state whether downtime is allowed, that is ``missing_information``; when downtime is allowed, the
  step traces the constraint and the maintenance window as stated — whether the step fits in it is
  not modeled.
- No duration is ever stated, and zero downtime is never claimed.
"""

import dataclasses
from collections.abc import Iterator
from typing import Any

from core.architecture_ir.diff import ChangeKind
from core.domain.migrations.plans import PlanFinding
from core.domain.migrations.steps import MigrationStep, Trace
from core.domain.migrations.values import DowntimeStatus, FindingType, StepType, TraceKind

from .patternbook import BLUE_GREEN_ID, ROLLING_ID
from .patterns import PlanningContext

D = DowntimeStatus
DOWNTIME = frozenset({D.KNOWN_DOWNTIME, D.POTENTIAL_DOWNTIME})


def _patterns(step: MigrationStep) -> set[str]:
    return {t.reference.split("@")[0] for t in step.traces if t.kind is TraceKind.PATTERN}


class _Availability:
    def __init__(self, context: PlanningContext) -> None:
        self.context = context
        relevant = context.analysis.relevant
        self.added = frozenset(c.element_id for c in relevant if c.change is ChangeKind.ADDED)

    def replicas(self, element: str) -> int | None:
        node = self.context.node(self.context.source, element)
        value = node.configuration.values.get("replicas") if node else None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def clients(self, element: str) -> str:
        found = [n.id for n in self.context.incoming(self.context.source, element)]
        return ", ".join(found) if found else "none declared"

    def in_place(self, element: str, step: MigrationStep, changes: dict[str, Any], notes: list[str]) -> None:
        replicas = self.replicas(element)
        if replicas is None:
            notes.append(
                f"The replicas of {element} are not declared; whether it stays available is unknown."
            )
        elif replicas > 1:
            notes.append(
                f"{element} declares {replicas} replicas, but the change is applied in place: whether "
                "they are changed one at a time is not modeled."
            )
        elif step.downtime is D.UNKNOWN:
            changes["downtime"] = D.POTENTIAL_DOWNTIME
            changes["downtime_note"] = (
                f"{element} has a single declared replica: if applying the configuration restarts it, "
                "it is out of service meanwhile. Whether it restarts is not modeled."
            )
            changes["manual_verification"] = True

    def assess(self, step: MigrationStep) -> MigrationStep:
        """The step with its traffic implications and availability considerations."""
        element = step.element_ids[0] if len(step.element_ids) == 1 else None
        changes: dict[str, Any] = {}
        notes: list[str] = []
        patterns = _patterns(step)
        if step.type is StepType.CUTOVER:
            changes["traffic"] = (
                f"Traffic moves at this step ({', '.join(step.element_ids)}); requests in flight at the "
                "switch are not modeled."
            )
            notes.append(
                "The previous path is retained until the next verification, so traffic can be switched back."
            )
        elif step.type is StepType.CONFIGURE and element and ROLLING_ID in patterns:
            changes["traffic"] = (
                f"One instance of {element} at a time is out of service while the others serve."
            )
            notes.append(
                f"Capacity is reduced by one of the {self.replicas(element)} declared replicas during the "
                "roll-out; whether the others carry the load is not modeled."
            )
        elif step.type is StepType.CONFIGURE and element and element not in self.added:
            self.in_place(element, step, changes, notes)
        elif step.type is StepType.PREPARE and element and step.downtime is D.KNOWN_DOWNTIME:
            changes["traffic"] = (
                f"Writes to {element} are stopped; how its clients ({self.clients(element)}) behave "
                "meanwhile is not modeled."
            )
        elif step.type in {StepType.REPLICATE, StepType.BACKFILL} and step.downtime is not D.KNOWN_DOWNTIME:
            notes.append("Copying and replicating add load to the source; how much is not modeled.")
        elif step.type is StepType.PROVISION and BLUE_GREEN_ID in patterns:
            changes["traffic"] = "None until the cutover: the new environment receives no traffic."
        elif step.type is StepType.PROVISION and element in self.added:
            changes["traffic"] = f"None: nothing routes to {element} yet."
        elif step.type is StepType.PROVISION and len(step.element_ids) > 1:
            notes.append(
                "Whether the connecting component must restart to use the new connection is not modeled."
            )
        if not changes and not notes:
            return step
        return dataclasses.replace(step, availability=(*step.availability, *notes), **changes)

    def constrained(self, step: MigrationStep) -> MigrationStep:
        """A step with downtime, traced to the constraint that allows it and the window as stated."""
        constraints = self.context.request.constraints
        if step.downtime not in DOWNTIME or constraints.downtime_allowed is not True:
            return step
        window = constraints.maintenance_window
        trace = Trace(TraceKind.CONSTRAINT, "constraints.downtime_allowed", "Downtime is allowed, as stated.")
        note = (
            f"The stated maintenance window is {window}; whether the step fits in it is not modeled."
            if window
            else "No maintenance window is stated."
        )
        return dataclasses.replace(
            step, traces=(*step.traces, trace), availability=(*step.availability, note)
        )

    def findings(self, steps: tuple[MigrationStep, ...]) -> Iterator[PlanFinding]:
        allowed = self.context.request.constraints.downtime_allowed
        down = [s for s in steps if s.downtime in DOWNTIME]
        if not down or allowed is True:
            return
        if allowed is None:
            yield PlanFinding(
                FindingType.MISSING_INFORMATION,
                "downtime_allowed",
                f"{len(down)} step(s) have known or potential downtime, and the request does not state "
                "whether downtime is allowed.",
                element_ids=tuple(e for s in down for e in s.element_ids),
                step_ids=tuple(s.id for s in down),
                missing=("Whether downtime is allowed, and within which maintenance window.",),
            )
            return
        trace = Trace(
            TraceKind.CONSTRAINT, "constraints.downtime_allowed", "No downtime is allowed, as stated."
        )
        for step in down:
            yield PlanFinding(
                FindingType.CONSTRAINT_CONFLICT,
                f"downtime:{step.key}",
                f"{step.key} has {step.downtime.value.replace('_', ' ')}, and the request allows no "
                "downtime.",
                element_ids=step.element_ids,
                step_ids=(step.id,),
                missing=(
                    "A strategy whose declared prerequisites keep this step online, or allowed downtime.",
                ),
                traces=(*step.traces, trace),
            )


def availability(
    context: PlanningContext, steps: tuple[MigrationStep, ...]
) -> tuple[tuple[MigrationStep, ...], tuple[PlanFinding, ...]]:
    """The steps with their downtime, traffic and availability stated, and the conflicts with the
    request's constraints."""
    assessor = _Availability(context)
    assessed = tuple(assessor.constrained(assessor.assess(s)) for s in steps)
    return assessed, tuple(assessor.findings(assessed))
