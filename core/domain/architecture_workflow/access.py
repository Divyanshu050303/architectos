"""The workflow's view of its project, always as the person who started it: the inputs every stage reads,
that person's current permissions, project knowledge, and the requirements engine. One implementation
of the controller's ``InputsLoader``, ``PermissionCheck``, ``KnowledgeAccess`` and
``RequirementAnalyzer`` ports, over the existing services and repositories — nothing is read with more
access than that person holds now.

Each call opens its own unit of work (``uows`` makes a fresh one), so a worker never reads a
membership, a requirement or a revision from an earlier transaction.
"""

from collections.abc import Callable

from core.domain.architecture_agent.agent_service import base_revision, load_pinned
from core.domain.architecture_diff.diff_service import capacity_inputs, cost_inputs
from core.domain.knowledge.ports import KnowledgeRetriever
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from core.domain.organizations.errors import PermissionDenied
from core.domain.organizations.permissions import Permission
from core.domain.projects.errors import ProjectNotFound
from core.domain.requirements.access import project_access
from core.domain.requirements.analysis_service import RequirementAnalysisService
from core.domain.requirements.errors import RequirementSetNotFound
from core.domain.unit_of_work import UnitOfWork

from .goals import WorkflowGoal
from .ports import RequirementAnalysis, WorkflowInputs
from .workflows import ArchitectureWorkflow


def goal_text(goal: WorkflowGoal) -> str:
    """The goal in the person's own words, as the requirements engine reads it."""
    parts = [goal.objective]
    for title, values in (
        ("Constraints", goal.constraints),
        ("Preferences", goal.preferences),
        ("Out of scope", goal.exclusions),
    ):
        if values:
            parts.append(f"{title}:\n" + "\n".join(f"- {v}" for v in values))
    if goal.context:
        parts.append(f"Context:\n{goal.context}")
    return "\n\n".join(parts)


class ProjectWorkflowAccess:
    def __init__(
        self,
        uows: Callable[[], UnitOfWork],
        knowledge: Callable[[], KnowledgeRetriever],
        requirements: Callable[[], RequirementAnalysisService],
    ) -> None:
        self._uows = uows
        self._knowledge = knowledge
        self._requirements = requirements

    async def load(self, workflow: ArchitectureWorkflow) -> WorkflowInputs:
        set_id, goal, project_id = workflow.requirement_set_id, workflow.goal, workflow.project_id
        if set_id is None:
            raise RequirementSetNotFound  # never asked before requirements are confirmed
        async with self._uows() as uow:
            access = await project_access(
                uow, project_id, workflow.requested_by_user_id, Permission.ARCHITECTURE_GENERATE
            )
            planning_input, requirements, missing = await load_pinned(uow, project_id, set_id)
            base = await base_revision(uow, project_id, goal.base) if goal.base else None
            compared = (goal.base.architecture_id if goal.base else None, None)
            capacity = await capacity_inputs(uow, project_id, goal.capacity_analysis_id, compared)
            cost = await cost_inputs(uow, access, goal.cost_analysis_id, compared)
        return WorkflowInputs(
            planning_input, requirements, access.project.policy,
            base.ir if base else None, base.content_hash if base else None,
            capacity, cost, goal.scenario, missing,
        )  # fmt: skip

    async def allowed(self, workflow: ArchitectureWorkflow, permission: Permission) -> bool:
        try:
            async with self._uows() as uow:
                await project_access(uow, workflow.project_id, workflow.requested_by_user_id, permission)
        except PermissionDenied, ProjectNotFound:  # removed from the organization, or a lower role now
            return False
        return True

    async def retrieve(self, workflow: ArchitectureWorkflow, query: RetrievalQuery) -> RetrievalResult:
        return await self._knowledge().retrieve(
            project_id=workflow.project_id, user_id=workflow.requested_by_user_id, query=query
        )

    async def analyze(self, workflow: ArchitectureWorkflow) -> RequirementAnalysis:
        stored = await self._requirements().analyze(
            project_id=workflow.project_id,
            user_id=workflow.requested_by_user_id,
            raw_input=goal_text(workflow.goal),
        )
        candidates = stored.result.get("candidates")
        return RequirementAnalysis(stored.id, len(candidates) if isinstance(candidates, list) else 0)
