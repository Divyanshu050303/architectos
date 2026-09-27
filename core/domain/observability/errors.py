from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidObservabilityRequest(DomainError):
    """``details`` = {"field", "reason"}."""

    code = "invalid_observability_request"
    message = "The observability analysis request is invalid."


class InvalidObservabilityResult(InvalidEngineResult):
    """A finding, check or result with malformed fields (an analyzer bug, or a corrupted record),
    including evidence that would show a secret. ``details`` = {"fields": [...]}."""

    code = "invalid_observability_result"
    message = "An observability result is malformed."


class ObservabilityAnalysisNotFound(DomainError):
    """No such analysis for this architecture (also when it belongs to another architecture,
    project or tenant: indistinguishable on purpose)."""

    code = "observability_analysis_not_found"
    message = "Observability analysis not found."


class InvalidObservabilityAnalysisTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_observability_analysis_transition"
    message = "The observability analysis cannot move to that status from its current status."
