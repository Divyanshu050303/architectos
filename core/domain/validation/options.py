"""What a validation request may choose, and which revision it validates. Plain data, owned by the
domain so the service can pass them to the engine port without depending on the engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.architecture_ir.versioning import IR_SCHEMA_VERSION

from .results import Severity

MAX_SELECTED_RULES = 100


@dataclass(frozen=True, slots=True)
class RevisionInfo:
    architecture_id: str
    number: int
    content_hash: str
    schema_version: int = IR_SCHEMA_VERSION  # the IR schema the revision was stored in (upgraded on read)


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """What a request may choose. Validated against the engine's registry before anything runs."""

    profile: str = "default"
    rules: tuple[str, ...] | None = None  # None: every rule of the profile
    parameters: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # by rule id
    severity_overrides: Mapping[str, Severity] = field(default_factory=dict)  # by rule id

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "rules": sorted(self.rules) if self.rules is not None else None,
            "parameters": {
                rule: dict(sorted(params.items())) for rule, params in sorted(self.parameters.items())
            },
            "severity_overrides": {rule: s.value for rule, s in sorted(self.severity_overrides.items())},
        }
