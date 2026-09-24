import uuid
from datetime import datetime

from pydantic import Field

from core.domain.identity.entities import Session, User

from .common import ApiModel


class UserResponse(ApiModel):
    """The only user representation the API returns. Security fields (password hash, status
    internals, session data) are never part of it."""

    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    email_verified: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            email_verified=user.is_email_verified,
            created_at=user.created_at,
        )


class SessionItem(ApiModel):
    """A signed-in device, as shown to its owner."""

    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    user_agent: str | None
    ip_address: str | None
    current: bool = Field(description="True for the session making this request.")

    @classmethod
    def from_session(cls, session: Session, *, current_session_id: uuid.UUID) -> SessionItem:
        return cls(
            id=session.id,
            created_at=session.created_at,
            last_used_at=session.last_used_at,
            expires_at=session.expires_at,
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            current=session.id == current_session_id,
        )


class SessionList(ApiModel):
    sessions: list[SessionItem]
