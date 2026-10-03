"""What the engines need beyond the two architectures — the same for both states, so a difference in
their results is the architectures' and nothing else's.

- the requirements and policy in force (validation, reliability, security, observability);
- for capacity, the workload (and entries) of a stored capacity analysis the person named;
- for cost, the pricing inputs of a stored cost analysis the person named: its snapshot, currency,
  pricing date and operating hours (priced at the architectures' declared resources).

Absent inputs make the engine ``not_evaluated``, with why — never a guess.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from core.domain.capacity.workload import WorkloadProfile
from core.domain.cost.pricing import PricingSnapshot
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement


@dataclass(frozen=True, slots=True)
class CapacityInputs:
    analysis_id: uuid.UUID  # the stored capacity analysis whose workload is reused
    workload: WorkloadProfile
    entries: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class CostInputs:
    analysis_id: uuid.UUID  # the stored cost analysis whose pricing inputs are reused
    snapshot: PricingSnapshot
    currency: str
    pricing_date: date
    operating_hours_per_month: Decimal
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class ImpactInputs:
    requirements: tuple[Requirement, ...] = ()
    policy: ArchitecturePolicy = field(default_factory=ArchitecturePolicy)
    capacity: CapacityInputs | None = None
    cost: CostInputs | None = None
