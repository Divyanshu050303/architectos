"""What the security service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo

from .analyses import SecurityAnalysisRequest
from .results import SecurityResult


class SecurityEngine(Protocol):
    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: SecurityAnalysisRequest,
        policy: ArchitecturePolicy,
        requirements: tuple[Requirement, ...],
    ) -> SecurityResult:
        """Deterministic for equal inputs. Raises InvalidSecurityRequest for a scope or analyzers
        the revision or the engine does not have (nothing has run then)."""
        ...

    def analyzers(self) -> tuple[Mapping[str, Any], ...]:
        """Every analyzer's description, in run order."""
        ...
