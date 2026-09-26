"""What the cost service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.engine_results import Evidence
from core.domain.validation.options import RevisionInfo

from .aggregation import CostSummary
from .analyses import CostAnalysisRequest
from .capacity import CapacityBasis
from .pricing import PricingSnapshot
from .projection import CostScenario, ScenarioProjection
from .results import CostResult


@dataclass(frozen=True, slots=True)
class CostEngineOutput:
    result: CostResult
    summary: CostSummary
    assumptions: tuple[Evidence, ...]  # what the projection rests on
    projections: tuple[ScenarioProjection, ...]  # one per scenario, in the request's order


class CostEngine(Protocol):
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
        """Deterministic for equal inputs. Raises InvalidCostRequest, InvalidScenario or
        IncompatibleCapacityAnalysis for what it cannot price (nothing has run then)."""
        ...

    def models(self) -> tuple[Mapping[str, Any], ...]:
        """Every cost model's description, by id."""
        ...
