from core.domain.errors import DomainError


class ArchitectureUnchanged(DomainError):
    """An edit that would produce a revision identical to its parent."""

    code = "architecture_unchanged"
    message = "These edits change nothing, so no new revision was created."


class InvalidRevision(DomainError):
    """``details`` = {"field": ..., "reason": ...} or {"reason": ...}."""

    code = "invalid_architecture_revision"
    message = "The revision is invalid."
