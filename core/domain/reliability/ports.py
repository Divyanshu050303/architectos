"""What the reliability service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo

from .analyses import ReliabilityAnalysisRequest
from .results import ReliabilityResult


class ReliabilityEngine(Protocol):
    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: ReliabilityAnalysisRequest,
        requirements: tuple[Requirement, ...],
    ) -> ReliabilityResult:
        """Deterministic for equal inputs. Raises InvalidReliabilityRequest for what the revision
        does not have (nothing has run then)."""
        ...

    def models(self) -> tuple[Mapping[str, Any], ...]:
        """Every model's and step's description, in precedence order."""
        ...
