"""The deterministic capacity engine behind the domain's ``CapacityEngine`` port."""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.ports import EngineOutput
from core.domain.capacity.scenarios import Scenario
from core.domain.validation.options import RevisionInfo

from .context import CapacityContext
from .engine import Registry, analyze
from .operating_envelope import run_scenario, scaling_options
from .registry import default_registry
from .traffic import propagate


class DeterministicCapacityEngine:
    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or default_registry()

    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: AnalysisRequest,
        scenarios: tuple[Scenario, ...],
    ) -> EngineOutput:
        context = CapacityContext(ir, revision, request)
        result = analyze(context, self._registry, propagate)
        scaling, unsupported = scaling_options(result, context)
        outcomes = tuple(run_scenario(context, result, s, self._registry, propagate) for s in scenarios)
        return EngineOutput(result, scaling, unsupported, outcomes)

    def models(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(model.meta.to_dict() for model in self._registry.models())
