from core.domain.errors import DomainError


class InvalidFinding(DomainError):
    """A finding or verdict with missing or malformed fields (a rule bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_validation_finding"
    message = "A validation finding is malformed."


class InvalidRunTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_validation_run_transition"
    message = "The validation run cannot move to that status from its current status."
