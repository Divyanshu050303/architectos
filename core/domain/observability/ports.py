"""What the observability service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo

from .analyses import ObservabilityAnalysisRequest
from .results import ObservabilityResult


class ObservabilityEngine(Protocol):
    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: ObservabilityAnalysisRequest,
        policy: ArchitecturePolicy,
        requirements: tuple[Requirement, ...],
    ) -> ObservabilityResult:
        """Deterministic for equal inputs. Raises InvalidObservabilityRequest for a scope, analyzers
        or requirement ids the revision, the engine or the project does not have (nothing has run
        then)."""
        ...

    def analyzers(self) -> tuple[Mapping[str, Any], ...]:
        """Every analyzer's description, in run order."""
        ...
