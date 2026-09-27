from core.domain.errors import DomainError


class InvalidDecision(DomainError):
    """``details`` = {"field", "reason"}."""

    code = "invalid_decision"
    message = "The decision is invalid."


class DecisionNotFound(DomainError):
    """No such decision in this project (also when it belongs to another project or tenant:
    indistinguishable on purpose)."""

    code = "decision_not_found"
    message = "Decision not found."


class InvalidDecisionTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_decision_transition"
    message = "The decision cannot move to that status from its current status."
