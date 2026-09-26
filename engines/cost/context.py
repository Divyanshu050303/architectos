"""The immutable inputs of one cost analysis: the IR revision, the request (currency, pricing date,
operating hours, assumptions), the pricing snapshot it cites, the provider the project deploys to,
and the shared graph index. ``fingerprint`` identifies every input besides the architecture's
content (the revision's hash) and the snapshot's records (its content hash, included)."""

import hashlib
import json
from dataclasses import dataclass
from functools import cached_property

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.pricing import PricingSnapshot
from core.domain.validation.options import RevisionInfo


@dataclass(frozen=True)
class CostContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    request: CostAnalysisRequest
    snapshot: PricingSnapshot
    provider: str | None  # the project's cloud provider; None: not set

    @cached_property
    def topology(self) -> Topology:
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
            "request": self.request.inputs(),
            "snapshot": [str(self.snapshot.id), self.snapshot.content_hash],
            "provider": self.provider,
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
