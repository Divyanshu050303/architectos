"""What the evolution service needs from the engine, so the domain never imports it."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.workload import WorkloadProfile
from core.domain.cost.pricing import PricingSnapshot
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.simulations.entities import PricingInputs
from core.domain.validation.options import RevisionInfo

from .candidates import EvidenceRef
from .entities import EvolutionRequest
from .evidence import StoredAnalysis
from .results import EvolutionResult
from .values import EvidenceSource


@dataclass(frozen=True, slots=True)
class ImpactInputs:
    """What the engines need beyond the architecture, the same for the baseline and every candidate:
    the requirements and policy in force, and the inputs of the current stored analyses."""

    requirements: tuple[Requirement, ...] = ()
    policy: ArchitecturePolicy = field(default_factory=ArchitecturePolicy)
    workload: WorkloadProfile | None = None  # the current capacity analysis's
    entries: tuple[str, ...] | None = None
    pricing: PricingInputs | None = None  # the current cost analysis's snapshot, date and hours
    snapshot: PricingSnapshot | None = None
    provider: str | None = None
    currency: str | None = None
    capacity: EvidenceRef | None = None  # the analysis whose workload is reused
    cost: EvidenceRef | None = None  # the analysis whose pricing is reused


class EvolutionEngine(Protocol):
    def analyze(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        request: EvolutionRequest,
        analyses: Mapping[EvidenceSource, StoredAnalysis],
        inputs: ImpactInputs,
    ) -> EvolutionResult:
        """Deterministic for equal inputs. Never modifies ``ir``."""
        ...

    def catalog(self) -> Mapping[str, Any]:
        """The goal types, the rules (with their contracts) and the limits."""
        ...
