"""The immutable inputs of one simulation: the exact revision, the request (scenario, analyses,
workload, entries, pricing inputs, assumptions), the project's in-force requirements, the pricing
snapshot and the project's cloud provider and currency when cost is asked. Evaluators read it and
never change it. ``fingerprint`` identifies every input besides the architecture's content (which
the revision's content hash identifies).
"""

import hashlib
import json
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain.cost.pricing import PricingSnapshot
from core.domain.requirements.entities import Requirement
from core.domain.simulations.entities import SimulationRequest
from core.domain.validation.options import RevisionInfo


@dataclass(frozen=True)
class SimulationContext:
    ir: ArchitectureIR  # the exact revision: the baseline
    revision: RevisionInfo
    request: SimulationRequest
    requirements: tuple[Requirement, ...] = ()  # the project's in-force requirements
    snapshot: PricingSnapshot | None = None  # the request's pricing snapshot, read by the service
    provider: str | None = None  # the project's cloud provider, for cost
    currency: str | None = None  # the project's currency, for cost (ISO 4217)

    @cached_property
    def memo(self) -> dict[Any, Any]:
        """Work shared by the evaluators of this one simulation; never shared between simulations."""
        return {}

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
            "snapshot": [str(self.snapshot.id), self.snapshot.content_hash] if self.snapshot else None,
            "provider": self.provider,
            "currency": self.currency,
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
