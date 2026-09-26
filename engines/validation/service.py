"""The deterministic validation engine behind the domain's ``ValidationEngine`` port."""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.results import ValidationResult

from .context import ValidationContext
from .engine import Registry, validate
from .registry import default_registry


class DeterministicValidationEngine:
    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry or default_registry()

    def validate(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        *,
        requirements: tuple[Requirement, ...],
        policy: ArchitecturePolicy | None,
        config: ValidationConfig,
    ) -> ValidationResult:
        context = ValidationContext(ir, revision, requirements=requirements, config=config, policy=policy)
        return validate(context, self._registry)

    def rules(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(rule.meta.to_dict() for rule in self._registry.rules())
