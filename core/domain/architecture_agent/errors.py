from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidAgentRequest(DomainError):
    """``details`` = {"field", "reason"}: a request, answer or acceptance that cannot be accepted."""

    code = "invalid_agent_request"
    message = "The architecture agent request is invalid."


class InvalidAgentRecord(InvalidEngineResult):
    """A proposal, candidate, report or run with malformed parts (a pipeline bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_agent_record"
    message = "An architecture agent record is malformed."


class AgentRunNotFound(DomainError):
    """No such run in this project — also when it belongs to another project or tenant."""

    code = "agent_run_not_found"
    message = "Architecture agent run not found."


class InvalidAgentTransition(DomainError):
    """``details`` = {"from", "to"}: the run cannot move to that status now (e.g. accepting a failed run)."""

    code = "invalid_agent_transition"
    message = "The architecture agent run cannot move to that status now."


class CandidateNotAcceptable(DomainError):
    """``details`` = {"reason"}: the candidate cannot become a revision (validation blocks it, it changed
    since it was reviewed, or the architecture moved on)."""

    code = "agent_candidate_not_acceptable"
    message = "The candidate architecture cannot be accepted."
