import uuid
from dataclasses import dataclass
from datetime import datetime

from .enums import UserStatus


@dataclass(frozen=True, slots=True)
class NewUser:
    """A validated registration, before it has an id or timestamps."""

    email: str
    name: str
    password_hash: str


@dataclass(frozen=True, slots=True)
class User:
    id: uuid.UUID
    email: str
    name: str
    password_hash: str
    avatar_url: str | None
    email_verified_at: datetime | None
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def can_sign_in(self) -> bool:
        return self.status is UserStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class SingleUseToken:
    """An emailed token (verification or password reset), as stored: only its hash."""

    id: uuid.UUID
    user_id: uuid.UUID
    token_hash: bytes
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    @property
    def is_spent(self) -> bool:
        return self.consumed_at is not None or self.revoked_at is not None
