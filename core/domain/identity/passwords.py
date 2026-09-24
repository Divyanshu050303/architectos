"""The single password policy and hashing implementation. Nothing else validates or hashes passwords.

Policy (NIST SP 800-63B / OWASP ASVS 2.1): length, not a known-breached password, not built
from the account's own email. No composition rules. Passwords are NFKC-normalized before
checking and hashing, so visually identical input always produces the same hash.

Hashing: Argon2id with argon2-cffi's defaults (RFC 9106 low-memory profile). Hashes embed their
parameters, so ``needs_rehash`` lets login upgrade old hashes when parameters change.
"""

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache, cached_property
from importlib.resources import files

from argon2 import PasswordHasher as _Argon2
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .errors import WeakPassword
from .tokens import generate_token

ABSOLUTE_MAX_LENGTH = 128
MIN_PERSONAL_FRAGMENT = 4


class PasswordIssue(StrEnum):
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    BLANK = "blank"
    COMMON = "common"
    CONTAINS_EMAIL = "contains_email"


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


@cache
def _common_passwords() -> frozenset[str]:
    text = files("core.domain.identity").joinpath("data/common_passwords.txt").read_text("utf-8")
    return frozenset(line for line in text.splitlines() if line)


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    min_length: int = 12
    max_length: int = ABSOLUTE_MAX_LENGTH
    common_passwords: frozenset[str] = field(default_factory=_common_passwords, repr=False)

    def __post_init__(self) -> None:
        if not 8 <= self.min_length <= self.max_length <= ABSOLUTE_MAX_LENGTH:
            msg = f"password lengths must satisfy 8 <= min <= max <= {ABSOLUTE_MAX_LENGTH}"
            raise ValueError(msg)

    def issues(self, password: str, *, email: str | None = None) -> list[PasswordIssue]:
        candidate = normalize_password(password)
        found: list[PasswordIssue] = []
        if len(candidate) < self.min_length:
            found.append(PasswordIssue.TOO_SHORT)
        if len(candidate) > self.max_length:
            found.append(PasswordIssue.TOO_LONG)
        if not candidate.strip():
            found.append(PasswordIssue.BLANK)
        folded = candidate.casefold()
        if folded in self.common_passwords:
            found.append(PasswordIssue.COMMON)
        if email and _contains_email(folded, email.casefold()):
            found.append(PasswordIssue.CONTAINS_EMAIL)
        return found

    def validate(self, password: str, *, email: str | None = None) -> None:
        issues = self.issues(password, email=email)
        if issues:
            raise WeakPassword(details={"reasons": [issue.value for issue in issues]})


def _contains_email(password: str, email: str) -> bool:
    local_part = email.partition("@")[0]
    return email in password or (len(local_part) >= MIN_PERSONAL_FRAGMENT and local_part in password)


class PasswordHasher:
    """Argon2id. Hashing is CPU- and memory-heavy (~64 MiB); async callers run it in a thread."""

    def __init__(self) -> None:
        self._argon2 = _Argon2(type=Type.ID)

    def hash(self, password: str) -> str:
        return self._argon2.hash(normalize_password(password))

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._argon2.verify(password_hash, normalize_password(password))
        except VerifyMismatchError:
            return False
        except InvalidHashError, VerificationError:
            # A corrupt or foreign hash must never authenticate anyone.
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._argon2.check_needs_rehash(password_hash)

    @cached_property
    def dummy_hash(self) -> str:
        """A valid hash of a random secret, for verifying against when no account exists, so that
        "unknown email" costs the same Argon2 work as "wrong password". Computed once."""
        return self.hash(generate_token())
