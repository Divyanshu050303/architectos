from core.domain.errors import DomainError


class OrganizationNotFound(DomainError):
    """Also raised when the organization exists but the caller is not a member, so membership
    cannot be probed."""

    code = "organization_not_found"
    message = "Organization not found."


class PermissionDenied(DomainError):
    code = "permission_denied"
    message = "Your role in this organization does not allow this."


class EmailNotVerified(DomainError):
    code = "email_not_verified"
    message = "Verify your email address first."


class InvalidOrganizationName(DomainError):
    code = "invalid_organization_name"
    message = "Enter an organization name between 1 and 100 characters."
