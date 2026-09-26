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


class InvalidValidationConfig(DomainError):
    """A validation request asks for something the engine does not offer: an unknown profile or
    rule, a parameter it does not accept, or a change to a mandatory rule.
    ``details`` = {"reason": ..., "rule_id": ..., "parameter": ...}."""

    code = "invalid_validation_config"
    message = "The validation configuration is invalid."
