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


class MemberNotFound(DomainError):
    """Also for a member id that belongs to another organization."""

    code = "member_not_found"
    message = "Member not found."


class CannotChangeOwnRole(DomainError):
    code = "cannot_change_own_role"
    message = "You cannot change your own role. Ask another owner or admin."


class RoleNotManageable(DomainError):
    """The target, or the role being assigned, is not below the caller's own role."""

    code = "role_not_manageable"
    message = "You can only manage members, and assign roles, below your own role."


class LastOwner(DomainError):
    code = "last_owner"
    message = "An organization must keep at least one owner. Make someone else an owner first."


class SoleOwnerOfOrganization(DomainError):
    """``details`` lists the organizations that would be left without an owner."""

    code = "sole_owner_of_organization"
    message = "You are the only owner of an organization that has other members. Transfer ownership first."
