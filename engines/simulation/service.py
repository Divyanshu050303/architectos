"""The deterministic simulation engine behind the domain's ``SimulationEngine`` port."""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.cost.pricing import PricingSnapshot
from core.domain.requirements.entities import Requirement
from core.domain.simulations.catalog import TYPES
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.limits import DEFAULT_LIMITS, SimulationLimits
from core.domain.simulations.ports import SimulationOutput
from core.domain.validation.options import RevisionInfo

from .context import SimulationContext
from .engine import Registry, simulate
from .registry import default_registry


class DeterministicSimulationEngine:
    def __init__(self, registry: Registry | None = None, limits: SimulationLimits = DEFAULT_LIMITS) -> None:
        self._registry = registry or default_registry()
        self._limits = limits

    def simulate(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: SimulationRequest,
        requirements: tuple[Requirement, ...],
        snapshot: PricingSnapshot | None,
        provider: str | None,
        currency: str | None,
    ) -> SimulationOutput:
        context = SimulationContext(ir, revision, request, requirements, snapshot, provider, currency)
        result, overlay = simulate(context, self._registry, self._limits)
        return SimulationOutput(result, overlay.to_dict())

    def catalog(self) -> Mapping[str, Any]:
        """The scenario types, the evaluators (in run order) and the execution limits."""
        return {
            "scenario_types": [t.to_dict() for t in TYPES],
            "evaluators": [e.meta.to_dict() for e in self._registry.evaluators()],
            "limits": self._limits.to_dict(),
        }
