"""What the engines establish about the two states (rule ``diff-impact@1``): each engine analyzes both
architectures with the same inputs, and its results are compared — nothing is calculated here.

- **Validation, reliability, security, observability** need nothing but the architecture, the
  requirements and the policy. Findings are compared by their **stable ids**: introduced (only in the
  target), resolved (only in the base), unchanged (counted). Each state's own counts are kept as the
  engine summarized them. The validation engine's verdict on each requirement, in each state, feeds the
  requirement impact.
- **Capacity** runs only on the workload of a stored capacity analysis the person named, the same for
  both states: bottlenecks (by node and resource) introduced or resolved, and the highest utilization
  and saturation multiple — only when the engine calculated them in both states.
- **Cost** runs only on the pricing inputs of a stored cost analysis the person named (one snapshot,
  date and operating hours for both states), on the architectures' declared resources: the known
  monthly total in each state, only when both states priced something, said to be a lower bound when
  the engine says it is, with each state's status.

An engine without its inputs is ``not_evaluated``, an engine that fails is ``failed`` (no findings, no
measures) — never zero, never estimated. Identical states are not analyzed: nothing differs.

Each state is analyzed as what it is: a revision under its architecture and number; a candidate under
its agent run's id, as revision 1 (a candidate has no revision yet).
"""

import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.architecture_diff.impacts import EngineImpact, FindingDelta, MeasureDelta
from core.domain.architecture_diff.ports import CapacityInputs, CostInputs, ImpactInputs
from core.domain.architecture_diff.values import FindingState, ImpactStatus
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.ports import CapacityEngine
from core.domain.cost.aggregation import summarize
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.ports import CostEngine
from core.domain.cost.results import CostStatus
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.ports import ObservabilityEngine
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.ports import ReliabilityEngine
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.ports import SecurityEngine
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.ports import ValidationEngine

log = logging.getLogger(__name__)

RULE = "diff-impact@1"
ENGINE_ERROR = "engine_error"
IDENTICAL = "The two states are identical: there is nothing to compare."
NO_WORKLOAD = "No capacity analysis was named: its workload is needed to compare capacity."
NO_PRICING = "No cost analysis was named: its pricing snapshot is needed to compare cost."
DECLARED = "Priced at each architecture's declared resources (no capacity basis)."
PRICED = frozenset({CostStatus.COMPLETED, CostStatus.PARTIAL})
ENGINES = ("validation", "reliability", "security", "observability", "capacity", "cost")

type Finding = tuple[str, str, str, tuple[str, ...]]  # id, severity, title, elements
type Verdicts = Mapping[uuid.UUID, str]


@dataclass(frozen=True, slots=True)
class DiffEngines:
    validation: ValidationEngine
    reliability: ReliabilityEngine
    security: SecurityEngine
    observability: ObservabilityEngine
    capacity: CapacityEngine
    cost: CostEngine


@dataclass(frozen=True, slots=True)
class State:
    """One side as the engines see it."""

    ir: ArchitectureIR
    reference_id: uuid.UUID  # the architecture's id, or a candidate's agent run id
    number: int  # the revision number; 1 for a candidate
    content_hash: str

    @property
    def revision(self) -> RevisionInfo:
        return RevisionInfo(str(self.reference_id), self.number, self.content_hash, IR_SCHEMA_VERSION)


@dataclass(frozen=True, slots=True)
class Impacts:
    engines: tuple[EngineImpact, ...]
    verdicts: tuple[Verdicts, Verdicts] = field(default_factory=lambda: ({}, {}))


def _findings(before: Iterable[Finding], after: Iterable[Finding]) -> tuple[tuple[FindingDelta, ...], int]:
    old, new = {f[0]: f for f in before}, {f[0]: f for f in after}
    introduced = [
        FindingDelta(i, FindingState.INTRODUCED, f[1], f[2][:500], f[3])
        for i, f in sorted(new.items())
        if i not in old
    ]
    resolved = [
        FindingDelta(i, FindingState.RESOLVED, f[1], f[2][:500], f[3])
        for i, f in sorted(old.items())
        if i not in new
    ]
    return (*introduced, *resolved), len(old.keys() & new.keys())


def _evaluated(
    engine: str,
    findings: tuple[Iterable[Finding], Iterable[Finding]],
    summaries: tuple[dict[str, Any], dict[str, Any]],
    versions: dict[str, Any],
    measures: tuple[MeasureDelta, ...] = (),
    limitations: tuple[str, ...] = (),
) -> EngineImpact:
    deltas, unchanged = _findings(*findings)
    return EngineImpact(
        engine,
        ImpactStatus.EVALUATED,
        versions,
        deltas,
        unchanged,
        summaries[0],
        summaries[1],
        measures,
        limitations,
    )


def _not_evaluated(engine: str, reason: str) -> EngineImpact:
    return EngineImpact(engine, ImpactStatus.NOT_EVALUATED, limitations=(reason,))


def _guarded(engine: str, run: Callable[[], EngineImpact]) -> EngineImpact:
    try:
        return run()
    except Exception:  # an engine bug or an input it refuses: reported, never hidden
        log.exception("architecture diff: engine could not compare", extra={"engine": engine, "rule": RULE})
        reason = f"The {engine} engine could not analyze both states; its absence of findings means nothing."
        return EngineImpact(engine, ImpactStatus.FAILED, limitations=(reason,), error=ENGINE_ERROR)


def _analysis_findings(result: Any) -> list[Finding]:
    return [(str(f.id), f.severity.value, f.title, (*f.node_ids, *f.connection_ids)) for f in result.findings]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _measure(name: str, before: Decimal | None, after: Decimal | None, unit: str) -> MeasureDelta | None:
    first, second = _decimal(before), _decimal(after)
    return MeasureDelta(name, first, second, unit) if first is not None and second is not None else None


class ImpactComparer:
    def __init__(self, engines: DiffEngines) -> None:
        self._engines = engines

    def compare(self, base: State, target: State, inputs: ImpactInputs) -> Impacts:
        if base.content_hash == target.content_hash:
            return Impacts(tuple(_not_evaluated(engine, IDENTICAL) for engine in ENGINES))
        verdicts: list[Verdicts] = []
        capacity, cost = inputs.capacity, inputs.cost
        impacts = (
            _guarded("validation", lambda: self._validation(base, target, inputs, verdicts)),
            _guarded("reliability", lambda: self._reliability(base, target, inputs)),
            _guarded("security", lambda: self._security(base, target, inputs)),
            _guarded("observability", lambda: self._observability(base, target, inputs)),
            _guarded("capacity", lambda: self._capacity(base, target, capacity))
            if capacity
            else _not_evaluated("capacity", NO_WORKLOAD),
            _guarded("cost", lambda: self._cost(base, target, cost))
            if cost
            else _not_evaluated("cost", NO_PRICING),
        )
        known = (verdicts[0], verdicts[1]) if len(verdicts) == 2 else ({}, {})
        return Impacts(impacts, known)

    # --- the analysis engines --------------------------------------------------------------------

    def _validation(
        self, base: State, target: State, inputs: ImpactInputs, verdicts: list[Verdicts]
    ) -> EngineImpact:
        policy = None if inputs.policy.is_empty else inputs.policy
        results = [
            self._engines.validation.validate(
                s.ir, s.revision, requirements=inputs.requirements, policy=policy, config=ValidationConfig()
            )
            for s in (base, target)
        ]
        verdicts.extend(
            {uuid.UUID(r.requirement_id): r.verdict.value for r in x.requirement_results} for x in results
        )
        before, after = (
            [(f.id, f.severity.value, f.title, tuple(f.entity_ids)) for f in r.findings] for r in results
        )
        summaries = (results[0].summary.to_dict(), results[1].summary.to_dict())
        return _evaluated(
            "validation", (before, after), summaries, {"rule_set": results[1].rule_set.to_dict()}
        )

    def _reliability(self, base: State, target: State, inputs: ImpactInputs) -> EngineImpact:
        engine = self._engines.reliability
        results = [
            engine.analyze(
                s.ir, s.revision, ReliabilityAnalysisRequest(s.reference_id, s.number), inputs.requirements
            )
            for s in (base, target)
        ]
        findings = (_analysis_findings(results[0]), _analysis_findings(results[1]))
        summaries = (results[0].summary(), results[1].summary())
        return _evaluated("reliability", findings, summaries, {"models": results[1].model_set.to_dict()})

    def _security(self, base: State, target: State, inputs: ImpactInputs) -> EngineImpact:
        engine = self._engines.security
        results = [
            engine.analyze(
                s.ir,
                s.revision,
                SecurityAnalysisRequest(s.reference_id, s.number),
                inputs.policy,
                inputs.requirements,
            )
            for s in (base, target)
        ]
        findings = (_analysis_findings(results[0]), _analysis_findings(results[1]))
        summaries = (results[0].summary(), results[1].summary())
        return _evaluated("security", findings, summaries, {"models": results[1].analyzer_set.to_dict()})

    def _observability(self, base: State, target: State, inputs: ImpactInputs) -> EngineImpact:
        engine = self._engines.observability
        results = [
            engine.analyze(
                s.ir,
                s.revision,
                ObservabilityAnalysisRequest(s.reference_id, s.number),
                inputs.policy,
                inputs.requirements,
            )
            for s in (base, target)
        ]
        findings = (_analysis_findings(results[0]), _analysis_findings(results[1]))
        summaries = (results[0].summary(), results[1].summary())
        return _evaluated("observability", findings, summaries, {"models": results[1].analyzer_set.to_dict()})

    # --- capacity and cost: only on the inputs the person named ----------------------------------

    def _capacity(self, base: State, target: State, given: CapacityInputs) -> EngineImpact:
        results = [
            self._engines.capacity.analyze(
                s.ir,
                s.revision,
                AnalysisRequest(s.reference_id, s.number, given.workload, entries=given.entries),
                (),
            ).result
            for s in (base, target)
        ]
        before, after = (
            [
                (
                    f"{b.node_id}:{b.resource}",
                    b.certainty.value,
                    f"{b.condition.value}: {b.resource}",
                    (b.node_id,),
                )
                for b in r.bottlenecks
            ]
            for r in results
        )
        first, second = results[0].summary, results[1].summary
        candidates = (
            _measure("highest_utilization", first.highest_utilization, second.highest_utilization, "ratio"),
            _measure(
                "saturation_multiple", first.saturation_multiple, second.saturation_multiple, "multiple"
            ),
        )
        measures = tuple(m for m in candidates if m is not None)  # never one side alone
        reused = f"Both states analyzed with the workload of capacity analysis {given.analysis_id}."
        versions = {"models": results[1].model_set.to_dict()}
        summaries = (first.to_dict(), second.to_dict())
        return _evaluated("capacity", (before, after), summaries, versions, measures, (reused,))

    def _cost(self, base: State, target: State, given: CostInputs) -> EngineImpact:
        outputs = [
            self._engines.cost.analyze(
                s.ir,
                s.revision,
                CostAnalysisRequest(
                    s.reference_id,
                    s.number,
                    given.snapshot.id,
                    given.currency,
                    given.pricing_date,
                    given.operating_hours_per_month,
                ),
                given.snapshot,
                given.provider,
                None,
                (),
            )
            for s in (base, target)
        ]
        statuses = [o.summary.status for o in outputs]
        totals = [summarize(o.result).totals for o in outputs]
        limitations = [DECLARED, f"Both states priced with the inputs of cost analysis {given.analysis_id}."]
        measures: tuple[MeasureDelta, ...] = ()
        if all(s in PRICED for s in statuses):  # a 0 from "nothing could be priced" is not a cost
            total = _measure(
                "known_monthly_total", totals[0].monthly.amount, totals[1].monthly.amount, totals[1].currency
            )
            measures = (total,) if total else ()
            if not all(t.complete for t in totals):
                limitations.append(
                    "A known monthly total is a lower bound: some lines are unknown or unsupported."
                )
        else:
            limitations.append("No monthly total is compared: a state could not be priced.")
        summaries = ({"status": statuses[0].value}, {"status": statuses[1].value})
        result = outputs[1].result
        versions = {"models": result.model_set.to_dict(), "snapshot_hash": result.snapshot_hash}
        return _evaluated("cost", ((), ()), summaries, versions, measures, tuple(limitations))
