"""The immutable inputs of one validation, and the configuration a request may choose.

Rules read the context and never change it: every part is immutable (the IR, the revision
reference, the requirements, the policy), and the graph index (``topology``) is built once and shared.
``fingerprint`` identifies every input besides the architecture's content (which the revision's
content hash identifies), so equal fingerprints and equal hashes mean equal validations.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.results import Severity

MAX_SELECTED_RULES = 100


@dataclass(frozen=True, slots=True)
class RevisionInfo:
    architecture_id: str
    number: int
    content_hash: str
    schema_version: int = IR_SCHEMA_VERSION  # the IR schema the revision was stored in (upgraded on read)


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """What a request may choose. Validated against the registry before anything runs."""

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


@dataclass(frozen=True)
class ValidationContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    requirements: tuple[Requirement, ...] = ()  # the project's requirements in play (live)
    config: ValidationConfig = field(default_factory=ValidationConfig)
    policy: ArchitecturePolicy | None = None  # the project's policy at the time of the run

    @property
    def has_policy(self) -> bool:
        return self.policy is not None and not self.policy.is_empty

    @cached_property
    def topology(self) -> Topology:
        """The shared graph index, built on first use."""
        return Topology(self.ir)

    @property
    def fingerprint(self) -> str:
        document = {
            "revision": [
                self.revision.architecture_id,
                self.revision.number,
                self.revision.content_hash,
                self.revision.schema_version,
            ],
            "requirements": sorted([str(r.id), r.version] for r in self.requirements),
            "config": self.config.to_dict(),
            "policy": self.policy.to_dict() if self.has_policy and self.policy else None,
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()
