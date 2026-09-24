"""Invitations: invite, list, revoke, accept.

- The token is 256-bit random, emailed to the invitee and stored only as SHA-256.
- Inviting an address with a pending invitation replaces it (the old link dies): this is resend.
- Accepting runs in one transaction: invitation row locked; must be pending, unexpired, for a
  live organization; the signed-in user's verified email must match; then the membership is
  created and the invitation marked accepted. Concurrent accepts of one token yield one member.
- Mutations take the organization lock, serializing them with membership changes.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.identity.entities import User
from core.domain.identity.tokens import MAX_TOKEN_LENGTH, generate_token, hash_token
from core.domain.identity.value_objects import normalize_email
from core.domain.notifications import Mailer
from core.domain.unit_of_work import UnitOfWork

from .entities import Invitation, Membership, OrganizationWithRole
from .enums import Role
from .errors import (
    AlreadyMember,
    EmailNotVerified,
    InvalidInvitation,
    InvitationEmailMismatch,
    InvitationExpired,
    InvitationNotFound,
    OrganizationNotFound,
)
from .membership_policy import check_invitation
from .permissions import Permission


@dataclass(frozen=True, slots=True)
class InvitationSettings:
    ttl: timedelta = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class _InvitationEmail:
    to: str
    inviter_name: str
    organization_name: str
    role: Role
    token: str


class InvitationService:
    def __init__(
        self, uow: UnitOfWork, *, mailer: Mailer, settings: InvitationSettings, clock: Clock = utc_now
    ) -> None:
        self._uow = uow
        self._mailer = mailer
        self._settings = settings
        self._clock = clock

    async def list_pending(self, *, membership: Membership) -> list[Invitation]:
        membership.require(Permission.MEMBER_INVITE)
        async with self._uow as uow:
            return await uow.invitations.list_pending(membership.organization_id)

    async def invite(
        self, *, inviter: User, organization_id: uuid.UUID, email: str, role: Role
    ) -> Invitation:
        invitee_email = normalize_email(email)
        now = self._clock()
        async with self._uow as uow:
            organization = await uow.organizations.get_active(organization_id)
            if organization is None or not await uow.organizations.lock_active(organization_id):
                raise OrganizationNotFound
            actor = await uow.memberships.get_for_user(organization_id=organization_id, user_id=inviter.id)
            if actor is None:
                raise OrganizationNotFound
            check_invitation(actor=actor.role, role=role)
            existing_user = await uow.users.get_by_email(invitee_email)
            if existing_user is not None and await uow.memberships.get_for_user(
                organization_id=organization_id, user_id=existing_user.id
            ):
                raise AlreadyMember

            token = generate_token()
            await uow.invitations.revoke_pending_for_email(
                organization_id=organization_id, email=invitee_email, at=now
            )
            invitation = await uow.invitations.add(
                organization_id=organization_id,
                email=invitee_email,
                role=role,
                token_hash=hash_token(token),
                invited_by_user_id=inviter.id,
                expires_at=now + self._settings.ttl,
                created_at=now,
            )
            await uow.audit.record(
                AuditEvent(
                    AuditAction.MEMBER_INVITED,
                    actor_user_id=inviter.id,
                    organization_id=organization_id,
                    resource_type="invitation",
                    resource_id=invitation.id,
                    metadata={"email": invitee_email, "role": role.value},
                )
            )
            outgoing = _InvitationEmail(invitee_email, inviter.name, organization.name, role, token)
        await self._mailer.send_invitation(
            to=outgoing.to,
            inviter_name=outgoing.inviter_name,
            organization_name=outgoing.organization_name,
            role=outgoing.role.value,
            token=outgoing.token,
        )
        return invitation

    async def revoke(
        self, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> None:
        now = self._clock()
        async with self._uow as uow:
            if not await uow.organizations.lock_active(organization_id):
                raise OrganizationNotFound
            actor = await uow.memberships.get_for_user(organization_id=organization_id, user_id=actor_user_id)
            if actor is None:
                raise OrganizationNotFound
            invitation = await uow.invitations.get_pending(
                organization_id=organization_id, invitation_id=invitation_id
            )
            if invitation is None:
                raise InvitationNotFound
            check_invitation(actor=actor.role, role=invitation.role)
            await uow.invitations.revoke(invitation.id, now)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.MEMBER_INVITATION_REVOKED,
                    actor_user_id=actor_user_id,
                    organization_id=organization_id,
                    resource_type="invitation",
                    resource_id=invitation.id,
                    metadata={"email": invitation.email, "role": invitation.role.value},
                )
            )

    async def accept(self, *, user: User, token: str) -> OrganizationWithRole:
        if not user.is_email_verified:
            raise EmailNotVerified
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidInvitation
        now = self._clock()
        async with self._uow as uow:
            invitation = await uow.invitations.get_by_hash_for_update(hash_token(token))
            if invitation is None or not invitation.is_pending():
                raise InvalidInvitation
            if invitation.expires_at <= now:
                raise InvitationExpired
            if not await uow.organizations.lock_active(invitation.organization_id):
                raise InvalidInvitation
            if user.email != invitation.email:
                raise InvitationEmailMismatch
            if await uow.memberships.get_for_user(
                organization_id=invitation.organization_id, user_id=user.id
            ):
                raise AlreadyMember
            await uow.memberships.add(
                organization_id=invitation.organization_id, user_id=user.id, role=invitation.role
            )
            await uow.invitations.mark_accepted(invitation.id, user_id=user.id, at=now)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.MEMBER_INVITATION_ACCEPTED,
                    actor_user_id=user.id,
                    organization_id=invitation.organization_id,
                    resource_type="invitation",
                    resource_id=invitation.id,
                    metadata={"role": invitation.role.value},
                )
            )
            joined = await uow.memberships.get_in_active_organization(
                organization_id=invitation.organization_id, user_id=user.id
            )
            assert joined is not None  # noqa: S101 — created above in this transaction
            return joined
