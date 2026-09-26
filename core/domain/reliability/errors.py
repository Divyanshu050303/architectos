from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidReliabilityRequest(DomainError):
    """``details`` = {"field", "reason"}."""

    code = "invalid_reliability_request"
    message = "The reliability analysis request is invalid."


class InvalidReliabilityResult(InvalidEngineResult):
    """A finding, estimate or result with malformed fields (a model bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_reliability_result"
    message = "A reliability result is malformed."


class ReliabilityAnalysisNotFound(DomainError):
    """No such analysis for this architecture (also when it belongs to another architecture,
    project or tenant: indistinguishable on purpose)."""

    code = "reliability_analysis_not_found"
    message = "Reliability analysis not found."


class InvalidReliabilityAnalysisTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_reliability_analysis_transition"
    message = "The reliability analysis cannot move to that status from its current status."
