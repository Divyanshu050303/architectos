"""What the workflow controller needs from the outside, so the domain never imports an engine, a model
or the database: the executors of the registry's actions, the store of workflows, candidates and steps,
and the check of a person's current permissions."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.architecture_diff.ports import CapacityInputs, CostInputs
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from core.domain.organizations.permissions import Permission
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.requirements.planning import PlanningInputV2
from core.domain.simulations.scenarios import Scenario

from .budget import WorkflowUsage
from .candidates import WorkflowCandidate
from .planner import Inputs, Next
from .steps import WorkflowStep
from .values import StepStatus
from .workflows import ArchitectureWorkflow, InputRequest


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    workflow: ArchitectureWorkflow
    candidates: tuple[WorkflowCandidate, ...] = ()  # in ordinal order
    steps: tuple[WorkflowStep, ...] = ()  # in execution order
    inputs: Inputs = field(default_factory=Inputs)


@dataclass(frozen=True, slots=True)
class StepContext:
    """What an executor is given: the workflow as it stands, the decision, and where it is in time."""

    workflow: ArchitectureWorkflow
    candidates: tuple[WorkflowCandidate, ...]
    decision: Next
    attempt: int
    now: datetime
    next_ordinal: int  # the ordinal a new candidate takes

    def candidate(self, candidate_id: uuid.UUID | None) -> WorkflowCandidate | None:
        return next((c for c in self.candidates if c.id == candidate_id), None)


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What an action did. Executors return failures as outcomes; they never move the workflow."""

    status: StepStatus
    outputs: dict[str, Any] = field(default_factory=dict)  # references only: ids, counts, codes
    usage: WorkflowUsage = field(default_factory=WorkflowUsage)  # model calls, tokens, retrievals…
    error: str | None = None
    retryable: bool = False
    note: str | None = None
    candidates: tuple[WorkflowCandidate, ...] = ()  # new ones, or existing ones with new reports/status
    requirement_analysis_id: uuid.UUID | None = None
    ask: InputRequest | None = None  # the workflow waits for a person
    selected: tuple[uuid.UUID, ...] = ()  # the review package (prepare_review only)
    limitations: tuple[str, ...] = ()


class StepExecutor(Protocol):
    async def execute(self, context: StepContext) -> StepOutcome: ...


class WorkflowStore(Protocol):
    async def snapshot(self, workflow_id: uuid.UUID) -> WorkflowSnapshot | None: ...

    async def commit(
        self,
        expected: ArchitectureWorkflow,
        workflow: ArchitectureWorkflow,
        candidates: tuple[WorkflowCandidate, ...],
        step: WorkflowStep | None,
    ) -> bool:
        """The workflow's new state, its new or changed candidates and the step, all or nothing — only
        if the stored workflow is still ``expected`` (still running, not cancelled, still held by this
        worker). False when it is not: nothing is written."""
        ...


class PermissionCheck(Protocol):
    async def allowed(self, workflow: ArchitectureWorkflow, permission: Permission) -> bool:
        """Whether the person who started the workflow holds ``permission`` in its project now."""
        ...


# --- what the executors read --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkflowInputs:
    """What the workflow's project gives every stage, read for the person who started it: the pinned
    requirement set (planning input and the same requirement versions), the policy, the base revision,
    and the inputs of the analyses the goal named. Absent inputs stay absent — never estimated."""

    planning_input: PlanningInputV2
    requirements: tuple[Requirement, ...]
    policy: ArchitecturePolicy = field(default_factory=ArchitecturePolicy)
    base: ArchitectureIR | None = None
    base_content_hash: str | None = None
    capacity: CapacityInputs | None = None
    cost: CostInputs | None = None
    scenario: Scenario | None = None
    missing: tuple[str, ...] = ()  # pinned requirements that can no longer be read


class InputsLoader(Protocol):
    async def load(self, workflow: ArchitectureWorkflow) -> WorkflowInputs: ...


@dataclass(frozen=True, slots=True)
class RequirementAnalysis:
    analysis_id: uuid.UUID
    candidates: int  # requirement candidates extracted, for a person to confirm
    usage: WorkflowUsage = field(default_factory=WorkflowUsage)


class RequirementAnalyzer(Protocol):
    async def analyze(self, workflow: ArchitectureWorkflow) -> RequirementAnalysis:
        """The goal's own words through the requirements engine, as a stored analysis of the project,
        for the person who started the workflow."""
        ...


class KnowledgeAccess(Protocol):
    async def retrieve(self, workflow: ArchitectureWorkflow, query: RetrievalQuery) -> RetrievalResult:
        """Project knowledge, authorized for the person who started the workflow."""
        ...
