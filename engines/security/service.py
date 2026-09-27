"""The deterministic security engine behind the domain's ``SecurityEngine`` port."""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.results import SecurityResult
from core.domain.validation.options import RevisionInfo

from .context import SecurityContext
from .engine import Registry, analyze
from .registry import default_registry


class DeterministicSecurityEngine:
    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or default_registry()

    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: SecurityAnalysisRequest,
        policy: ArchitecturePolicy,
        requirements: tuple[Requirement, ...],
    ) -> SecurityResult:
        return analyze(SecurityContext(ir, revision, request, policy, requirements), self._registry)

    def analyzers(self) -> tuple[Mapping[str, Any], ...]:
        """Every analyzer, in run order."""
        return tuple(a.meta.to_dict() for a in self._registry.analyzers())
