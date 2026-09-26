"""The immutable inputs of one capacity analysis: the IR revision, the request (workload profile,
model selection, parameters, assumptions) and the shared graph index. Models read it and never
change it. ``fingerprint`` identifies every input besides the architecture's content (which the
revision's content hash identifies)."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.workload import WorkloadAssumption, WorkloadProfile
from core.domain.validation.options import RevisionInfo


@dataclass(frozen=True)
class CapacityContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    request: AnalysisRequest

    @property
    def workload(self) -> WorkloadProfile:
        return self.request.workload

    @cached_property
    def topology(self) -> Topology:
        return Topology(self.ir)

    @cached_property
    def assumptions(self) -> Mapping[str, WorkloadAssumption]:
        """The workload's and the request's assumptions, by key (keys never clash)."""
        return {a.key: a for a in (*self.workload.assumptions, *self.request.assumptions)}

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
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode()).hexdigest()
