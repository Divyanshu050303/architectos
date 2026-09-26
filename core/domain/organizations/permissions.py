"""The single role -> permission matrix. Nothing else decides what a role may do.

Route dependencies (apps/api/dependencies/permissions.py) and services both ask ``has_permission``;
no code compares role names directly. Rules that depend on who the target is (e.g. an admin
cannot remove an owner) live in the membership policy, on top of this matrix.
"""

from enum import StrEnum

from .enums import Role


class Permission(StrEnum):
    ORGANIZATION_READ = "organization.read"
    ORGANIZATION_UPDATE = "organization.update"
    ORGANIZATION_DELETE = "organization.delete"

    MEMBER_READ = "member.read"
    MEMBER_INVITE = "member.invite"
    MEMBER_REMOVE = "member.remove"
    MEMBER_UPDATE_ROLE = "member.update_role"

    AUDIT_READ = "audit.read"

    PROJECT_READ = "project.read"
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    PROJECT_ARCHIVE = "project.archive"  # archive and restore
    PROJECT_DELETE = "project.delete"
    PROJECT_POLICY_UPDATE = "project.policy_update"  # the architecture policy validation enforces

    REQUIREMENT_READ = "requirement.read"
    REQUIREMENT_CREATE = "requirement.create"
    REQUIREMENT_UPDATE = "requirement.update"  # includes status changes
    REQUIREMENT_DELETE = "requirement.delete"
    REQUIREMENT_SET_CREATE = "requirement_set.create"  # reading sets needs requirement.read

    ARCHITECTURE_READ = "architecture.read"
    ARCHITECTURE_CREATE = "architecture.create"
    ARCHITECTURE_UPDATE = "architecture.update"
    ARCHITECTURE_DELETE = "architecture.delete"
    ARCHITECTURE_VALIDATE = "architecture.validate"
    ARCHITECTURE_ANALYZE = "architecture.analyze"  # capacity analyses
    ARCHITECTURE_SIMULATE = "architecture.simulate"
    ARCHITECTURE_EVOLVE = "architecture.evolve"


_VIEWER = frozenset(
    {
        Permission.ORGANIZATION_READ,
        Permission.MEMBER_READ,
        Permission.PROJECT_READ,
        Permission.REQUIREMENT_READ,
        Permission.ARCHITECTURE_READ,
    }
)

_MEMBER = _VIEWER | {
    Permission.PROJECT_CREATE,
    Permission.PROJECT_UPDATE,
    Permission.REQUIREMENT_CREATE,
    Permission.REQUIREMENT_UPDATE,
    Permission.REQUIREMENT_DELETE,
    Permission.REQUIREMENT_SET_CREATE,
    Permission.ARCHITECTURE_CREATE,
    Permission.ARCHITECTURE_UPDATE,
    Permission.ARCHITECTURE_VALIDATE,
    Permission.ARCHITECTURE_ANALYZE,
    Permission.ARCHITECTURE_SIMULATE,
    Permission.ARCHITECTURE_EVOLVE,
}

_ADMIN = _MEMBER | {
    Permission.ORGANIZATION_UPDATE,
    Permission.MEMBER_INVITE,
    Permission.MEMBER_REMOVE,
    Permission.MEMBER_UPDATE_ROLE,
    Permission.AUDIT_READ,
    Permission.PROJECT_ARCHIVE,
    Permission.PROJECT_DELETE,
    Permission.PROJECT_POLICY_UPDATE,
    Permission.ARCHITECTURE_DELETE,
}

_OWNER = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: _OWNER,
    Role.ADMIN: frozenset(_ADMIN),
    Role.MEMBER: frozenset(_MEMBER),
    Role.VIEWER: _VIEWER,
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
