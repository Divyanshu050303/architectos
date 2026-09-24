import pytest

from core.domain.identity.auth_service import AuthService, VerificationSettings
from core.domain.identity.entities import NewUser, User
from core.domain.identity.enums import SessionRevocationReason, UserStatus
from core.domain.identity.errors import (
    IncorrectPassword,
    InvalidAvatarUrl,
    InvalidCredentials,
    InvalidName,
    InvalidToken,
    NothingToUpdate,
)
from core.domain.identity.password_service import PasswordService, ResetSettings
from core.domain.identity.passwords import PasswordHasher, PasswordPolicy
from core.domain.identity.session_service import ClientInfo, SessionService, SessionSettings
from core.domain.identity.user_service import UserService
from core.domain.identity.value_objects import normalize_avatar_url, tombstone_email

from .fakes import FakeClock, FakeUnitOfWork, RecordingMailer

PASSWORD = "correct horse battery staple"
AVATAR = "https://images.example.com/ada.png"


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture
def users(uow: FakeUnitOfWork, hasher: PasswordHasher, clock: FakeClock) -> UserService:
    return UserService(uow, hasher=hasher, clock=clock)


@pytest.fixture
async def ada(uow: FakeUnitOfWork, hasher: PasswordHasher) -> User:
    return await uow.users.add(NewUser("ada@example.com", "Ada", hasher.hash(PASSWORD)))


# --- avatar URLs --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "http://images.example.com/a.png",
        "https://user:pass@images.example.com/a.png",
        "https:///no-host.png",
        "https://images.example.com/a b.png",
        "ftp://images.example.com/a.png",
        "https://example.com/" + "a" * 2100,
        "",
    ],
)
def test_unsafe_avatar_urls_are_rejected(url: str) -> None:
    with pytest.raises(InvalidAvatarUrl):
        normalize_avatar_url(url)


def test_https_avatar_urls_are_accepted() -> None:
    assert normalize_avatar_url(f"  {AVATAR} ") == AVATAR


def test_allow_list_restricts_avatar_hosts() -> None:
    hosts = frozenset({"images.example.com"})
    assert normalize_avatar_url(AVATAR, allowed_hosts=hosts) == AVATAR
    assert normalize_avatar_url("https://IMAGES.example.com/x.png", allowed_hosts=hosts)
    with pytest.raises(InvalidAvatarUrl):
        normalize_avatar_url("https://tracker.example.net/pixel.gif", allowed_hosts=hosts)


# --- profile ------------------------------------------------------------------------------------


async def test_update_name_only(users: UserService, ada: User) -> None:
    updated = await users.update_profile(user_id=ada.id, name="  Ada   Lovelace ")
    assert (updated.name, updated.avatar_url) == ("Ada Lovelace", None)


async def test_set_and_remove_avatar(users: UserService, ada: User) -> None:
    with_avatar = await users.update_profile(user_id=ada.id, avatar_url=AVATAR, set_avatar=True)
    assert with_avatar.avatar_url == AVATAR

    renamed = await users.update_profile(user_id=ada.id, name="Ada L.")
    assert renamed.avatar_url == AVATAR  # untouched when not supplied

    removed = await users.update_profile(user_id=ada.id, avatar_url=None, set_avatar=True)
    assert removed.avatar_url is None


async def test_update_requires_a_field(users: UserService, ada: User) -> None:
    with pytest.raises(NothingToUpdate):
        await users.update_profile(user_id=ada.id)


async def test_update_validates_values(users: UserService, ada: User) -> None:
    with pytest.raises(InvalidName):
        await users.update_profile(user_id=ada.id, name="  ")
    with pytest.raises(InvalidAvatarUrl):
        await users.update_profile(user_id=ada.id, avatar_url="javascript:alert(1)", set_avatar=True)


# --- deletion -----------------------------------------------------------------------------------


async def test_deletion_scrubs_personal_data_and_ends_everything(
    users: UserService,
    uow: FakeUnitOfWork,
    hasher: PasswordHasher,
    mailer: RecordingMailer,
    clock: FakeClock,
    ada: User,
) -> None:
    sessions = SessionService(uow, hasher=hasher, settings=SessionSettings(), clock=clock)
    passwords = PasswordService(
        uow, hasher=hasher, policy=PasswordPolicy(), mailer=mailer, settings=ResetSettings(), clock=clock
    )
    await users.update_profile(user_id=ada.id, avatar_url=AVATAR, set_avatar=True)
    signed_in = await sessions.login(email="ada@example.com", password=PASSWORD, client=ClientInfo())
    await passwords.request_reset(email="ada@example.com")
    reset_token = mailer.sent[-1].token
    assert reset_token is not None

    await users.delete_account(user_id=ada.id, password=PASSWORD)

    deleted = uow.users.by_id[ada.id]
    assert deleted.status is UserStatus.DELETED
    assert deleted.deleted_at == clock.now
    assert deleted.email == tombstone_email(ada.id)
    assert deleted.email.endswith("@deleted.invalid")
    assert (deleted.name, deleted.avatar_url) == ("Deleted user", None)
    assert not hasher.verify(deleted.password_hash, PASSWORD)
    assert uow.sessions.by_id[signed_in.session.id].revoked_reason is SessionRevocationReason.ACCOUNT_DELETED
    with pytest.raises(InvalidToken):
        await passwords.reset(token=reset_token, new_password="brand new password 1")
    with pytest.raises(InvalidCredentials):
        await sessions.login(email="ada@example.com", password=PASSWORD, client=ClientInfo())


async def test_deletion_requires_the_current_password(
    users: UserService, uow: FakeUnitOfWork, ada: User
) -> None:
    with pytest.raises(IncorrectPassword):
        await users.delete_account(user_id=ada.id, password="not my password")
    assert uow.users.by_id[ada.id].status is UserStatus.ACTIVE


async def test_the_email_can_be_registered_again(
    users: UserService,
    uow: FakeUnitOfWork,
    hasher: PasswordHasher,
    mailer: RecordingMailer,
    clock: FakeClock,
    ada: User,
) -> None:
    await users.delete_account(user_id=ada.id, password=PASSWORD)
    auth = AuthService(
        uow,
        hasher=hasher,
        policy=PasswordPolicy(),
        mailer=mailer,
        verification=VerificationSettings(),
        clock=clock,
    )

    result = await auth.register(email="ada@example.com", password=PASSWORD, name="Ada Again")

    assert result.user is not None
    assert result.user.id != ada.id
