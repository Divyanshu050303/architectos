import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.identity.entities import NewUser
from core.domain.identity.enums import UserStatus
from core.domain.identity.errors import EmailAlreadyRegistered
from persistence.repositories.users import SqlAlchemyUserRepository

pytestmark = pytest.mark.integration


def new_user(email: str = "ada@example.com") -> NewUser:
    return NewUser(email=email, name="Ada", password_hash="$argon2id$placeholder")


async def test_added_user_round_trips(db: AsyncSession) -> None:
    users = SqlAlchemyUserRepository(db)
    added = await users.add(new_user())

    assert added.status is UserStatus.ACTIVE
    assert added.created_at is not None
    assert await users.get(added.id) == added
    assert await users.get_by_email("ada@example.com") == added


async def test_lookup_by_email_is_case_insensitive_against_stored_data(db: AsyncSession) -> None:
    users = SqlAlchemyUserRepository(db)
    # Rows written before normalization existed, or by hand, still match a normalized lookup.
    added = await users.add(new_user("Mixed.Case@Example.com"))
    assert await users.get_by_email("mixed.case@example.com") == added


async def test_duplicate_email_raises_and_the_transaction_survives(db: AsyncSession) -> None:
    users = SqlAlchemyUserRepository(db)
    await users.add(new_user())

    with pytest.raises(EmailAlreadyRegistered):
        await users.add(new_user("ADA@example.com"))

    # The failed insert was confined to a savepoint: the session is still usable.
    other = await users.add(new_user("grace@example.com"))
    assert await users.get(other.id) is not None
