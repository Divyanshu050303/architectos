"""The deterministic cost engine behind the domain's ``CostEngine`` port.

Scenario usage comes from the capacity engine (the ``CapacityEngine`` port) run on the cited
capacity analysis's own request with the scenarios. Its baseline must reproduce the stored capacity
result exactly; if the capacity models changed since, scenarios are refused
(``capacity_models_changed``) rather than compared across model versions.
"""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.ports import CapacityEngine
from core.domain.capacity.scenarios import ScenarioResult
from core.domain.cost.aggregation import summarize
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.capacity import CapacityBasis
from core.domain.cost.errors import IncompatibleCapacityAnalysis
from core.domain.cost.ports import CostEngineOutput
from core.domain.cost.pricing import PricingSnapshot
from core.domain.cost.projection import CostScenario
from core.domain.validation.options import RevisionInfo
from engines.capacity.service import DeterministicCapacityEngine

from .calculator import Registry, analyze
from .context import CostContext
from .projection import baseline_assumptions, project
from .registry import default_registry


class DeterministicCostEngine:
    def __init__(self, registry: Registry | None = None, capacity: CapacityEngine | None = None) -> None:
        self._registry = registry or default_registry()
        self._capacity = capacity or DeterministicCapacityEngine()

    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: CostAnalysisRequest,
        snapshot: PricingSnapshot,
        provider: str | None,
        capacity: CapacityBasis | None,
        scenarios: tuple[CostScenario, ...],
    ) -> CostEngineOutput:
        context = CostContext(ir, revision, request, snapshot, provider, capacity)
        result = analyze(context, self._registry)
        runs = self._capacity_runs(ir, revision, capacity, scenarios)
        projections = tuple(
            project(context, result, scenario, self._registry, run)
            for scenario, run in zip(scenarios, runs, strict=True)
        )
        return CostEngineOutput(result, summarize(result), baseline_assumptions(context, result), projections)

    def _capacity_runs(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        capacity: CapacityBasis | None,
        scenarios: tuple[CostScenario, ...],
    ) -> tuple[ScenarioResult | None, ...]:
        if capacity is None or not scenarios:
            return tuple(None for _ in scenarios)
        request = AnalysisRequest.from_inputs(capacity.request_inputs)
        output = self._capacity.analyze(ir, revision, request, tuple(s.scenario for s in scenarios))
        if output.result.fingerprint != capacity.result_fingerprint:
            raise IncompatibleCapacityAnalysis(details={"reason": "capacity_models_changed"})
        return output.scenarios

    def models(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(model.meta.to_dict() for model in self._registry.models())
