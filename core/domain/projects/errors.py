from core.domain.errors import DomainError


class ProjectNotFound(DomainError):
    """Also when the project exists but the caller is not a member of its organization, or it was
    deleted: existence is never revealed."""

    code = "project_not_found"
    message = "Project not found."


class ProjectArchived(DomainError):
    code = "project_archived"
    message = "This project is archived and read-only. Restore it to make changes."


class ProjectNotArchived(DomainError):
    code = "project_not_archived"
    message = "Archive the project before deleting it."


class ProjectSlugTaken(DomainError):
    code = "project_slug_taken"
    message = "Another project in this organization already uses this slug. Choose a different one."


class InvalidProjectName(DomainError):
    code = "invalid_project_name"
    message = "Enter a project name between 1 and 100 characters."


class InvalidProjectDescription(DomainError):
    code = "invalid_project_description"
    message = "Keep the description under 2,000 characters."


class InvalidProjectSlug(DomainError):
    code = "invalid_project_slug"
    message = "Use 1 to 63 lowercase letters, digits and single hyphens, e.g. food-delivery."


class InvalidProjectSettings(DomainError):
    """``details`` names the offending setting."""

    code = "invalid_project_settings"
    message = "The project settings are invalid."
