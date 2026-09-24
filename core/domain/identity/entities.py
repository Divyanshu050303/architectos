import uuid
from dataclasses import dataclass
from datetime import datetime

from .enums import SessionRevocationReason, UserStatus


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


@dataclass(frozen=True, slots=True)
class NewSession:
    user_id: uuid.UUID
    refresh_token_hash: bytes
    expires_at: datetime
    created_at: datetime
    user_agent: str | None
    ip_address: str | None


@dataclass(frozen=True, slots=True)
class Session:
    id: uuid.UUID
    user_id: uuid.UUID
    refresh_token_hash: bytes
    previous_refresh_token_hash: bytes | None
    refreshed_at: datetime | None
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_reason: SessionRevocationReason | None
    user_agent: str | None
    ip_address: str | None
    created_at: datetime

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and now < self.expires_at
