"""Who may change whose membership. Pure functions over roles: exhaustively unit-tested.

The permission matrix says whether a role may manage members at all; this module adds the rules
that depend on the target:
- Owners may manage anyone, including other owners, and may grant ownership.
- Anyone else may act only on members ranked strictly below them and assign only roles strictly
  below their own. (An admin manages members and viewers; not other admins, not owners.)
- Nobody changes their own role.
- Leaving (removing yourself) needs no permission.
- An organization always keeps at least one owner.
"""

from .enums import Role
from .errors import CannotChangeOwnRole, LastOwner, PermissionDenied, RoleNotManageable
from .permissions import Permission, has_permission

RANK: dict[Role, int] = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}


def _outranks(actor: Role, other: Role) -> bool:
    return actor is Role.OWNER or RANK[actor] > RANK[other]


def check_role_change(*, actor: Role, target: Role, new_role: Role, is_self: bool, owner_count: int) -> None:
    if not has_permission(actor, Permission.MEMBER_UPDATE_ROLE):
        raise PermissionDenied
    if is_self:
        raise CannotChangeOwnRole
    if not (_outranks(actor, target) and _outranks(actor, new_role)):
        raise RoleNotManageable
    if target is Role.OWNER and new_role is not Role.OWNER and owner_count <= 1:
        raise LastOwner


def check_removal(*, actor: Role, target: Role, is_self: bool, owner_count: int) -> None:
    if not is_self:
        if not has_permission(actor, Permission.MEMBER_REMOVE):
            raise PermissionDenied
        if not _outranks(actor, target):
            raise RoleNotManageable
    if target is Role.OWNER and owner_count <= 1:
        raise LastOwner
