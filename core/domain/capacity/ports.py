"""What the capacity service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.validation.options import RevisionInfo

from .analyses import AnalysisRequest
from .results import CapacityResult, Unsupported
from .scenarios import ScalingOption, Scenario, ScenarioResult


@dataclass(frozen=True, slots=True)
class EngineOutput:
    result: CapacityResult
    scaling: tuple[ScalingOption, ...]
    unsupported_scaling: tuple[Unsupported, ...]
    scenarios: tuple[ScenarioResult, ...]


class CapacityEngine(Protocol):
    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: AnalysisRequest,
        scenarios: tuple[Scenario, ...],
    ) -> EngineOutput:
        """Deterministic for equal inputs. Raises InvalidCapacityConfig or InvalidScenario for
        what the engine does not offer or cannot apply (nothing has run then)."""
        ...

    def models(self) -> tuple[Mapping[str, Any], ...]:
        """Every model's description, by id."""
        ...
