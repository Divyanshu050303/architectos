from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidSecurityRequest(DomainError):
    """``details`` = {"field", "reason"}."""

    code = "invalid_security_request"
    message = "The security analysis request is invalid."


class InvalidSecurityResult(InvalidEngineResult):
    """A finding or result with malformed fields (an analyzer bug, or a corrupted record), including
    evidence that would show a secret. ``details`` = {"fields": [...]}."""

    code = "invalid_security_result"
    message = "A security result is malformed."


class SecurityAnalysisNotFound(DomainError):
    """No such analysis for this architecture (also when it belongs to another architecture,
    project or tenant: indistinguishable on purpose)."""

    code = "security_analysis_not_found"
    message = "Security analysis not found."


class InvalidSecurityAnalysisTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_security_analysis_transition"
    message = "The security analysis cannot move to that status from its current status."
