"""The capacity evaluator: workload and capacity scenarios, evaluated by the Capacity Engine.

The Capacity Engine is given the exact revision and one capacity scenario: the scenario's workload
change and its changes to the properties the Capacity Engine may change (replicas, declared limits,
per-request costs, traffic shares…). The engine computes the baseline and the scenario itself, with
the same models (``EngineOutput.scenarios``), so nothing here calculates demand, capacity or
utilization, and no formula is duplicated. Scaling follows only what a capacity model states: no
linear scaling is assumed where a model does not declare it.

From the two capacity results, per component and resource: demand, capacity and utilization,
baseline and scenario, each in the canonical unit of its dimension (``requests/second``,
``cores``, ``B``…; utilization as a ``ratio``). An unknown value stays ``None``: a workload the
models cannot place is not zero demand. A resource whose unit changes between the two sides is
marked not comparable. System-wide: the highest known utilization and the number of bottlenecks;
new and resolved bottlenecks and scaling options go to the trace, in the engine's words.

Not evaluated here, and reported: the scenario's failures (the Capacity Engine does not model where
load goes when an element is unavailable), and clearing a capacity property (a capacity scenario
sets values, it does not remove them).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.ports import CapacityEngine, EngineOutput
from core.domain.capacity.results import AnalysisStatus, CapacityResult, Utilization
from core.domain.capacity.scenarios import ConfigurationChange as CapacityChange
from core.domain.capacity.scenarios import Scenario as CapacityScenario
from core.domain.capacity.scenarios import ScenarioResult
from core.domain.capacity.units import CANONICAL, Quantity
from core.domain.engine_results import Evidence, Unsupported
from core.domain.requirements.value_objects import decimal_to_str
from core.domain.simulations.catalog import CAPACITY_PROPERTIES
from core.domain.simulations.overlay import Overlay
from core.domain.simulations.results import SYSTEM, AnalysisRun, Delta
from core.domain.simulations.scenarios import Scenario
from core.domain.simulations.values import AnalysisKind, RunState

from .context import SimulationContext
from .engine import Evaluation, EvaluatorMeta
from .validation import ScenarioPlan

A = AnalysisKind
NOT_CALCULATED = {AnalysisStatus.UNSUPPORTED, AnalysisStatus.FAILED}


def capacity_scenario(scenario: Scenario) -> tuple[CapacityScenario, tuple[Unsupported, ...]]:
    """The Capacity Engine's scenario for ``scenario``, and what it cannot express."""
    changes: list[CapacityChange] = []
    unsupported: list[Unsupported] = []
    for change in scenario.changes:
        if change.property not in CAPACITY_PROPERTIES:
            continue  # read by other engines only
        if change.value is None:
            message = f"Clearing {change.property} is not a capacity scenario: its effect is not evaluated."
            unsupported.append(Unsupported(change.element_id, "capacity_cannot_clear", message))
            continue
        changes.append(CapacityChange(change.element_id, change.property, Decimal(str(change.value))))
    if scenario.workload is not None:
        return scenario.workload.capacity_scenario(scenario.name, tuple(changes)), tuple(unsupported)
    return CapacityScenario(name=scenario.name, changes=tuple(changes)), tuple(unsupported)


@dataclass(frozen=True, slots=True)
class CapacityRun:
    """The Capacity Engine's baseline and scenario for one simulation, shared by the evaluators."""

    request: AnalysisRequest
    scenario: CapacityScenario
    unsupported: tuple[Unsupported, ...]  # what the capacity scenario cannot express
    output: EngineOutput

    @property
    def outcome(self) -> ScenarioResult:
        [outcome] = self.output.scenarios
        return outcome


def capacity_run(context: SimulationContext, engine: CapacityEngine) -> CapacityRun:
    """The Capacity Engine run of this simulation (its workload profile required), once per
    simulation: the capacity and the cost evaluators read the same results."""
    key = ("capacity", "run")
    if key not in context.memo:
        request = context.request
        assert request.workload is not None  # noqa: S101 -- callers check the workload is given
        scenario, unsupported = capacity_scenario(request.scenario)
        capacity_request = AnalysisRequest(
            architecture_id=request.architecture_id,
            revision_number=request.revision_number,
            workload=request.workload,
            entries=request.entries,
        )
        output = engine.analyze(context.ir, context.revision, capacity_request, (scenario,))
        context.memo[key] = CapacityRun(capacity_request, scenario, unsupported, output)
    run: CapacityRun = context.memo[key]
    return run


def _utilization(result: CapacityResult) -> dict[tuple[str, str], Utilization]:
    return {(c.node_id, u.resource): u for c in result.components for u in c.utilization}


def _value(quantity: Quantity | None) -> tuple[Decimal | None, str | None]:
    if quantity is None:
        return None, None
    return quantity.canonical, CANONICAL[quantity.dimension]


def _resource(node_id: str, resource: str, b: Utilization | None, a: Utilization | None) -> list[Delta]:
    deltas: list[Delta] = []
    for part in ("demand", "capacity"):
        old, old_unit = _value(getattr(b, part) if b else None)
        new, new_unit = _value(getattr(a, part) if a else None)
        unit = new_unit or old_unit
        if unit is None:
            continue  # not known on either side
        note = "unit_changed" if old_unit and new_unit and old_unit != new_unit else None
        deltas.append(Delta(A.CAPACITY, node_id, f"{resource}.{part}", unit, old, new, note))
    old_ratio, new_ratio = (b.ratio if b else None), (a.ratio if a else None)
    if old_ratio is not None or new_ratio is not None:
        deltas.append(Delta(A.CAPACITY, node_id, f"{resource}.utilization", "ratio", old_ratio, new_ratio))
    return deltas


def _deltas(baseline: CapacityResult, scenario: CapacityResult) -> list[Delta]:
    before, after = _utilization(baseline), _utilization(scenario)
    deltas: list[Delta] = []
    for node_id, resource in sorted(set(before) | set(after)):
        deltas += _resource(
            node_id, resource, before.get((node_id, resource)), after.get((node_id, resource))
        )
    old_high, new_high = baseline.summary.highest_utilization, scenario.summary.highest_utilization
    deltas.append(Delta(A.CAPACITY, SYSTEM, "highest_utilization", "ratio", old_high, new_high))
    counts = (Decimal(len(baseline.bottlenecks)), Decimal(len(scenario.bottlenecks)))
    deltas.append(Delta(A.CAPACITY, SYSTEM, "bottlenecks", "bottlenecks", *counts))
    return deltas


def _trace(outcome: ScenarioResult) -> tuple[Evidence, ...]:
    comparison = outcome.comparison
    steps = [Evidence(f"capacity.input.{e.label}", e.value) for e in comparison.changed_inputs]
    steps += [Evidence(f"capacity.change.{e.label}", e.value) for e in comparison.changed_configuration]
    steps += [Evidence(f"capacity.new_bottleneck.{n}.{r}", c) for n, r, c in comparison.new_bottlenecks]
    steps += [
        Evidence(f"capacity.resolved_bottleneck.{n}.{r}", c) for n, r, c in comparison.resolved_bottlenecks
    ]
    steps += [
        Evidence(
            f"capacity.scaling.{s.node_id}.{s.resource}",
            f"{s.current.value} {s.current.unit} -> {s.required.value} {s.required.unit} ({s.basis})",
        )
        for s in outcome.scaling
    ]
    return tuple(steps)


def _failures(overlay: Overlay) -> tuple[Unsupported, ...]:
    return tuple(
        Unsupported(
            element,
            "failure_not_modeled",
            f"{element} is unavailable; the Capacity Engine does not model where its load goes.",
        )
        for element in (*overlay.unavailable_nodes, *overlay.unavailable_connections)
    )


class CapacityEvaluator:
    meta = EvaluatorMeta(
        analysis=A.CAPACITY,
        version=1,
        name="Capacity",
        description="The Capacity Engine's baseline and scenario, from the workload profile and the "
        "scenario's workload and capacity changes: demand, capacity and utilization per resource, and "
        "bottlenecks.",
        requires=("workload",),
        unsupported=(
            "Where load goes when an element is unavailable (failures are not modeled by capacity).",
            "Clearing a capacity property.",
            "Latency, queueing and time-varying load.",
            "Scaling a model does not state (no implicit linear scaling).",
        ),
    )

    def __init__(self, engine: CapacityEngine) -> None:
        self._engine = engine

    def evaluate(
        self,
        context: SimulationContext,
        overlay: Overlay,
        plan: ScenarioPlan,
        earlier: Mapping[AnalysisKind, Evaluation],
    ) -> Evaluation:
        request = context.request
        assert request.workload is not None  # noqa: S101 -- required by the declaration
        run_ = capacity_run(context, self._engine)
        scenario, unsupported, outcome = run_.scenario, run_.unsupported, run_.outcome
        baseline, projected = run_.output.result, outcome.result
        gaps = unsupported + _failures(overlay) + outcome.unsupported_scaling
        if baseline.status in NOT_CALCULATED and projected.status in NOT_CALCULATED:
            message = "No capacity model applies to the components in scope."
            run = AnalysisRun(A.CAPACITY, RunState.UNSUPPORTED, reason="no_capacity_model", message=message)
            return Evaluation(run, unsupported=gaps)
        complete = (
            baseline.status is AnalysisStatus.COMPLETED and projected.status is AnalysisStatus.COMPLETED
        )
        state = (
            RunState.COMPLETED if complete and not gaps and not projected.unsupported else RunState.PARTIAL
        )
        run = AnalysisRun(
            A.CAPACITY,
            state,
            baseline.model_set,
            baseline.fingerprint,
            projected.fingerprint,
            message=f"Baseline {baseline.status.value}, scenario {projected.status.value}.",
        )
        workload = request.workload.assumptions
        assumptions = tuple(Evidence(f"capacity.workload.{a.key}", a.statement) for a in workload)
        if scenario.multiplier is not None:
            assumptions += (Evidence("capacity.workload_multiplier", decimal_to_str(scenario.multiplier)),)
        return Evaluation(
            run,
            deltas=tuple(_deltas(baseline, projected)),
            unsupported=gaps + tuple(projected.unsupported),
            trace=_trace(outcome),
            assumptions=assumptions,
        )
