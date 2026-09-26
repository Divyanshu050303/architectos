from core.domain.errors import DomainError


class ArchitectureUnchanged(DomainError):
    """An edit that would produce a revision identical to its parent."""

    code = "architecture_unchanged"
    message = "These edits change nothing, so no new revision was created."


class InvalidRevision(DomainError):
    """``details`` = {"field": ..., "reason": ...} or {"reason": ...}."""

    code = "invalid_architecture_revision"
    message = "The revision is invalid."


class ArchitectureNotFound(DomainError):
    """The project has no architecture (or the project is not visible to the caller)."""

    code = "architecture_not_found"
    message = "This project has no architecture yet."


class ArchitectureRevisionNotFound(DomainError):
    code = "architecture_revision_not_found"
    message = "Architecture revision not found."


class ArchitectureAlreadyExists(DomainError):
    code = "architecture_already_exists"
    message = "This project already has an architecture; change it with a new revision."


class ArchitectureVersionConflict(DomainError):
    """Optimistic concurrency: the edit was based on a revision that is no longer current.
    ``details`` = {"latest_version": n}."""

    code = "architecture_version_conflict"
    message = "The architecture changed since you loaded it. Reload it and apply your change again."


class InvalidLayout(DomainError):
    """``details`` = {"reason": ..., "node_id": ...}."""

    code = "invalid_architecture_layout"
    message = "The layout is invalid."
