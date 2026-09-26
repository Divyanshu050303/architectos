"""The immutable inputs of one validation, and the configuration a request may choose.

Rules read the context and never change it: every part is immutable (the IR, the revision
reference, the requirements, the policy), and the graph index (``topology``) is built once and shared.
``fingerprint`` identifies every input besides the architecture's content (which the revision's
content hash identifies), so equal fingerprints and equal hashes mean equal validations.
"""

import hashlib
import json
from dataclasses import dataclass, field
from functools import cached_property

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import MAX_SELECTED_RULES, RevisionInfo, ValidationConfig


@dataclass(frozen=True)
class ValidationContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    # The project's live (not deleted) requirements, every status; None: not provided, so no
    # requirement rule can say anything (stated as a limitation). () : the project has none.
    requirements: tuple[Requirement, ...] | None = None
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
            "requirements": (
                None
                if self.requirements is None
                else sorted([str(r.id), r.version, r.content.status.value] for r in self.requirements)
            ),
            "config": self.config.to_dict(),
            "policy": self.policy.to_dict() if self.has_policy and self.policy else None,
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()


__all__ = ["MAX_SELECTED_RULES", "RevisionInfo", "ValidationConfig", "ValidationContext"]
