import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AuditAction(StrEnum):
    USER_REGISTERED = "user.registered"
    USER_EMAIL_VERIFIED = "user.email_verified"
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    USER_PASSWORD_RESET = "user.password_reset"  # noqa: S105 — an event name
    USER_PASSWORD_CHANGED = "user.password_changed"  # noqa: S105 — an event name
    USER_PROFILE_UPDATED = "user.profile_updated"
    USER_DELETED = "user.deleted"
    SESSION_REVOKED = "session.revoked"
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
    MEMBER_INVITED = "member.invited"
    MEMBER_INVITATION_REVOKED = "member.invitation_revoked"
    MEMBER_INVITATION_ACCEPTED = "member.invitation_accepted"
    MEMBER_ROLE_CHANGED = "member.role_changed"
    MEMBER_REMOVED = "member.removed"
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    PROJECT_DELETED = "project.deleted"
    REQUIREMENT_CREATED = "requirement.created"
    REQUIREMENT_UPDATED = "requirement.updated"  # content (anything but status) changed
    REQUIREMENT_STATUS_CHANGED = "requirement.status_changed"
    REQUIREMENT_VERSION_CREATED = "requirement.version_created"  # every revision
    REQUIREMENT_DELETED = "requirement.deleted"
    REQUIREMENT_SET_CREATED = "requirement_set.created"


# Defense in depth: metadata keys that look like secrets are refused outright.
_SECRET_KEY = re.compile(r"password|token|secret|hash|cookie|authorization", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """What a service records. Request origin (IP, user agent) is attached by the unit of work."""

    action: AuditAction
    actor_user_id: uuid.UUID | None
    organization_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        leaked = [key for key in self.metadata if _SECRET_KEY.search(key)]
        if leaked:
            msg = f"audit metadata must not contain secrets: {leaked}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: uuid.UUID
    organization_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    metadata: dict[str, Any]
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditCursor:
    """Position in a newest-first listing: the last entry of the previous page."""

    created_at: datetime
    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AuditPage:
    entries: list[AuditEntry]
    next_cursor: AuditCursor | None
