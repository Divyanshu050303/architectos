"""The immutable inputs of one cost analysis: the IR revision, the request (currency, pricing date,
operating hours, assumptions), the pricing snapshot it cites, the provider the project deploys to,
the capacity analysis it cites for usage (checked against the revision: another architecture or
revision is refused, never combined), and the shared graph index. ``fingerprint`` identifies every
input besides the architecture's content (the revision's hash), the snapshot's records and the
capacity result (their hashes, included)."""

import hashlib
import json
from dataclasses import dataclass
from functools import cached_property

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.capacity import CapacityBasis
from core.domain.cost.errors import IncompatibleCapacityAnalysis
from core.domain.cost.lookup import PriceIndex
from core.domain.cost.pricing import PricingSnapshot
from core.domain.validation.options import RevisionInfo


@dataclass(frozen=True)
class CostContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    request: CostAnalysisRequest
    snapshot: PricingSnapshot
    provider: str | None  # the project's cloud provider; None: not set
    capacity: CapacityBasis | None = None  # the capacity analysis the request cites

    def __post_init__(self) -> None:
        if self.capacity is None:
            if self.request.capacity_analysis_id is not None:
                raise IncompatibleCapacityAnalysis(details={"reason": "analysis"})
            return
        self.capacity.check(self.request, self.revision.content_hash)

    @cached_property
    def topology(self) -> Topology:
        return Topology(self.ir)

    @cached_property
    def prices(self) -> PriceIndex:
        """The snapshot's records by SKU, built once per analysis."""
        return PriceIndex(self.snapshot)

    @cached_property
    def snapshot_hash(self) -> str:
        return self.snapshot.content_hash

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
            "snapshot": [str(self.snapshot.id), self.snapshot_hash],
            "provider": self.provider,
            "capacity": [str(self.capacity.analysis_id), self.capacity.result_fingerprint]
            if self.capacity
            else None,
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
