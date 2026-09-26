"""The deterministic reliability engine behind the domain's ``ReliabilityEngine`` port."""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.results import ReliabilityResult
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo

from .context import ReliabilityContext
from .engine import Registry, analyze
from .registry import default_registry


class DeterministicReliabilityEngine:
    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or default_registry()

    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: ReliabilityAnalysisRequest,
        requirements: tuple[Requirement, ...],
    ) -> ReliabilityResult:
        return analyze(ReliabilityContext(ir, revision, request, requirements), self._registry)

    def models(self) -> tuple[Mapping[str, Any], ...]:
        """Component models (precedence order), then architecture steps (run order)."""
        models = [{"type": "model", **m.meta.to_dict()} for m in self._registry.models()]
        return tuple(models + [{"type": "step", **s.meta.to_dict()} for s in self._registry.steps()])
