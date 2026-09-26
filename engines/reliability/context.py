"""The immutable inputs of one reliability analysis: the IR revision, the request (entries,
objectives, assumptions), each component's declared reliability facts, and the shared graph index.
Models and steps read it and never change it. ``fingerprint`` identifies every input besides the
architecture's content (which the revision's content hash identifies)."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.inputs import ComponentReliability
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo


@dataclass(frozen=True)
class ReliabilityContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    request: ReliabilityAnalysisRequest
    # The project's live requirements (any status): in-force availability and reliability ones
    # become objectives.
    requirements: tuple[Requirement, ...] = ()

    @cached_property
    def topology(self) -> Topology:
        return Topology(self.ir)

    @cached_property
    def facts(self) -> Mapping[str, ComponentReliability]:
        """Every node's declared reliability facts, by node id."""
        return {node.id: ComponentReliability.of(node) for node in self.ir.nodes}

    @property
    def fingerprint(self) -> str:
        document = {
            "revision": [
                self.revision.architecture_id,
                self.revision.number,
                self.revision.content_hash,
                self.revision.schema_version,
            ],
            "request": self.request.inputs(),
            "requirements": sorted([str(r.id), r.version, r.content.status.value] for r in self.requirements),
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
