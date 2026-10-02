"""What the migration plan service asks of the planning engine: a proposal for exact inputs it has
read and authorized, and the versions of the rules and models it plans with (for staleness)."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.evolution.candidates import Candidate

from .entities import MigrationRequest
from .evidence import AnalysisEvidence
from .plans import MigrationProposal, SourceRef


@dataclass(frozen=True, slots=True)
class TargetRevision:
    number: int
    content_hash: str
    ir: ArchitectureIR


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    analysis_id: uuid.UUID
    candidate: Candidate


@dataclass(frozen=True, slots=True)
class PlanningInputs:
    """Everything a plan is generated from — immutable, read before the engine runs."""

    request: MigrationRequest
    source: SourceRef
    source_ir: ArchitectureIR
    target: TargetRevision | TargetCandidate
    analyses: tuple[AnalysisEvidence, ...] = ()


class MigrationPlanner(Protocol):
    def plan(self, inputs: PlanningInputs) -> MigrationProposal:
        """The proposal for these exact inputs; refuses (a domain error) a target it cannot plan to."""
        ...

    def models(self) -> Mapping[str, int]:
        """The versions of every rule and model the planner uses now."""
        ...
