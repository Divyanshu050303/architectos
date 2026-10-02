from core.domain.engine_results import InvalidEngineResult
from core.domain.errors import DomainError


class InvalidDriftRequest(DomainError):
    """``details`` = {"field", "reason"}."""

    code = "invalid_drift_request"
    message = "The drift analysis request is invalid."


class InvalidDriftResult(InvalidEngineResult):
    """A drift result with malformed parts (an engine bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_drift_result"
    message = "A drift result is malformed."


class DriftAnalysisNotFound(DomainError):
    """No such drift analysis in this project — also when it belongs to another project or tenant."""

    code = "drift_analysis_not_found"
    message = "Drift analysis not found."


class DriftItemNotFound(DomainError):
    """No such drift item in this project — also when it belongs to another project or tenant."""

    code = "drift_item_not_found"
    message = "Drift item not found."


class InvalidDriftTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_drift_transition"
    message = "The drift analysis cannot move to that status from its current status."


class InvalidReviewAction(DomainError):
    """``details`` = {"action", "status", "reason"?}: the action is not allowed in the item's status, or
    lacks what it needs (a reason, evidence, a link)."""

    code = "invalid_drift_review_action"
    message = "That review action is not allowed for this drift item now."
