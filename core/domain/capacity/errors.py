from core.domain.errors import DomainError


class InvalidQuantity(DomainError):
    """A number or unit the capacity engine cannot use. ``details`` = {"field", "reason"}."""

    code = "invalid_capacity_quantity"
    message = "A capacity quantity is invalid."


class InvalidWorkload(DomainError):
    """A workload profile with missing, contradictory or out-of-range values.
    ``details`` = {"field", "reason"}."""

    code = "invalid_workload_profile"
    message = "The workload profile is invalid."


class InvalidCapacityResult(DomainError):
    """A capacity result or estimate with malformed fields (a model bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_capacity_result"
    message = "A capacity result is malformed."


class InvalidAnalysisTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_capacity_analysis_transition"
    message = "The capacity analysis cannot move to that status from its current status."
