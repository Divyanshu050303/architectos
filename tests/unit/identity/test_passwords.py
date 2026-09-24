import pytest

from core.domain.identity.errors import WeakPassword
from core.domain.identity.passwords import PasswordHasher, PasswordIssue, PasswordPolicy

STRONG = "correct horse battery staple"


@pytest.fixture(scope="module")
def policy() -> PasswordPolicy:
    return PasswordPolicy(min_length=12)


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


def test_strong_password_passes(policy: PasswordPolicy) -> None:
    assert policy.issues(STRONG, email="ada@example.com") == []
    policy.validate(STRONG, email="ada@example.com")


@pytest.mark.parametrize(
    ("password", "issue"),
    [
        ("short", PasswordIssue.TOO_SHORT),
        ("x" * 129, PasswordIssue.TOO_LONG),
        (" " * 12, PasswordIssue.BLANK),
        ("password1234", PasswordIssue.COMMON),
        ("PassWord1234", PasswordIssue.COMMON),  # matching is case-insensitive
    ],
)
def test_weak_passwords_are_reported(policy: PasswordPolicy, password: str, issue: PasswordIssue) -> None:
    assert issue in policy.issues(password)


def test_password_built_from_the_email_is_rejected(policy: PasswordPolicy) -> None:
    assert PasswordIssue.CONTAINS_EMAIL in policy.issues("lovelace-2024-!", email="lovelace@example.com")
    assert PasswordIssue.CONTAINS_EMAIL in policy.issues("xx ada@example.com xx", email="ada@example.com")
    # A very short local part is not treated as personal information.
    assert PasswordIssue.CONTAINS_EMAIL not in policy.issues("adamant river stone", email="ada@example.com")


def test_validate_reports_every_failed_rule(policy: PasswordPolicy) -> None:
    with pytest.raises(WeakPassword) as raised:
        policy.validate("   ")
    assert raised.value.details == {"reasons": ["too_short", "blank"]}


def test_minimum_length_is_configurable_within_bounds() -> None:
    assert PasswordIssue.TOO_SHORT not in PasswordPolicy(min_length=8).issues("zq8#Lm2!")
    with pytest.raises(ValueError, match="password lengths"):
        PasswordPolicy(min_length=6)


def test_length_counts_characters_after_nfkc_normalization(policy: PasswordPolicy) -> None:
    # "ﬁ" (one code point) normalizes to "fi" (two).
    assert PasswordIssue.TOO_SHORT not in policy.issues("ﬁ" * 6)


def test_hash_is_argon2id_and_never_the_password(hasher: PasswordHasher) -> None:
    password_hash = hasher.hash(STRONG)
    assert password_hash.startswith("$argon2id$")
    assert STRONG not in password_hash


def test_verification_accepts_the_password_and_rejects_others(hasher: PasswordHasher) -> None:
    password_hash = hasher.hash(STRONG)
    assert hasher.verify(password_hash, STRONG)
    assert not hasher.verify(password_hash, STRONG + "!")
    assert not hasher.verify(password_hash, "")


def test_equivalent_unicode_forms_verify(hasher: PasswordHasher) -> None:
    composed, decomposed = "café au lait 42", "café au lait 42"
    assert hasher.verify(hasher.hash(composed), decomposed)


def test_same_password_hashes_differently_each_time(hasher: PasswordHasher) -> None:
    first, second = hasher.hash(STRONG), hasher.hash(STRONG)
    assert first != second  # random salt per hash
    assert hasher.verify(first, STRONG)
    assert hasher.verify(second, STRONG)


@pytest.mark.parametrize(
    "bad_hash", ["", "not-a-hash", "$argon2id$v=19$garbage", "$2b$12$abcdefghijklmnopqrstuv"]
)
def test_corrupt_hashes_never_verify(hasher: PasswordHasher, bad_hash: str) -> None:
    assert not hasher.verify(bad_hash, STRONG)


def test_current_hashes_do_not_need_rehash(hasher: PasswordHasher) -> None:
    assert not hasher.needs_rehash(hasher.hash(STRONG))
    weaker = "$argon2id$v=19$m=8192,t=1,p=1$c29tZXNhbHQ$" + "A" * 43
    assert hasher.needs_rehash(weaker)
