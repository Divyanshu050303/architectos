"""Reliability objectives: each checked against modeled evidence only.

Objectives come from the request and from the project's requirements (``sla.py``). Each is checked
on its subjects:

- ``availability``: the named components' availability, else every request path's;
- ``recovery_time``: the named components' recovery time, else every component on a request path
  whose recovery a model describes;
- ``data_loss``: the named components' data loss window, else every database, object store and
  queue;
- ``redundancy``: the named components', else every path component's replicas or declared
  alternatives on its path (a count).

The verdict: ``violated`` when a modeled value misses it (the offending subjects are named, even if
others are unknown), else ``not_verifiable`` when any subject's value is unknown (never a pass),
else ``satisfied``; ``not_applicable`` when there is nothing to check it on. A violated objective is
a finding (``*_below_objective``, ``*_exceeds_objective``), with the requirement's priority as its
severity; one that cannot be evaluated is an ``objective_not_evaluable`` finding naming what is
missing.
"""

from collections.abc import Iterable
from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.domain.capacity.results import Certainty
from core.domain.capacity.units import rounded_text
from core.domain.engine_results import Evidence
from core.domain.reliability.analyses import Objective
from core.domain.reliability.results import FindingType, ObjectiveKind, ObjectiveResult, ReliabilityFinding
from core.domain.requirements.enums import RequirementPriority
from core.domain.validation.results import Severity, Verdict
from engines.validation.rules.requirements import SEVERITY_BY_PRIORITY

from .availability import RUNNING
from .context import ReliabilityContext
from .engine import OUT_OF_SCOPE, Progress, StepMeta, StepOutput, names
from .sla import translate

STORES = frozenset({NodeKind.DATABASE, NodeKind.STORAGE, NodeKind.QUEUE})
RESOURCE = {
    ObjectiveKind.AVAILABILITY: "availability",
    ObjectiveKind.RECOVERY_TIME: "recovery_time",
    ObjectiveKind.DATA_LOSS: "data_loss_window",
    ObjectiveKind.REDUNDANCY: "redundancy",
}
VIOLATION = {
    ObjectiveKind.AVAILABILITY: FindingType.AVAILABILITY_BELOW_OBJECTIVE,
    ObjectiveKind.RECOVERY_TIME: FindingType.RECOVERY_EXCEEDS_OBJECTIVE,
    ObjectiveKind.DATA_LOSS: FindingType.DATA_LOSS_EXCEEDS_OBJECTIVE,
    ObjectiveKind.REDUNDANCY: FindingType.REDUNDANCY_BELOW_OBJECTIVE,
}
type Subject = tuple[str, Decimal | None, tuple[str, ...]]  # (element, value, what is missing)


class Objectives:
    meta = StepMeta(
        id="objectives",
        version=1,
        name="Reliability objectives",
        description="Request objectives and in-force reliability requirements, against modeled evidence.",
        produces=("objectives", "findings"),
        assumptions=("An objective passes only on modeled values; missing evidence is never success.",),
        limitations=("Requirements stated in words only are not interpreted.",),
    )

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        derived, verdicts = translate(context.requirements, context.topology)
        priorities = {str(r.id): r.content.priority for r in context.requirements}
        results: list[ObjectiveResult] = list(verdicts)
        findings: list[ReliabilityFinding] = []
        for objective in (*context.request.objectives, *derived):
            subjects = self._subjects(objective, context, progress)
            result, failing = self._verdict(objective, subjects)
            results.append(result)
            priority = priorities.get(result.requirement_id or "")
            finding = self._finding(objective, result, failing, priority)
            if finding is not None:
                findings.append(finding)
        return StepOutput(findings=tuple(findings), objectives=tuple(results))

    # --- what an objective is checked on ---------------------------------------------------------

    def _subjects(
        self, objective: Objective, context: ReliabilityContext, progress: Progress
    ) -> list[Subject]:
        resource = RESOURCE[objective.kind]
        on_paths = sorted({n for p in progress.paths for n in p.node_ids if _in_scope(context, n)})
        if objective.kind is ObjectiveKind.AVAILABILITY and not objective.node_ids:
            return [(p.entry_id, _value(p.availability), p.availability.missing) for p in progress.paths]
        if objective.node_ids:
            nodes: Iterable[str] = objective.node_ids
        elif objective.kind is ObjectiveKind.DATA_LOSS:
            nodes = [n.id for n in context.ir.nodes if n.kind in STORES]
        elif objective.kind is ObjectiveKind.RECOVERY_TIME:
            nodes = [n for n in on_paths if (node := context.topology.node(n)) and node.kind in RUNNING]
        else:
            nodes = on_paths
        if objective.kind is ObjectiveKind.REDUNDANCY:
            return [self._redundancy(context, n, on_paths) for n in nodes]
        subjects: list[Subject] = []
        for node_id in nodes:
            component = progress.component(node_id)
            estimate = component.estimate(resource) if component else None
            if estimate is None:
                subjects.append((node_id, None, (f"{node_id}.{resource}",)))
            else:
                subjects.append((node_id, _value(estimate), estimate.missing or (f"{node_id}.{resource}",)))
        return subjects

    @staticmethod
    def _redundancy(context: ReliabilityContext, node_id: str, on_paths: list[str]) -> Subject:
        facts = context.facts[node_id]
        replicas = facts.number("replicas")
        group = facts.known("redundancy_group")
        members = (
            [n for n in on_paths if context.facts[n].known("redundancy_group") == group] if group else []
        )
        counts = [c for c in (replicas, Decimal(len(members)) if members else None) if c is not None]
        if not counts:
            return (node_id, None, (f"{node_id}.replicas",))
        return (node_id, max(counts), ())

    # --- verdicts and findings -------------------------------------------------------------------

    @staticmethod
    def _verdict(objective: Objective, subjects: list[Subject]) -> tuple[ObjectiveResult, tuple[str, ...]]:
        resource = RESOURCE[objective.kind]
        known = [(s, v) for s, v, _ in subjects if v is not None]
        failing = tuple(s for s, v in known if not objective.met(v))
        unknown = [(s, m) for s, v, m in subjects if v is None]
        actual = tuple(Evidence(f"{s}.{resource}", rounded_text(v)) for s, v in known)
        requirement = str(objective.requirement_id) if objective.requirement_id else None
        target = objective.stated
        if not subjects:
            why = "Nothing in the architecture is concerned by this objective."
            result = ObjectiveResult(
                objective.key, objective.kind, target, Verdict.NOT_APPLICABLE, why, requirement_id=requirement
            )
            return result, ()
        if failing:
            verdict = Verdict.VIOLATED
            explanation = f"{names(failing)} {'misses' if len(failing) == 1 else 'miss'} {target} (modeled)."
        elif unknown:
            verdict = Verdict.NOT_VERIFIABLE
            explanation = f"Not established for {names(s for s, _ in unknown)}: their values are not modeled."
        else:
            verdict = Verdict.SATISFIED
            explanation = f"Every modeled value meets {target}."
        missing = tuple(m for _, names in unknown for m in names) if verdict is not Verdict.SATISFIED else ()
        nodes = tuple(s for s, _, _ in subjects)
        result = ObjectiveResult(
            objective.key, objective.kind, target, verdict, explanation, nodes, actual, missing, requirement
        )
        return result, failing

    def _finding(
        self,
        objective: Objective,
        result: ObjectiveResult,
        failing: tuple[str, ...],
        priority: RequirementPriority | None,
    ) -> ReliabilityFinding | None:
        if result.verdict is Verdict.VIOLATED:
            return ReliabilityFinding(
                VIOLATION[objective.kind],
                SEVERITY_BY_PRIORITY[priority] if priority is not None else Severity.MEDIUM,
                Certainty.MODELED,
                f"The objective {objective.key} ({result.target}) is not met",
                f"{result.explanation} The modeled architecture does not support the objective; this is an "
                "estimate from declared values, not a measurement.",
                "Review the components named: redundancy, failover or recovery, or the objective itself.",
                node_ids=failing,
                evidence=result.actual,
                model_id=self.meta.id,
                model_version=self.meta.version,
                objective=objective.key,
            )
        if result.verdict is Verdict.NOT_VERIFIABLE:
            known = {e.label.rsplit(".", 1)[0] for e in result.actual}
            unknown = tuple(n for n in result.node_ids if n not in known)
            return ReliabilityFinding(
                FindingType.OBJECTIVE_NOT_EVALUABLE,
                Severity.LOW,
                Certainty.CANDIDATE,
                f"The objective {objective.key} ({result.target}) cannot be evaluated",
                f"{result.explanation} It is neither met nor missed until they are.",
                "Declare the missing inputs, then run the analysis again.",
                node_ids=unknown or result.node_ids,
                evidence=result.actual,
                missing=result.missing,
                model_id=self.meta.id,
                model_version=self.meta.version,
                objective=objective.key,
            )
        return None


def _in_scope(context: ReliabilityContext, node_id: str) -> bool:
    node = context.topology.node(node_id)
    return node is not None and node.kind not in OUT_OF_SCOPE


def _value(estimate: object) -> Decimal | None:
    quantity = getattr(estimate, "quantity", None)
    return quantity.value if quantity is not None else None
