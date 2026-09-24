import uuid
from datetime import datetime

from core.domain.identity.entities import User

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
