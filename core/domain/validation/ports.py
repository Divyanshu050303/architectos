"""What the validation service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement

from .options import RevisionInfo, ValidationConfig
from .results import ValidationResult


class ValidationEngine(Protocol):
    def validate(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        *,
        requirements: tuple[Requirement, ...],
        policy: ArchitecturePolicy | None,
        config: ValidationConfig,
    ) -> ValidationResult:
        """Deterministic for equal inputs. Raises InvalidValidationConfig for a configuration the
        engine does not offer (nothing has run then)."""
        ...

    def rules(self) -> tuple[Mapping[str, Any], ...]:
        """Every rule's description (id, version, name, category, severity, profiles, …), by id."""
        ...
