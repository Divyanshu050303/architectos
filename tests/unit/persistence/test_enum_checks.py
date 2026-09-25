"""CHECK constraints are generated from the domain enums, so the two cannot drift apart."""

from enum import StrEnum

import pytest
from sqlalchemy import CheckConstraint, Table

from core.domain.identity.enums import SessionRevocationReason, UserStatus
from core.domain.organizations.enums import Role
from core.domain.projects.enums import ProjectStatus
from persistence.models import (
    InvitationRecord,
    OrganizationMemberRecord,
    ProjectRecord,
    SessionRecord,
    UserRecord,
)


def check_sql(table: Table, name: str) -> str:
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == name:
            return str(constraint.sqltext)
    raise AssertionError(f"{table.name} has no check constraint {name}")


@pytest.mark.parametrize(
    ("table", "constraint", "enum"),
    [
        (UserRecord.__table__, "ck_users_status", UserStatus),
        (OrganizationMemberRecord.__table__, "ck_organization_members_role", Role),
        (InvitationRecord.__table__, "ck_invitations_role", Role),
        (SessionRecord.__table__, "ck_sessions_revoked_reason", SessionRevocationReason),
        (ProjectRecord.__table__, "ck_projects_status", ProjectStatus),
    ],
)
def test_check_constraint_lists_every_enum_value(table: Table, constraint: str, enum: type[StrEnum]) -> None:
    sql = check_sql(table, constraint)
    for member in enum:
        assert f"'{member.value}'" in sql
