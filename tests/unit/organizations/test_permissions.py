"""The role matrix, spelled out. Changing who may do what must change this table on purpose."""

import pytest

from core.domain.organizations.enums import Role
from core.domain.organizations.permissions import ROLE_PERMISSIONS, Permission, has_permission

OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER

EXPECTED: dict[Permission, set[Role]] = {
    Permission.ORGANIZATION_READ: {OWNER, ADMIN, MEMBER, VIEWER},
    Permission.ORGANIZATION_UPDATE: {OWNER, ADMIN},
    Permission.ORGANIZATION_DELETE: {OWNER},
    Permission.MEMBER_READ: {OWNER, ADMIN, MEMBER, VIEWER},
    Permission.MEMBER_INVITE: {OWNER, ADMIN},
    Permission.MEMBER_REMOVE: {OWNER, ADMIN},
    Permission.MEMBER_UPDATE_ROLE: {OWNER, ADMIN},
    Permission.AUDIT_READ: {OWNER, ADMIN},
    Permission.PROJECT_READ: {OWNER, ADMIN, MEMBER, VIEWER},
    Permission.PROJECT_CREATE: {OWNER, ADMIN, MEMBER},
    Permission.PROJECT_UPDATE: {OWNER, ADMIN, MEMBER},
    Permission.PROJECT_ARCHIVE: {OWNER, ADMIN},
    Permission.PROJECT_DELETE: {OWNER, ADMIN},
    Permission.REQUIREMENT_READ: {OWNER, ADMIN, MEMBER, VIEWER},
    Permission.REQUIREMENT_CREATE: {OWNER, ADMIN, MEMBER},
    Permission.REQUIREMENT_UPDATE: {OWNER, ADMIN, MEMBER},
    Permission.REQUIREMENT_DELETE: {OWNER, ADMIN, MEMBER},
    Permission.ARCHITECTURE_READ: {OWNER, ADMIN, MEMBER, VIEWER},
    Permission.ARCHITECTURE_CREATE: {OWNER, ADMIN, MEMBER},
    Permission.ARCHITECTURE_UPDATE: {OWNER, ADMIN, MEMBER},
    Permission.ARCHITECTURE_DELETE: {OWNER, ADMIN},
    Permission.ARCHITECTURE_VALIDATE: {OWNER, ADMIN, MEMBER},
    Permission.ARCHITECTURE_SIMULATE: {OWNER, ADMIN, MEMBER},
    Permission.ARCHITECTURE_EVOLVE: {OWNER, ADMIN, MEMBER},
}


def test_every_permission_is_specified() -> None:
    assert set(EXPECTED) == set(Permission)
    assert set(ROLE_PERMISSIONS) == set(Role)


@pytest.mark.parametrize("permission", list(Permission))
@pytest.mark.parametrize("role", list(Role))
def test_role_permission_matrix(role: Role, permission: Permission) -> None:
    assert has_permission(role, permission) is (role in EXPECTED[permission])


def test_viewers_can_never_write() -> None:
    writes = {p for p in Permission if not p.value.endswith(".read")}
    assert not (ROLE_PERMISSIONS[Role.VIEWER] & writes)


def test_roles_are_strictly_nested() -> None:
    assert (
        ROLE_PERMISSIONS[VIEWER]
        < ROLE_PERMISSIONS[MEMBER]
        < ROLE_PERMISSIONS[ADMIN]
        < ROLE_PERMISSIONS[OWNER]
    )
