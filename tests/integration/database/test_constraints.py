"""Database-level guarantees: uniqueness, checks, foreign keys, cascades and audit immutability.

These are the invariants the database enforces on its own, so they hold even if application
code is wrong. Rows are inserted directly through the table models.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import (
    AuditLogRecord,
    EmailVerificationTokenRecord,
    InvitationRecord,
    OrganizationMemberRecord,
    OrganizationRecord,
    PasswordResetTokenRecord,
    SessionRecord,
    UserRecord,
)

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
LATER = NOW + timedelta(days=1)


def token_hash() -> bytes:
    return hashlib.sha256(secrets.token_bytes(32)).digest()


async def add(db: AsyncSession, *records: object) -> None:
    db.add_all(records)
    await db.flush()


async def add_in_savepoint(db: AsyncSession, records: tuple[object, ...]) -> None:
    """Add and flush inside a savepoint: on failure the savepoint rolls back and the records are
    discarded with it, so the test's own transaction stays usable."""
    async with db.begin_nested():
        db.add_all(records)
        await db.flush()


async def expect_violation(db: AsyncSession, *records: object) -> None:
    with pytest.raises(IntegrityError):
        await add_in_savepoint(db, records)


def user(email: str = "ada@example.com", **overrides: object) -> UserRecord:
    fields: dict[str, object] = {"email": email, "password_hash": "$argon2id$placeholder", "name": "Ada"}
    return UserRecord(**(fields | overrides))


# --- users --------------------------------------------------------------------------------------


async def test_user_defaults_and_timestamps_come_from_the_database(db: AsyncSession) -> None:
    ada = user()
    await add(db, ada)
    await db.refresh(ada)

    assert ada.id.version == 7
    assert ada.status == "active"
    assert ada.created_at.tzinfo is not None
    assert ada.updated_at == ada.created_at


async def test_orm_updates_refresh_updated_at(db: AsyncSession) -> None:
    ada = user()
    await add(db, ada)
    # now() is constant inside a transaction, so backdate the row first to observe the change.
    week_ago = NOW - timedelta(days=7)
    await db.execute(update(UserRecord).where(UserRecord.id == ada.id).values(updated_at=week_ago))
    await db.refresh(ada)
    assert ada.updated_at == week_ago

    ada.name = "Ada Lovelace"
    await db.flush()
    await db.refresh(ada)

    assert ada.updated_at > week_ago
    assert ada.updated_at == (await db.scalar(text("SELECT now()")))


async def test_email_is_unique_regardless_of_case(db: AsyncSession) -> None:
    await add(db, user("ada@example.com"))
    await expect_violation(db, user("ADA@Example.com"))


@pytest.mark.parametrize("email", [" ada@example.com", "ada@example.com ", "a@"])
async def test_email_must_be_trimmed_and_plausible(db: AsyncSession, email: str) -> None:
    await expect_violation(db, user(email))


async def test_status_is_restricted_and_deleted_requires_deleted_at(db: AsyncSession) -> None:
    await expect_violation(db, user(status="banned"))
    await expect_violation(db, user(status="deleted"))
    await expect_violation(db, user(deleted_at=NOW))
    await add(db, user(status="deleted", deleted_at=NOW))


async def test_name_length_is_bounded(db: AsyncSession) -> None:
    await expect_violation(db, user(name=""))
    await expect_violation(db, user(name="x" * 81))


# --- organizations and membership ---------------------------------------------------------------


async def test_membership_is_unique_per_user_and_organization(db: AsyncSession) -> None:
    ada, org = user(), OrganizationRecord(name="Acme")
    await add(db, ada, org)
    await add(db, OrganizationMemberRecord(organization_id=org.id, user_id=ada.id, role="owner"))

    await expect_violation(
        db, OrganizationMemberRecord(organization_id=org.id, user_id=ada.id, role="viewer")
    )


async def test_membership_role_is_restricted(db: AsyncSession) -> None:
    ada, org = user(), OrganizationRecord(name="Acme")
    await add(db, ada, org)
    await expect_violation(
        db, OrganizationMemberRecord(organization_id=org.id, user_id=ada.id, role="superuser")
    )


async def test_membership_requires_existing_user_and_organization(db: AsyncSession) -> None:
    org = OrganizationRecord(name="Acme")
    await add(db, org)
    await expect_violation(
        db, OrganizationMemberRecord(organization_id=org.id, user_id=uuid.uuid7(), role="owner")
    )


async def test_deleting_an_organization_cascades_to_members_and_invitations(db: AsyncSession) -> None:
    ada, org = user(), OrganizationRecord(name="Acme")
    await add(db, ada, org)
    await add(
        db,
        OrganizationMemberRecord(organization_id=org.id, user_id=ada.id, role="owner"),
        InvitationRecord(
            organization_id=org.id,
            email="bob@example.com",
            role="member",
            token_hash=token_hash(),
            expires_at=LATER,
        ),
    )

    await db.execute(delete(OrganizationRecord).where(OrganizationRecord.id == org.id))

    assert (await db.scalars(select(OrganizationMemberRecord))).all() == []
    assert (await db.scalars(select(InvitationRecord))).all() == []
    assert (await db.get(UserRecord, ada.id)) is not None


async def test_organization_name_length_is_bounded(db: AsyncSession) -> None:
    await expect_violation(db, OrganizationRecord(name=""))
    await expect_violation(db, OrganizationRecord(name="x" * 101))


# --- sessions and single-use tokens -------------------------------------------------------------


def session_for(user_id: uuid.UUID, **overrides: object) -> SessionRecord:
    fields: dict[str, object] = {"user_id": user_id, "refresh_token_hash": token_hash(), "expires_at": LATER}
    return SessionRecord(**(fields | overrides))


async def test_session_hash_must_be_a_sha256_digest(db: AsyncSession) -> None:
    ada = user()
    await add(db, ada)
    await expect_violation(db, session_for(ada.id, refresh_token_hash=b"too-short"))


async def test_session_revocation_needs_both_time_and_reason(db: AsyncSession) -> None:
    ada = user()
    await add(db, ada)
    await expect_violation(db, session_for(ada.id, revoked_at=NOW))
    await expect_violation(db, session_for(ada.id, revoked_reason="logout"))
    await expect_violation(db, session_for(ada.id, revoked_at=NOW, revoked_reason="because"))
    await add(db, session_for(ada.id, revoked_at=NOW, revoked_reason="logout"))


@pytest.mark.parametrize("model", [EmailVerificationTokenRecord, PasswordResetTokenRecord])
async def test_single_use_tokens_are_unique_and_have_one_outcome(
    db: AsyncSession, model: type[EmailVerificationTokenRecord] | type[PasswordResetTokenRecord]
) -> None:
    ada = user()
    await add(db, ada)
    shared = token_hash()
    await add(db, model(user_id=ada.id, token_hash=shared, expires_at=LATER))

    await expect_violation(db, model(user_id=ada.id, token_hash=shared, expires_at=LATER))
    await expect_violation(
        db, model(user_id=ada.id, token_hash=token_hash(), expires_at=LATER, consumed_at=NOW, revoked_at=NOW)
    )


async def test_deleting_a_user_cascades_to_sessions_tokens_and_memberships(db: AsyncSession) -> None:
    ada, org = user(), OrganizationRecord(name="Acme")
    await add(db, ada, org)
    await add(
        db,
        session_for(ada.id),
        EmailVerificationTokenRecord(user_id=ada.id, token_hash=token_hash(), expires_at=LATER),
        PasswordResetTokenRecord(user_id=ada.id, token_hash=token_hash(), expires_at=LATER),
        OrganizationMemberRecord(organization_id=org.id, user_id=ada.id, role="owner"),
    )

    await db.execute(delete(UserRecord).where(UserRecord.id == ada.id))

    for model in (
        SessionRecord,
        EmailVerificationTokenRecord,
        PasswordResetTokenRecord,
        OrganizationMemberRecord,
    ):
        assert (await db.scalars(select(model))).all() == [], model.__tablename__


# --- invitations --------------------------------------------------------------------------------


def invitation(org_id: uuid.UUID, email: str = "bob@example.com", **overrides: object) -> InvitationRecord:
    fields: dict[str, object] = {
        "organization_id": org_id,
        "email": email,
        "role": "member",
        "token_hash": token_hash(),
        "expires_at": LATER,
    }
    return InvitationRecord(**(fields | overrides))


async def test_only_one_pending_invitation_per_email_per_organization(db: AsyncSession) -> None:
    org, other = OrganizationRecord(name="Acme"), OrganizationRecord(name="Globex")
    await add(db, org, other)
    first = invitation(org.id)
    await add(db, first)

    await expect_violation(db, invitation(org.id, "BOB@example.com"))
    await add(db, invitation(other.id))  # a different organization may invite the same person

    first.revoked_at = NOW
    await db.flush()
    await add(db, invitation(org.id))  # once the first is closed, a new one is allowed


async def test_invitation_outcome_and_acceptor_are_consistent(db: AsyncSession) -> None:
    ada, org = user(), OrganizationRecord(name="Acme")
    await add(db, ada, org)
    await expect_violation(db, invitation(org.id, accepted_at=NOW, revoked_at=NOW))
    await expect_violation(db, invitation(org.id, accepted_by_user_id=ada.id))
    await expect_violation(db, invitation(org.id, role="root"))


async def test_invitation_survives_deletion_of_its_inviter(db: AsyncSession) -> None:
    ada, org = user(), OrganizationRecord(name="Acme")
    await add(db, ada, org)
    sent = invitation(org.id, invited_by_user_id=ada.id)
    await add(db, sent)

    await db.execute(delete(UserRecord).where(UserRecord.id == ada.id))
    await db.refresh(sent)

    assert sent.invited_by_user_id is None


# --- audit log ----------------------------------------------------------------------------------


async def test_audit_log_accepts_inserts(db: AsyncSession) -> None:
    entry = AuditLogRecord(
        action="user.login", event_metadata={"method": "password"}, ip_address="203.0.113.7"
    )
    await add(db, entry)
    await db.refresh(entry)
    assert entry.event_metadata == {"method": "password"}


@pytest.mark.parametrize("action", ["login", "User.Login", "user.login; drop table users"])
async def test_audit_action_must_be_a_dotted_identifier(db: AsyncSession, action: str) -> None:
    await expect_violation(db, AuditLogRecord(action=action))


@pytest.mark.parametrize(
    "statement",
    [
        update(AuditLogRecord).values(action="user.logout"),
        delete(AuditLogRecord),
        text("TRUNCATE audit_logs"),
    ],
    ids=["update", "delete", "truncate"],
)
async def test_audit_log_is_append_only(db: AsyncSession, statement: object) -> None:
    await add(db, AuditLogRecord(action="user.login"))

    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin_nested():
            await db.execute(statement)  # type: ignore[call-overload]


async def test_audit_entries_outlive_the_organization_they_describe(db: AsyncSession) -> None:
    org = OrganizationRecord(name="Acme")
    await add(db, org)
    await add(db, AuditLogRecord(action="organization.created", organization_id=org.id))

    await db.execute(delete(OrganizationRecord).where(OrganizationRecord.id == org.id))

    remaining = (
        await db.scalars(select(AuditLogRecord).where(AuditLogRecord.organization_id == org.id))
    ).all()
    assert len(remaining) == 1
