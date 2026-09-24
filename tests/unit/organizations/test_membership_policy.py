"""Every (actor, target, new role, self) combination against a spec written independently of the code."""

import itertools

import pytest

from core.domain.errors import DomainError
from core.domain.organizations.enums import Role
from core.domain.organizations.errors import (
    CannotChangeOwnRole,
    LastOwner,
    PermissionDenied,
    RoleNotManageable,
)
from core.domain.organizations.membership_policy import check_removal, check_role_change

OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER
BELOW = {OWNER: {OWNER, ADMIN, MEMBER, VIEWER}, ADMIN: {MEMBER, VIEWER}, MEMBER: set(), VIEWER: set()}


def expected_role_change(
    actor: Role, target: Role, new: Role, is_self: bool, owners: int
) -> type[DomainError] | None:
    if actor not in {OWNER, ADMIN}:
        return PermissionDenied
    if is_self:
        return CannotChangeOwnRole
    if target not in BELOW[actor] or new not in BELOW[actor]:
        return RoleNotManageable
    if target is OWNER and new is not OWNER and owners <= 1:
        return LastOwner
    return None


def expected_removal(actor: Role, target: Role, is_self: bool, owners: int) -> type[DomainError] | None:
    if not is_self:
        if actor not in {OWNER, ADMIN}:
            return PermissionDenied
        if target not in BELOW[actor]:
            return RoleNotManageable
    if target is OWNER and owners <= 1:
        return LastOwner
    return None


ROLES = list(Role)


@pytest.mark.parametrize(
    ("actor", "target", "new", "is_self", "owners"),
    [
        c
        for c in itertools.product(ROLES, ROLES, ROLES, [False, True], [1, 2])
        if not c[3] or c[0] is c[1]  # yourself always has your own role
    ],
)
def test_role_changes_follow_the_spec(
    actor: Role, target: Role, new: Role, is_self: bool, owners: int
) -> None:
    expected = expected_role_change(actor, target, new, is_self, owners)
    if expected is None:
        check_role_change(actor=actor, target=target, new_role=new, is_self=is_self, owner_count=owners)
    else:
        with pytest.raises(expected):
            check_role_change(actor=actor, target=target, new_role=new, is_self=is_self, owner_count=owners)


@pytest.mark.parametrize(
    ("actor", "target", "is_self", "owners"),
    [c for c in itertools.product(ROLES, ROLES, [False, True], [1, 2]) if not c[2] or c[0] is c[1]],
)
def test_removals_follow_the_spec(actor: Role, target: Role, is_self: bool, owners: int) -> None:
    expected = expected_removal(actor, target, is_self, owners)
    if expected is None:
        check_removal(actor=actor, target=target, is_self=is_self, owner_count=owners)
    else:
        with pytest.raises(expected):
            check_removal(actor=actor, target=target, is_self=is_self, owner_count=owners)


# The spec's named invariants, stated directly.


def test_viewer_cannot_change_roles_or_remove_anyone() -> None:
    with pytest.raises(PermissionDenied):
        check_role_change(actor=VIEWER, target=VIEWER, new_role=MEMBER, is_self=False, owner_count=1)
    with pytest.raises(PermissionDenied):
        check_removal(actor=VIEWER, target=VIEWER, is_self=False, owner_count=1)


def test_member_cannot_change_roles_or_remove_admins() -> None:
    with pytest.raises(PermissionDenied):
        check_role_change(actor=MEMBER, target=VIEWER, new_role=MEMBER, is_self=False, owner_count=1)
    with pytest.raises(PermissionDenied):
        check_removal(actor=MEMBER, target=ADMIN, is_self=False, owner_count=1)


def test_admin_cannot_do_owner_only_things() -> None:
    with pytest.raises(RoleNotManageable):  # grant ownership
        check_role_change(actor=ADMIN, target=MEMBER, new_role=OWNER, is_self=False, owner_count=1)
    with pytest.raises(RoleNotManageable):  # demote an owner
        check_role_change(actor=ADMIN, target=OWNER, new_role=MEMBER, is_self=False, owner_count=2)
    with pytest.raises(RoleNotManageable):  # remove an owner
        check_removal(actor=ADMIN, target=OWNER, is_self=False, owner_count=2)


def test_nobody_changes_their_own_role() -> None:
    for role in (OWNER, ADMIN):
        with pytest.raises(CannotChangeOwnRole):
            check_role_change(actor=role, target=role, new_role=VIEWER, is_self=True, owner_count=2)


def test_the_final_owner_can_neither_be_demoted_nor_removed_nor_leave() -> None:
    with pytest.raises(LastOwner):
        check_removal(actor=OWNER, target=OWNER, is_self=True, owner_count=1)
    # (Demoting or removing another owner implies two owners, so the last-owner guard is
    # exercised through counts that are stale, e.g. under concurrency; see the race tests.)
    with pytest.raises(LastOwner):
        check_role_change(actor=OWNER, target=OWNER, new_role=ADMIN, is_self=False, owner_count=1)
    with pytest.raises(LastOwner):
        check_removal(actor=OWNER, target=OWNER, is_self=False, owner_count=1)


def test_members_and_viewers_can_leave() -> None:
    for role in (ADMIN, MEMBER, VIEWER):
        check_removal(actor=role, target=role, is_self=True, owner_count=1)
