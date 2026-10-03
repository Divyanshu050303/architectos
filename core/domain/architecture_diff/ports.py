"""What the engines need beyond the two architectures — the same for both states, so a difference in
their results is the architectures' and nothing else's.

- the requirements and policy in force (validation, reliability, security, observability);
- for capacity, the workload (and entries) of a stored capacity analysis the person named;
- for cost, the pricing inputs of a stored cost analysis the person named: its snapshot, currency,
  pricing date and operating hours (priced at the architectures' declared resources).

Absent inputs make the engine ``not_evaluated``, with why — never a guess.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol

from core.domain.architecture_agent.requests import AgentUsage
from core.domain.architecture_agent.results import Rejection
from core.domain.architecture_agent.runs import RawOutput
from core.domain.capacity.workload import WorkloadProfile
from core.domain.cost.pricing import PricingSnapshot
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement

from .errors import InvalidDiffRecord, InvalidDiffRequest
from .explanations import DiffExplanation
from .values import Basis, ExplanationFailure


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


# --- the AI interpretation ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplanationBudget:
    """The limits of one explanation request. A request may lower them, never raise them."""

    max_model_calls: int = 2  # the call, and at most one retry of a retryable failure
    max_input_tokens: int = 60_000
    max_output_tokens: int = 6_000
    max_seconds: float = 60.0
    max_context_chars: int = 60_000
    max_passages: int = 10

    def __post_init__(self) -> None:
        bounded = (
            1 <= self.max_model_calls <= 2
            and 1000 <= self.max_input_tokens <= 60_000
            and 256 <= self.max_output_tokens <= 6_000
            and 5 < self.max_seconds <= 60
            and 1000 <= self.max_context_chars <= 60_000
            and 0 <= self.max_passages <= 10
        )
        if not bounded:
            raise InvalidDiffRequest(details={"field": "budget", "reason": "out_of_range"})


@dataclass(frozen=True, slots=True)
class ExplanationContext:
    """What the model is given: named sections of data (each delimited as untrusted), and exactly what
    a statement may cite, by basis. Anything else it cites is refused."""

    sections: tuple[tuple[str, str], ...]
    citable: Mapping[Basis, frozenset[str]]
    group_ids: tuple[str, ...]  # the groups it may explain
    requirement_refs: tuple[str, ...]  # the requirement impacts it may explain (REQ-n)

    @property
    def size(self) -> int:
        return sum(len(body) for _, body in self.sections)

    @property
    def text(self) -> str:
        return "\n".join(body for _, body in self.sections)


@dataclass(frozen=True, slots=True)
class ExplainOutcome:
    model: str
    prompt_version: str
    usage: AgentUsage
    explanation: DiffExplanation | None = None
    failure: ExplanationFailure | None = None
    rejections: tuple[Rejection, ...] = ()
    raw: RawOutput | None = None
    attempts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.explanation is None) == (self.failure is None):
            raise InvalidDiffRecord(details={"fields": ["outcome.failure"]})


class DiffExplainer(Protocol):
    @property
    def model(self) -> str: ...

    async def explain(self, context: ExplanationContext, budget: ExplanationBudget) -> ExplainOutcome:
        """At most ``budget.max_model_calls`` calls. Never raises for a model failure."""
        ...
