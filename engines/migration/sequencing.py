"""Transition validation and sequencing: whether each step has what must come before it, and the order
the steps would be carried out in.

**Transitions** are checked against the steps and the classified changes, never against a step's
wording:

- a step concerning an element the target adds waits for that element's own provisioning;
- the decommissioning of an element the target removes waits for every other step concerning it;
- a cutover waits for a verification or a preparation, and a decommissioning for a verification;
- a cutover, an irreversible step, and a step with known or potential downtime require a person's
  verification before the plan proceeds past it.

A transition without its prerequisite is a ``missing_prerequisite`` finding naming the step and
what it lacks; nothing is added or reordered to hide it.

The **sequence** follows the dependency graph's levels, each in key order. Steps of one level are
grouped into a parallel stage only when each is explicitly parallelizable; every other step is a
stage of its own. A plan whose dependencies are invalid or cyclic has no sequence.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from core.architecture_ir.diff import ChangeKind
from core.domain.migrations.changes import ChangeAnalysis
from core.domain.migrations.plans import PlanFinding, Stage
from core.domain.migrations.steps import MigrationStep
from core.domain.migrations.values import DowntimeStatus, FindingType, Reversibility, StepType

from .dependency_graph import DependencyGraph

CUTOVER_AFTER = frozenset({StepType.VERIFY, StepType.PREPARE})
DOWNTIME = frozenset({DowntimeStatus.KNOWN_DOWNTIME, DowntimeStatus.POTENTIAL_DOWNTIME})


@dataclass(frozen=True, slots=True)
class Sequencing:
    sequence: tuple[Stage, ...]
    findings: tuple[PlanFinding, ...]


def _missing(step: MigrationStep, rule: str, message: str, needed: str) -> PlanFinding:
    return PlanFinding(
        FindingType.MISSING_PREREQUISITE,
        f"{rule}:{step.key}",
        message,
        element_ids=step.element_ids,
        step_ids=(step.id,),
        missing=(needed,),
    )


class _Transitions:
    def __init__(self, graph: DependencyGraph, analysis: ChangeAnalysis) -> None:
        self.graph = graph
        added = [c for c in analysis.relevant if c.change is ChangeKind.ADDED]
        self.added = frozenset(c.element_id for c in added)
        self.connections = frozenset(c.element_id for c in added if c.element == "connection")
        self.removed = frozenset(c.element_id for c in analysis.relevant if c.change is ChangeKind.REMOVED)

    def provisions(self, step: MigrationStep, element: str) -> bool:
        """A connection's provisioning also names its endpoints, which must already exist."""
        if step.type is not StepType.PROVISION or element not in step.element_ids:
            return False
        return step.element_ids == (element,) or element in self.connections

    def check(self, step_id: str) -> Iterator[PlanFinding]:
        graph, step = self.graph, self.graph.steps[step_id]
        ancestors = graph.ancestors(step_id)
        before = [graph.steps[a] for a in ancestors]
        for element in sorted(self.added & set(step.element_ids)):
            if not self.provisions(step, element) and not any(self.provisions(a, element) for a in before):
                yield _missing(
                    step,
                    f"provisioned:{element}",
                    f"{step.key} concerns {element}, which the target adds, before it is provisioned.",
                    f"A dependency on the step provisioning {element}.",
                )
        if step.type is StepType.DECOMMISSION and len(step.element_ids) == 1:
            [element] = step.element_ids
            later = sorted(
                s.key
                for i, s in graph.steps.items()
                if element in self.removed
                and i != step_id
                and element in s.element_ids
                and i not in ancestors
            )
            if later:
                yield _missing(
                    step,
                    f"retired:{element}",
                    f"{step.key} removes {element} before steps that still concern it: {', '.join(later)}.",
                    f"Dependencies on {', '.join(later)}.",
                )
        types = {a.type for a in before}
        if step.type is StepType.CUTOVER and not types & CUTOVER_AFTER:
            yield _missing(
                step,
                "verified",
                f"{step.key} switches over without a verification or preparation before it.",
                "A dependency on a step verifying or preparing what it switches to.",
            )
        if step.type is StepType.DECOMMISSION and StepType.VERIFY not in types:
            yield _missing(
                step,
                "verified",
                f"{step.key} removes something without a verification before it.",
                "A dependency on a step verifying that nothing uses what it removes.",
            )
        consequential = (
            step.type is StepType.CUTOVER
            or step.reversibility is Reversibility.IRREVERSIBLE
            or step.downtime in DOWNTIME
        )
        if consequential and not step.manual_verification:
            yield _missing(
                step,
                "manual",
                f"{step.key} is a cutover, is irreversible or causes downtime, and no person verifies it "
                "before the plan proceeds.",
                "Manual verification of this step.",
            )


def _stages(graph: DependencyGraph) -> tuple[Stage, ...]:
    stages: list[Stage] = []
    for level in graph.levels():
        together = tuple(i for i in level if graph.steps[i].parallelizable)
        if len(together) < 2:
            together = ()
        else:
            stages.append(Stage(len(stages) + 1, together, parallel=True))
        for i in level:
            if i not in together:
                stages.append(Stage(len(stages) + 1, (i,)))
    return tuple(stages)


def sequence(steps: tuple[MigrationStep, ...], analysis: ChangeAnalysis) -> Sequencing:
    """The dependency and transition findings for these steps, and their sequence when the
    dependencies allow one."""
    graph = DependencyGraph.of(steps)
    findings = graph.findings()
    if findings:  # an invalid graph can be neither ordered nor its transitions judged
        return Sequencing((), findings)
    transitions = _Transitions(graph, analysis)
    return Sequencing(_stages(graph), tuple(f for i in graph.steps for f in transitions.check(i)))
