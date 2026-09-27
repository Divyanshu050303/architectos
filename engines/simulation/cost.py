"""The cost evaluator: the baseline and the scenario priced by the Cost Engine, with the same pricing
snapshot, and compared by the Cost Engine's own comparison. Estimates, never invoices.

The Cost Engine (through its port) prices the exact revision and the scenario's in-memory copy with
one request — the same snapshot, currency, pricing date and operating hours — so both sides share
their pricing provenance and billing conventions (730 hours a month; no currency conversion: a price
in another currency is unknown). Usage comes from the Capacity Engine's run of this simulation (its
baseline for the baseline, its scenario for the scenario, with the scenario's workload), never
recomputed here; without a workload profile, usage lines stay unknown. ``CostComparison.between``
compares the two results: known monthly totals, changed lines, and whether both are complete.

A difference between incomplete totals is between lower bounds: the system delta is marked
``incomplete`` (not comparable) and a line priced on one side only is ``unknown_line``. No price is
invented, and no period or currency is mixed.
"""

import dataclasses
import re
import uuid
from collections.abc import Mapping

from core.domain.capacity.ports import CapacityEngine
from core.domain.cost.capacity import CapacityBasis
from core.domain.cost.ports import CostEngine, CostEngineOutput
from core.domain.cost.projection import CostComparison
from core.domain.cost.results import CostStatus
from core.domain.engine_results import Evidence
from core.domain.simulations.overlay import Overlay
from core.domain.simulations.results import SYSTEM, AnalysisRun, Delta
from core.domain.simulations.values import AnalysisKind, RunState
from core.domain.validation.options import RevisionInfo
from engines.capacity.operating_envelope import scenario_workload

from .capacity import capacity_run
from .context import SimulationContext
from .engine import Evaluation, EvaluatorMeta
from .validation import ScenarioPlan

A = AnalysisKind
NOT_CALCULATED = {CostStatus.UNSUPPORTED, CostStatus.FAILED}
METRIC = re.compile(r"[^a-z0-9_.]")
ESTIMATE = Evidence(
    "cost.estimate",
    "Cost figures are estimates from the pricing snapshot and the modeled usage, not invoices.",
)


def _bases(
    context: SimulationContext, overlay: Overlay, capacity: CapacityEngine
) -> tuple[uuid.UUID, CapacityBasis, CapacityBasis] | None:
    """The capacity bases of the baseline and the scenario, from this simulation's capacity run."""
    request = context.request
    if request.workload is None:
        return None
    run = capacity_run(context, capacity)
    result, outcome = run.output.result, run.outcome
    basis_id = uuid.uuid5(uuid.NAMESPACE_URL, f"simulation:{context.fingerprint}:capacity")
    base = CapacityBasis(
        basis_id,
        request.architecture_id,
        request.revision_number,
        context.revision.content_hash,
        result.status.value,
        request.workload,
        result.model_set,
        result.fingerprint,
        (),
        request_inputs=run.request.inputs(),
    )
    baseline = base.for_scenario(request.workload, result, run.output.scaling)
    scenario = dataclasses.replace(base, revision_content_hash=overlay.scenario_hash).for_scenario(
        scenario_workload(request.workload, run.scenario), outcome.result, outcome.scaling
    )
    return basis_id, baseline, scenario


def _metric(resource: str) -> str:
    return METRIC.sub("_", resource.lower()) + ".monthly_cost"


def _deltas(comparison: CostComparison, currency: str) -> tuple[Delta, ...]:
    unit = f"{currency}/month"
    system = Delta(
        A.COST,
        SYSTEM,
        "monthly_cost",
        unit,
        comparison.baseline.amount,
        comparison.scenario.amount,
        None if comparison.complete else "incomplete",
    )
    lines = tuple(
        Delta(
            A.COST,
            line.element_id,
            _metric(line.resource),
            unit,
            line.baseline_monthly.amount if line.baseline_monthly else None,
            line.scenario_monthly.amount if line.scenario_monthly else None,
            "unknown_line" if line.unknown else None,
        )
        for line in comparison.changed_lines
    )
    return (system, *lines)


class CostEvaluator:
    meta = EvaluatorMeta(
        analysis=A.COST,
        version=1,
        name="Cost",
        description="The baseline and the scenario priced by the Cost Engine with one pricing snapshot, and "
        "compared by its own comparison: known monthly totals and changed lines, complete or not.",
        requires=("pricing",),
        unsupported=(
            "Prices missing from the snapshot (never invented).",
            "Currency conversion and mixed billing periods.",
            "Usage without a workload profile (usage lines stay unknown).",
            "Invoices, discounts not in the snapshot, taxes.",
        ),
    )

    def __init__(self, engine: CostEngine, capacity: CapacityEngine) -> None:
        self._engine = engine
        self._capacity = capacity

    def _price(
        self, context: SimulationContext, overlay: Overlay, currency: str
    ) -> tuple[CostEngineOutput, CostEngineOutput]:
        request, snapshot = context.request, context.snapshot
        assert request.pricing is not None  # noqa: S101 -- required by the declaration
        assert snapshot is not None  # noqa: S101 -- read with the pricing inputs
        cost_request = request.pricing.cost_request(
            request.architecture_id, request.revision_number, currency
        )
        bases = _bases(context, overlay, self._capacity)
        baseline_basis = scenario_basis = None
        if bases is not None:
            basis_id, baseline_basis, scenario_basis = bases
            cost_request = dataclasses.replace(cost_request, capacity_analysis_id=basis_id)
        revision = context.revision
        scenario_revision = RevisionInfo(
            revision.architecture_id, revision.number, overlay.scenario_hash, revision.schema_version
        )
        provider = context.provider
        baseline = self._engine.analyze(
            context.ir, revision, cost_request, snapshot, provider, baseline_basis, ()
        )
        projected = self._engine.analyze(
            overlay.architecture, scenario_revision, cost_request, snapshot, provider, scenario_basis, ()
        )
        return baseline, projected

    def evaluate(
        self,
        context: SimulationContext,
        overlay: Overlay,
        plan: ScenarioPlan,
        earlier: Mapping[AnalysisKind, Evaluation],
    ) -> Evaluation:
        currency = context.currency
        if currency is None:
            message = "The project declares no currency: the cost is not calculated."
            return Evaluation(
                AnalysisRun(A.COST, RunState.UNSUPPORTED, reason="no_currency", message=message)
            )
        baseline, projected = self._price(context, overlay, currency)
        before, after = baseline.result, projected.result
        if before.status in NOT_CALCULATED and after.status in NOT_CALCULATED:
            message = "No cost model applies to the components in scope."
            return Evaluation(
                AnalysisRun(A.COST, RunState.UNSUPPORTED, reason="no_cost_model", message=message)
            )
        comparison = CostComparison.between(before, after, ())
        complete = before.status is CostStatus.COMPLETED and after.status is CostStatus.COMPLETED
        run = AnalysisRun(
            A.COST,
            RunState.COMPLETED if complete and comparison.complete else RunState.PARTIAL,
            before.model_set,
            before.fingerprint,
            after.fingerprint,
            message=f"Baseline {before.status.value}, scenario {after.status.value}.",
        )
        snapshot = context.snapshot
        assert snapshot is not None  # noqa: S101 -- priced above
        trace = (
            Evidence("cost.snapshot", f"{snapshot.id} ({snapshot.content_hash})"),
            Evidence("cost.changed_lines", str(len(comparison.changed_lines))),
            Evidence("cost.complete", "true" if comparison.complete else "false"),
        )
        unsupported = {(u.element_id, u.code): u for u in (*before.unsupported, *after.unsupported)}
        return Evaluation(
            run,
            deltas=_deltas(comparison, currency),
            unsupported=tuple(unsupported.values()),
            trace=trace,
            assumptions=(ESTIMATE, *baseline.assumptions),
        )
