from core.domain.errors import DomainError


class RequirementNotFound(DomainError):
    """Also when the requirement belongs to another project, or was deleted."""

    code = "requirement_not_found"
    message = "Requirement not found."


class RequirementVersionNotFound(DomainError):
    code = "requirement_version_not_found"
    message = "Requirement version not found."


class InvalidRequirement(DomainError):
    """``details`` = {"field": <snake_case path, e.g. "structured_data.value">, "reason": <code>}."""

    code = "invalid_requirement"
    message = "The requirement is invalid."


class RequirementVersionConflict(DomainError):
    """Optimistic concurrency: the client edited a version that is no longer current.
    ``details`` = {"current_version": n}."""

    code = "requirement_version_conflict"
    message = "This requirement changed since you loaded it. Reload it and apply your change again."


class InvalidStatusTransition(DomainError):
    """``details`` = {"from": status, "to": status}."""

    code = "invalid_status_transition"
    message = "The requirement cannot move to that status from its current status."


class RequirementLocked(DomainError):
    """``details`` = {"status": status}."""

    code = "requirement_locked"
    message = (
        "This requirement cannot be edited in its current status. Reopen a satisfied requirement "
        "(set it to active) first; deprecated requirements never change."
    )


class ChangeReasonRequired(DomainError):
    code = "change_reason_required"
    message = "Explain why an active or satisfied requirement is changing."
