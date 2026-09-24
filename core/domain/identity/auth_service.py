"""Authentication use cases. Phase 2: registration."""

import asyncio
from dataclasses import dataclass

from core.domain.unit_of_work import UnitOfWork

from .entities import NewUser, User
from .errors import EmailAlreadyRegistered
from .passwords import PasswordHasher, PasswordPolicy
from .value_objects import normalize_email, normalize_name


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """``user`` is None when the email was already registered. Callers must answer both cases
    identically, so registration cannot be used to discover which emails have accounts."""

    user: User | None


class AuthService:
    def __init__(self, uow: UnitOfWork, *, hasher: PasswordHasher, policy: PasswordPolicy) -> None:
        self._uow = uow
        self._hasher = hasher
        self._policy = policy

    async def register(self, *, email: str, password: str, name: str) -> RegistrationResult:
        normalized_email = normalize_email(email)
        clean_name = normalize_name(name)
        self._policy.validate(password, email=normalized_email)
        # Always hash, including for an email that turns out to be taken: the response time
        # must not reveal which case happened.
        password_hash = await asyncio.to_thread(self._hasher.hash, password)

        async with self._uow as uow:
            try:
                user = await uow.users.add(
                    NewUser(email=normalized_email, name=clean_name, password_hash=password_hash)
                )
            except EmailAlreadyRegistered:
                return RegistrationResult(user=None)
        return RegistrationResult(user=user)
