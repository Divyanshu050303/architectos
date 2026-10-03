from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidWorkflowRequest(DomainError):
    """``details`` = {"field", "reason"}: a goal, budget, input or decision that cannot be accepted."""

    code = "invalid_workflow_request"
    message = "The architecture workflow request is invalid."


class InvalidWorkflowRecord(InvalidEngineResult):
    """A workflow, step or candidate with malformed parts (a controller bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_workflow_record"
    message = "An architecture workflow record is malformed."


class WorkflowNotFound(DomainError):
    """No such workflow in this project — also when it belongs to another project or tenant."""

    code = "architecture_workflow_not_found"
    message = "Architecture workflow not found."


class WorkflowCandidateNotFound(DomainError):
    """No such candidate in this workflow — also when the workflow is another project's."""

    code = "workflow_candidate_not_found"
    message = "Workflow candidate not found."


class InvalidWorkflowTransition(DomainError):
    """``details`` = {"from", "to"}: the workflow cannot move to that status now (e.g. approving a
    workflow that is still running)."""

    code = "invalid_workflow_transition"
    message = "The architecture workflow cannot move to that status now."


class ToolNotAllowed(DomainError):
    """``details`` = {"action", "reason"}: an action outside the registry, outside its allowed stages or
    statuses, beyond its limit, or with a side effect the workflow may never perform on its own."""

    code = "workflow_tool_not_allowed"
    message = "The workflow may not perform that action."


class CandidateNotApprovable(DomainError):
    """``details`` = {"reason"}: the candidate cannot become a revision — not selected for review,
    changed since review, blocked by validation, or its base is no longer current (``stale_candidate``)."""

    code = "workflow_candidate_not_approvable"
    message = "The workflow candidate cannot be approved."
