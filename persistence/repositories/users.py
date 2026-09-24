import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.identity.entities import NewUser, User
from core.domain.identity.enums import UserStatus
from core.domain.identity.errors import EmailAlreadyRegistered
from persistence.models import UserRecord

from ._errors import violated_constraint

EMAIL_UNIQUE_INDEX = "uq_users_email_lower"


def to_user(record: UserRecord) -> User:
    return User(
        id=record.id,
        email=record.email,
        name=record.name,
        password_hash=record.password_hash,
        avatar_url=record.avatar_url,
        email_verified_at=record.email_verified_at,
        status=UserStatus(record.status),
        created_at=record.created_at,
        updated_at=record.updated_at,
        deleted_at=record.deleted_at,
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        record = await self._session.get(UserRecord, user_id)
        return to_user(record) if record else None

    async def get_by_email(self, email: str) -> User | None:
        # lower(email) matches the functional unique index, so this is an index lookup.
        record = await self._session.scalar(select(UserRecord).where(func.lower(UserRecord.email) == email))
        return to_user(record) if record else None

    async def get_by_email_for_update(self, email: str) -> User | None:
        record = await self._session.scalar(
            select(UserRecord).where(func.lower(UserRecord.email) == email).with_for_update()
        )
        return to_user(record) if record else None

    async def mark_email_verified(self, user_id: uuid.UUID, at: datetime) -> None:
        await self._session.execute(
            update(UserRecord)
            .where(UserRecord.id == user_id, UserRecord.email_verified_at.is_(None))
            .values(email_verified_at=at, updated_at=func.now())
        )

    async def add(self, user: NewUser) -> User:
        record = UserRecord(email=user.email, name=user.name, password_hash=user.password_hash)
        try:
            # A savepoint confines a duplicate-email failure to this insert, so the caller's
            # transaction can continue (and commit other work) after handling it.
            async with self._session.begin_nested():
                self._session.add(record)
                await self._session.flush()
        except IntegrityError as error:
            if violated_constraint(error) == EMAIL_UNIQUE_INDEX:
                raise EmailAlreadyRegistered from None
            raise
        # created_at/updated_at/status are set by the database.
        await self._session.refresh(record)
        return to_user(record)
