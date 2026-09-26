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
    """No such architecture in this project (also when it was deleted, or belongs to another
    project: indistinguishable on purpose)."""

    code = "architecture_not_found"
    message = "Architecture not found."


class ArchitectureRevisionNotFound(DomainError):
    code = "architecture_revision_not_found"
    message = "Architecture revision not found."


class ArchitectureNameTaken(DomainError):
    code = "architecture_name_taken"
    message = "Another architecture of this project already has this name."


class InvalidArchitectureMetadata(DomainError):
    """``details`` = {"field": "name" | "description", "reason": ...}."""

    code = "invalid_architecture_metadata"
    message = "The architecture's name or description is invalid."


class ArchitectureArchived(DomainError):
    code = "architecture_archived"
    message = "This architecture is archived and read-only. Restore it to make changes."


class ArchitectureNotArchived(DomainError):
    code = "architecture_not_archived"
    message = "Archive the architecture before deleting it."


class ArchitectureVersionConflict(DomainError):
    """Optimistic concurrency: the edit was based on a revision that is no longer current.
    ``details`` = {"latest_version": n}."""

    code = "architecture_version_conflict"
    message = "The architecture changed since you loaded it. Reload it and apply your change again."


class InvalidLayout(DomainError):
    """``details`` = {"reason": ..., "node_id": ...}."""

    code = "invalid_architecture_layout"
    message = "The layout is invalid."
