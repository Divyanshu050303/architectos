"""In-memory stand-ins for the persistence layer and mailer, for service tests without a database."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self

from core.architecture_ir.serialization import to_dict
from core.domain.architecture.entities import (
    Architecture,
    ArchitectureLayout,
    ArchitectureQuery,
    ArchitectureStatus,
    NewArchitecture,
    Position,
    RevisionSummary,
)
from core.domain.architecture.errors import ArchitectureNameTaken
from core.domain.architecture.versions import ArchitectureRevision, NewRevision
from core.domain.audit.entities import AuditCursor, AuditEntry, AuditEvent
from core.domain.capacity.analyses import AnalysisReport, CapacityAnalysis
from core.domain.capacity.queries import AnalysisQuery, BottleneckQuery, ComponentQuery
from core.domain.capacity.results import Bottleneck, ComponentResult, Unsupported
from core.domain.capacity.scenarios import ScalingOption, ScenarioOutcome
from core.domain.identity.entities import (
    DeletedUserValues,
    NewSession,
    NewUser,
    ProfileChanges,
    Session,
    SingleUseToken,
    User,
)
from core.domain.identity.enums import SessionRevocationReason, UserStatus
from core.domain.identity.errors import EmailAlreadyRegistered
from core.domain.organizations.entities import (
    Invitation,
    Membership,
    MemberView,
    Organization,
    OrganizationWithRole,
    OwnedOrganization,
)
from core.domain.organizations.enums import Role
from core.domain.projects.entities import NewProject, Project, ProjectAccess
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.errors import ProjectSlugTaken
from core.domain.projects.queries import ProjectQuery, ProjectSort
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.analyses import NewRequirementAnalysis, RequirementAnalysis
from core.domain.requirements.entities import NewRequirement, Requirement, RequirementVersion, Revision
from core.domain.requirements.enums import RequirementStatus
from core.domain.requirements.errors import CandidateAlreadyPromoted
from core.domain.requirements.queries import RequirementQuery
from core.domain.requirements.requirement_sets import NewRequirementSet, RequirementSet
from core.domain.validation.queries import FindingQuery, RunQuery
from core.domain.validation.results import Finding
from core.domain.validation.runs import RunInputs, RunReport, ValidationRun


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeUserRepository:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.by_id: dict[uuid.UUID, User] = {}

    async def get(self, user_id: uuid.UUID) -> User | None:
        return self.by_id.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((user for user in self.by_id.values() if user.email.lower() == email), None)

    async def get_by_email_for_update(self, email: str) -> User | None:
        return await self.get_by_email(email)

    async def mark_email_verified(self, user_id: uuid.UUID, at: datetime) -> None:
        user = self.by_id[user_id]
        if user.email_verified_at is None:
            self.by_id[user_id] = replace(user, email_verified_at=at)

    async def update_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        self.by_id[user_id] = replace(self.by_id[user_id], password_hash=password_hash)

    async def update_profile(self, user_id: uuid.UUID, changes: ProfileChanges) -> User:
        user = self.by_id[user_id]
        if changes.name is not None:
            user = replace(user, name=changes.name)
        if not changes.keep_avatar:
            user = replace(user, avatar_url=changes.avatar_url)
        self.by_id[user_id] = replace(user, updated_at=self._clock())
        return self.by_id[user_id]

    async def soft_delete(self, user_id: uuid.UUID, *, tombstone: DeletedUserValues, at: datetime) -> None:
        self.by_id[user_id] = replace(
            self.by_id[user_id],
            status=UserStatus.DELETED,
            deleted_at=at,
            email=tombstone.email,
            name=tombstone.name,
            password_hash=tombstone.password_hash,
            avatar_url=None,
            email_verified_at=None,
        )

    async def add(self, user: NewUser) -> User:
        if await self.get_by_email(user.email.lower()):
            raise EmailAlreadyRegistered
        now = self._clock()
        stored = User(
            id=uuid.uuid7(),
            email=user.email,
            name=user.name,
            password_hash=user.password_hash,
            avatar_url=None,
            email_verified_at=None,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self.by_id[stored.id] = stored
        return stored


class FakeTokenRepository:
    def __init__(self) -> None:
        self.tokens: dict[uuid.UUID, SingleUseToken] = {}

    async def add(
        self, *, user_id: uuid.UUID, token_hash: bytes, expires_at: datetime, created_at: datetime
    ) -> None:
        token = SingleUseToken(uuid.uuid7(), user_id, token_hash, expires_at, None, None, created_at)
        self.tokens[token.id] = token

    async def get_by_hash_for_update(self, token_hash: bytes) -> SingleUseToken | None:
        return next((token for token in self.tokens.values() if token.token_hash == token_hash), None)

    def _outstanding(self, user_id: uuid.UUID) -> list[SingleUseToken]:
        return [t for t in self.tokens.values() if t.user_id == user_id and not t.is_spent]

    async def latest_outstanding_created_at(self, user_id: uuid.UUID) -> datetime | None:
        return max((t.created_at for t in self._outstanding(user_id)), default=None)

    async def revoke_outstanding(self, user_id: uuid.UUID, at: datetime) -> None:
        for token in self._outstanding(user_id):
            self.tokens[token.id] = replace(token, revoked_at=at)

    async def mark_consumed(self, token_id: uuid.UUID, at: datetime) -> None:
        self.tokens[token_id] = replace(self.tokens[token_id], consumed_at=at)


class FakeSessionRepository:
    def __init__(self) -> None:
        self.by_id: dict[uuid.UUID, Session] = {}

    async def add(self, session: NewSession) -> Session:
        stored = Session(
            id=uuid.uuid7(),
            user_id=session.user_id,
            refresh_token_hash=session.refresh_token_hash,
            previous_refresh_token_hash=None,
            refreshed_at=None,
            expires_at=session.expires_at,
            last_used_at=session.created_at,
            revoked_at=None,
            revoked_reason=None,
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            created_at=session.created_at,
        )
        self.by_id[stored.id] = stored
        return stored

    async def get(self, session_id: uuid.UUID) -> Session | None:
        return self.by_id.get(session_id)

    async def get_for_update(self, session_id: uuid.UUID) -> Session | None:
        return self.by_id.get(session_id)

    async def rotate(
        self, session_id: uuid.UUID, *, new_hash: bytes, previous_hash: bytes, at: datetime
    ) -> None:
        self.by_id[session_id] = replace(
            self.by_id[session_id],
            refresh_token_hash=new_hash,
            previous_refresh_token_hash=previous_hash,
            refreshed_at=at,
            last_used_at=at,
        )

    async def revoke(self, session_id: uuid.UUID, *, reason: SessionRevocationReason, at: datetime) -> None:
        session = self.by_id[session_id]
        if session.revoked_at is None:
            self.by_id[session_id] = replace(session, revoked_at=at, revoked_reason=reason)

    async def list_active(self, user_id: uuid.UUID, *, now: datetime, limit: int) -> list[Session]:
        active = [s for s in self.by_id.values() if s.user_id == user_id and s.is_active(now)]
        active.sort(key=lambda s: (s.last_used_at or s.created_at, s.id), reverse=True)
        return active[:limit]

    async def revoke_owned(
        self, session_id: uuid.UUID, *, user_id: uuid.UUID, reason: SessionRevocationReason, at: datetime
    ) -> bool:
        session = self.by_id.get(session_id)
        if session is None or session.user_id != user_id or not session.is_active(at):
            return False
        await self.revoke(session_id, reason=reason, at=at)
        return True

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        *,
        reason: SessionRevocationReason,
        at: datetime,
        keep: uuid.UUID | None = None,
    ) -> int:
        targets = [
            s for s in self.by_id.values() if s.user_id == user_id and s.revoked_at is None and s.id != keep
        ]
        for session in targets:
            await self.revoke(session.id, reason=reason, at=at)
        return len(targets)


class FakeOrganizationRepository:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.by_id: dict[uuid.UUID, Organization] = {}
        self.deleted: set[uuid.UUID] = set()

    async def add(self, *, name: str) -> Organization:
        now = self._clock()
        organization = Organization(id=uuid.uuid7(), name=name, created_at=now, updated_at=now)
        self.by_id[organization.id] = organization
        return organization

    async def rename(self, organization_id: uuid.UUID, name: str) -> Organization:
        self.by_id[organization_id] = replace(
            self.by_id[organization_id], name=name, updated_at=self._clock()
        )
        return self.by_id[organization_id]

    async def soft_delete(self, organization_id: uuid.UUID, at: datetime) -> None:
        self.deleted.add(organization_id)

    async def lock_active(self, organization_id: uuid.UUID) -> bool:
        return organization_id in self.by_id and organization_id not in self.deleted

    async def get_active(self, organization_id: uuid.UUID) -> Organization | None:
        return self.by_id.get(organization_id) if organization_id not in self.deleted else None


class FakeMembershipRepository:
    def __init__(self, organizations: FakeOrganizationRepository, clock: FakeClock) -> None:
        self._organizations = organizations
        self._clock = clock
        self.by_id: dict[uuid.UUID, Membership] = {}

    async def add(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> Membership:
        membership = Membership(uuid.uuid7(), organization_id, user_id, role, self._clock())
        self.by_id[membership.id] = membership
        return membership

    def _live(self, membership: Membership) -> bool:
        return membership.organization_id not in self._organizations.deleted

    async def get_in_active_organization(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationWithRole | None:
        for m in self.by_id.values():
            if m.organization_id == organization_id and m.user_id == user_id and self._live(m):
                return OrganizationWithRole(self._organizations.by_id[organization_id], m)
        return None

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationWithRole]:
        found = [
            OrganizationWithRole(self._organizations.by_id[m.organization_id], m)
            for m in self.by_id.values()
            if m.user_id == user_id and self._live(m)
        ]
        return sorted(found, key=lambda o: o.organization.name.lower())

    def _in(self, organization_id: uuid.UUID) -> list[Membership]:
        return [m for m in self.by_id.values() if m.organization_id == organization_id]

    async def get_for_user(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        return next((m for m in self._in(organization_id) if m.user_id == user_id), None)

    async def get(self, *, organization_id: uuid.UUID, membership_id: uuid.UUID) -> Membership | None:
        found = self.by_id.get(membership_id)
        return found if found and found.organization_id == organization_id else None

    async def count_owners(self, organization_id: uuid.UUID) -> int:
        return sum(1 for m in self._in(organization_id) if m.role is Role.OWNER)

    async def list_members(self, organization_id: uuid.UUID) -> list[MemberView]:
        return [MemberView(m, "Someone", "someone@example.com", None) for m in self._in(organization_id)]

    async def update_role(self, membership_id: uuid.UUID, role: Role) -> Membership:
        self.by_id[membership_id] = replace(self.by_id[membership_id], role=role)
        return self.by_id[membership_id]

    async def delete(self, membership_id: uuid.UUID) -> None:
        del self.by_id[membership_id]

    async def owned_by(self, user_id: uuid.UUID) -> list[OwnedOrganization]:
        owned = {
            m.organization_id
            for m in self.by_id.values()
            if m.user_id == user_id and m.role is Role.OWNER and self._live(m)
        }
        return [
            OwnedOrganization(org, sum(1 for m in self._in(org) if m.role is Role.OWNER), len(self._in(org)))
            for org in sorted(owned)
        ]

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None:
        for membership_id in [m.id for m in self.by_id.values() if m.user_id == user_id]:
            del self.by_id[membership_id]


class FakeInvitationRepository:
    def __init__(self) -> None:
        self.by_id: dict[uuid.UUID, Invitation] = {}
        self.hashes: dict[bytes, uuid.UUID] = {}

    async def add(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
        role: Role,
        token_hash: bytes,
        invited_by_user_id: uuid.UUID,
        expires_at: datetime,
        created_at: datetime,
    ) -> Invitation:
        invitation = Invitation(
            uuid.uuid7(), organization_id, email, role, invited_by_user_id, expires_at, None, None, created_at
        )
        self.by_id[invitation.id] = invitation
        self.hashes[token_hash] = invitation.id
        return invitation

    async def list_pending(self, organization_id: uuid.UUID) -> list[Invitation]:
        return [i for i in self.by_id.values() if i.organization_id == organization_id and i.is_pending()]

    async def get_pending(self, *, organization_id: uuid.UUID, invitation_id: uuid.UUID) -> Invitation | None:
        found = self.by_id.get(invitation_id)
        return found if found and found.organization_id == organization_id and found.is_pending() else None

    async def get_by_hash_for_update(self, token_hash: bytes) -> Invitation | None:
        invitation_id = self.hashes.get(token_hash)
        return self.by_id.get(invitation_id) if invitation_id else None

    async def revoke_pending_for_email(self, *, organization_id: uuid.UUID, email: str, at: datetime) -> None:
        for invitation in await self.list_pending(organization_id):
            if invitation.email == email:
                self.by_id[invitation.id] = replace(invitation, revoked_at=at)

    async def revoke(self, invitation_id: uuid.UUID, at: datetime) -> None:
        self.by_id[invitation_id] = replace(self.by_id[invitation_id], revoked_at=at)

    async def mark_accepted(self, invitation_id: uuid.UUID, *, user_id: uuid.UUID, at: datetime) -> None:
        self.by_id[invitation_id] = replace(self.by_id[invitation_id], accepted_at=at)


class FakeAuditRepository:
    """Records events in order; committed and rolled-back events are tracked separately."""

    def __init__(self) -> None:
        self.pending: list[AuditEvent] = []
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.pending.append(event)

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, after: AuditCursor | None, limit: int
    ) -> list[AuditEntry]:
        return []

    def actions(self) -> list[str]:
        return [event.action.value for event in self.events]


class FakeProjectRepository:
    def __init__(self, clock: FakeClock, memberships: FakeMembershipRepository) -> None:
        self._clock = clock
        self._memberships = memberships
        self.by_id: dict[uuid.UUID, Project] = {}

    async def get_for_member(
        self, project_id: uuid.UUID, *, user_id: uuid.UUID, lock: ProjectLock | None = None
    ) -> ProjectAccess | None:
        project = await self.get_live(project_id)
        if project is None:
            return None
        scoped = await self._memberships.get_in_active_organization(
            organization_id=project.organization_id, user_id=user_id
        )
        return ProjectAccess(project=project, membership=scoped.membership) if scoped else None

    async def list_for_organization(self, organization_id: uuid.UUID, query: ProjectQuery) -> list[Project]:
        found = [p for p in self.by_id.values() if p.organization_id == organization_id and not p.is_deleted]
        if query.status is not None:
            found = [p for p in found if p.status is query.status]
        if query.search:
            term = query.search.lower()
            found = [p for p in found if term in p.name.lower() or term in p.slug]
        if query.sort is ProjectSort.NAME:
            found.sort(key=lambda p: (p.name.lower(), p.id))
        else:
            attr = "created_at" if query.sort is ProjectSort.CREATED_AT else "updated_at"
            found.sort(key=lambda p: (getattr(p, attr), p.id), reverse=True)
        if query.after is not None:
            ids = [p.id for p in found]
            found = found[ids.index(query.after.id) + 1 :] if query.after.id in ids else []
        return found[: query.limit]

    async def add(self, project: NewProject) -> Project:
        if any(
            p.organization_id == project.organization_id and p.slug == project.slug and not p.is_deleted
            for p in self.by_id.values()
        ):
            raise ProjectSlugTaken
        now = self._clock()
        stored = Project(
            id=uuid.uuid7(),
            organization_id=project.organization_id,
            name=project.name,
            slug=project.slug,
            description=project.description,
            status=ProjectStatus.ACTIVE,
            settings=project.settings,
            created_by_user_id=project.created_by_user_id,
            archived_at=None,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        self.by_id[stored.id] = stored
        return stored

    async def get_live(self, project_id: uuid.UUID) -> Project | None:
        found = self.by_id.get(project_id)
        return found if found and not found.is_deleted else None

    async def get_live_for_update(self, project_id: uuid.UUID) -> Project | None:
        return await self.get_live(project_id)

    async def save(self, project: Project) -> Project:
        stored = replace(project, updated_at=self._clock())
        self.by_id[project.id] = stored
        return stored


class FakeRequirementRepository:
    """Keeps every version, like the append-only table."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.by_id: dict[uuid.UUID, Requirement] = {}
        self.versions: dict[uuid.UUID, list[RequirementVersion]] = {}

    def _version_of(
        self, requirement: Requirement, reason: str | None, author: uuid.UUID | None
    ) -> RequirementVersion:
        return RequirementVersion(
            requirement_id=requirement.id,
            version=requirement.version,
            content=requirement.content,
            source=requirement.source,
            confidence=requirement.confidence,
            change_reason=reason,
            created_by_user_id=author,
            created_at=self._clock(),
        )

    async def add(self, requirement: NewRequirement) -> Requirement:
        if requirement.origin is not None:
            existing = next(
                (r for r in self.by_id.values() if r.origin == requirement.origin and not r.is_deleted), None
            )
            if existing is not None:
                raise CandidateAlreadyPromoted(details={"requirement_id": str(existing.id)})
        numbers = [r.number for r in self.by_id.values() if r.project_id == requirement.project_id]
        now = self._clock()
        stored = Requirement(
            id=uuid.uuid7(),
            project_id=requirement.project_id,
            number=max(numbers, default=0) + 1,
            version=1,
            content=requirement.content,
            source=requirement.source,
            confidence=requirement.confidence,
            created_by_user_id=requirement.created_by_user_id,
            created_at=now,
            updated_at=now,
            origin=requirement.origin,
        )
        self.by_id[stored.id] = stored
        self.versions[stored.id] = [self._version_of(stored, None, requirement.created_by_user_id)]
        return stored

    async def get(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, for_update: bool = False
    ) -> Requirement | None:
        found = self.by_id.get(requirement_id)
        return found if found and found.project_id == project_id and not found.is_deleted else None

    async def save(self, revision: Revision) -> Requirement:
        requirement = revision.requirement
        assert self.by_id[requirement.id].version == requirement.version - 1
        stored = replace(requirement, updated_at=self._clock())
        self.by_id[stored.id] = stored
        self.versions[stored.id].append(
            self._version_of(stored, revision.change_reason, revision.author_user_id)
        )
        return stored

    async def save_deleted(self, requirement: Requirement) -> None:
        self.by_id[requirement.id] = requirement

    async def list_for_project(self, project_id: uuid.UUID, query: RequirementQuery) -> list[Requirement]:
        found = [r for r in self.by_id.values() if r.project_id == project_id and not r.is_deleted]
        for attr in ("type", "category", "status", "priority"):
            wanted = getattr(query, attr)
            if wanted is not None:
                found = [r for r in found if getattr(r.content, attr) == wanted]
        if query.search:
            term = query.search.lower()
            found = [
                r for r in found if term in r.content.title.lower() or term in r.content.statement.lower()
            ]
        found.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        if query.after is not None:
            after = (query.after.created_at, query.after.id)
            found = [r for r in found if (r.created_at, r.id) < after]
        return found[: query.limit]

    async def list_by_status(
        self, project_id: uuid.UUID, statuses: frozenset[RequirementStatus], *, limit: int
    ) -> list[Requirement]:
        found = [
            r
            for r in self.by_id.values()
            if r.project_id == project_id and not r.is_deleted and r.content.status in statuses
        ]
        return sorted(found, key=lambda r: r.number)[:limit]

    async def list_by_ids(self, project_id: uuid.UUID, requirement_ids: list[uuid.UUID]) -> list[Requirement]:
        return [r for i in requirement_ids if (r := await self.get(project_id, i)) is not None]

    async def list_versions(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, after: int | None, limit: int
    ) -> list[RequirementVersion]:
        if await self.get(project_id, requirement_id) is None:
            return []
        return [v for v in self.versions[requirement_id] if after is None or v.version > after][:limit]

    async def get_version(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, version: int
    ) -> RequirementVersion | None:
        versions = await self.list_versions(project_id, requirement_id, after=version - 1, limit=1)
        return versions[0] if versions and versions[0].version == version else None


class FakeRequirementSetRepository:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.by_id: dict[uuid.UUID, RequirementSet] = {}
        self.planning_inputs: dict[uuid.UUID, dict[str, Any]] = {}

    async def add(self, requirement_set: NewRequirementSet) -> RequirementSet:
        numbers = [s.number for s in self.by_id.values() if s.project_id == requirement_set.project_id]
        stored = RequirementSet(
            id=uuid.uuid7(),
            project_id=requirement_set.project_id,
            number=max(numbers, default=0) + 1,
            name=requirement_set.name,
            description=requirement_set.description,
            schema_version=requirement_set.schema_version,
            content_hash=requirement_set.content_hash,
            requirement_count=len(requirement_set.items),
            created_by_user_id=requirement_set.created_by_user_id,
            created_at=self._clock(),
            items=requirement_set.items,
        )
        self.by_id[stored.id] = stored
        self.planning_inputs[stored.id] = requirement_set.planning_input
        return stored

    async def get(self, project_id: uuid.UUID, set_id: uuid.UUID) -> RequirementSet | None:
        found = self.by_id.get(set_id)
        return found if found and found.project_id == project_id else None

    async def list_for_project(
        self, project_id: uuid.UUID, *, before_number: int | None, limit: int
    ) -> list[RequirementSet]:
        found = [
            replace(s, items=())
            for s in self.by_id.values()
            if s.project_id == project_id and (before_number is None or s.number < before_number)
        ]
        return sorted(found, key=lambda s: s.number, reverse=True)[:limit]

    async def get_planning_input(
        self, project_id: uuid.UUID, set_id: uuid.UUID
    ) -> tuple[RequirementSet, dict[str, Any]] | None:
        found = await self.get(project_id, set_id)
        return (replace(found, items=()), self.planning_inputs[set_id]) if found else None


class FakeRequirementAnalysisRepository:
    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.by_id: dict[uuid.UUID, RequirementAnalysis] = {}

    async def add(self, analysis: NewRequirementAnalysis) -> RequirementAnalysis:
        stored = RequirementAnalysis(
            id=uuid.uuid7(),
            project_id=analysis.project_id,
            raw_input=analysis.raw_input,
            input_sha256=analysis.input_sha256,
            engine_version=analysis.engine_version,
            result=analysis.result,
            created_by_user_id=analysis.created_by_user_id,
            created_at=self._clock(),
        )
        self.by_id[stored.id] = stored
        return stored

    async def get(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> RequirementAnalysis | None:
        found = self.by_id.get(analysis_id)
        return found if found and found.project_id == project_id else None


class FakeArchitectureRepository:
    """Many architectures per project, append-only revisions, a layout per architecture."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.architectures: dict[uuid.UUID, Architecture] = {}  # by id (deleted ones kept)
        self.revisions: dict[tuple[uuid.UUID, int], ArchitectureRevision] = {}  # (architecture, number)
        self.layouts: dict[uuid.UUID, ArchitectureLayout] = {}  # by architecture

    def _check_name(self, architecture: Architecture | NewArchitecture) -> None:
        for other in self.architectures.values():
            if (
                other.id != architecture.id
                and other.project_id == architecture.project_id
                and other.deleted_at is None
                and other.name.lower() == architecture.name.lower()
            ):
                raise ArchitectureNameTaken

    def _store(self, revision: NewRevision) -> ArchitectureRevision:
        key = (revision.architecture_id, revision.number)
        assert key not in self.revisions, "revisions are append-only"
        stored = ArchitectureRevision(
            id=uuid.uuid7(),
            created_at=self._clock(),
            **{f.name: getattr(revision, f.name) for f in dataclass_fields(revision)},
            snapshot=to_dict(revision.ir),
            stored_schema_version=revision.ir_schema_version,
        )
        self.revisions[key] = stored
        return stored

    async def add(
        self, architecture: NewArchitecture, first: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        self._check_name(architecture)
        stored = Architecture(
            id=architecture.id,
            project_id=architecture.project_id,
            name=architecture.name,
            description=architecture.description,
            status=ArchitectureStatus.ACTIVE,
            current_revision=first.number,
            created_by_user_id=architecture.created_by_user_id,
            updated_by_user_id=architecture.created_by_user_id,
            archived_at=None,
            deleted_at=None,
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        self.architectures[stored.id] = stored
        return stored, self._store(first)

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
    ) -> Architecture | None:
        found = self.architectures.get(architecture_id)
        return found if found and found.project_id == project_id and found.deleted_at is None else None

    async def list_for_project(self, project_id: uuid.UUID, query: ArchitectureQuery) -> list[Architecture]:
        found = [
            a
            for a in self.architectures.values()
            if a.project_id == project_id
            and a.deleted_at is None
            and (query.status is None or a.status is query.status)
            and (query.search is None or query.search.lower() in a.name.lower())
            and (query.after is None or (a.created_at, a.id) < query.after)
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)[: query.limit]

    async def save(self, architecture: Architecture) -> Architecture:
        self._check_name(architecture)
        saved = replace(architecture, updated_at=self._clock())
        self.architectures[architecture.id] = saved
        return saved

    async def add_revision(
        self, architecture: Architecture, revision: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        current = self.architectures[architecture.id]
        assert current.current_revision == revision.parent_number, "the parent must be current"
        stored = self._store(revision)
        moved = replace(
            current,
            current_revision=revision.number,
            updated_at=self._clock(),
            updated_by_user_id=revision.created_by_user_id,
        )
        self.architectures[architecture.id] = moved
        return moved, stored

    async def get_revision(self, architecture_id: uuid.UUID, number: int) -> ArchitectureRevision | None:
        return self.revisions.get((architecture_id, number))

    async def list_revisions(
        self, architecture_id: uuid.UUID, *, before: int | None, limit: int
    ) -> list[RevisionSummary]:
        found = sorted(
            (
                r
                for (a, n), r in self.revisions.items()
                if a == architecture_id and (before is None or n < before)
            ),
            key=lambda r: r.number,
            reverse=True,
        )
        return [
            RevisionSummary(
                number=r.number,
                parent_number=r.parent_number,
                restored_from=r.restored_from,
                source=r.source,
                summary=r.summary,
                reason=r.reason,
                content_hash=r.content_hash,
                ir_schema_version=r.ir_schema_version,
                requirement_set_id=r.requirement_set_id,
                created_by_user_id=r.created_by_user_id,
                created_at=r.created_at,
            )
            for r in found[:limit]
        ]

    async def get_layout(self, architecture_id: uuid.UUID) -> ArchitectureLayout:
        return self.layouts.get(architecture_id, ArchitectureLayout())

    async def save_layout(
        self, architecture: Architecture, positions: Mapping[str, Position], user_id: uuid.UUID
    ) -> ArchitectureLayout:
        layout = ArchitectureLayout(dict(positions), user_id, self._clock())
        self.layouts[architecture.id] = layout
        return layout


class FakeValidationRunRepository:
    """Keeps each run's report and findings, like the append-only tables."""

    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, RunReport] = {}
        self.findings: dict[uuid.UUID, tuple[Finding, ...]] = {}

    async def add(self, run: ValidationRun, inputs: RunInputs) -> RunReport:
        report = RunReport.of(run, inputs)
        self.reports[run.id] = report
        self.findings[run.id] = run.result.findings if run.result is not None else ()
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, run_id: uuid.UUID
    ) -> RunReport | None:
        report = self.reports.get(run_id)
        if report is None or (report.run.project_id, report.run.architecture_id) != (
            project_id,
            architecture_id,
        ):
            return None
        return report

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: RunQuery
    ) -> list[RunReport]:
        found = [
            r
            for r in self.reports.values()
            if (r.run.project_id, r.run.architecture_id) == (project_id, architecture_id)
            and (query.revision is None or r.run.revision_number == query.revision)
            and (query.after is None or (r.run.requested_at, r.run.id) < query.after)
        ]
        return sorted(found, key=lambda r: (r.run.requested_at, r.run.id), reverse=True)[: query.limit]

    async def list_findings(
        self, project_id: uuid.UUID, run_id: uuid.UUID, query: FindingQuery
    ) -> list[tuple[int, Finding]]:
        report = self.reports.get(run_id)
        if report is None or report.run.project_id != project_id:
            return []
        return [
            (position, f)
            for position, f in enumerate(self.findings.get(run_id, ()))
            if (query.after is None or position > query.after)
            and (query.severity is None or f.severity is query.severity)
            and (query.category is None or f.category is query.category)
            and (query.blocking is None or f.blocking is query.blocking)
            and (query.rule_id is None or f.rule_id == query.rule_id)
            and (query.entity_id is None or query.entity_id in f.entity_ids)
        ][: query.limit]


class FakeCapacityAnalysisRepository:
    """Keeps each analysis's report, components and bottlenecks, like the append-only tables."""

    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, AnalysisReport] = {}
        self.components: dict[uuid.UUID, tuple[ComponentResult, ...]] = {}
        self.bottlenecks: dict[uuid.UUID, tuple[Bottleneck, ...]] = {}

    async def add(
        self,
        analysis: CapacityAnalysis,
        inputs: Mapping[str, Any],
        scaling: tuple[ScalingOption, ...],
        unsupported_scaling: tuple[Unsupported, ...],
        scenarios: tuple[ScenarioOutcome, ...],
    ) -> AnalysisReport:
        report = AnalysisReport.of(analysis, inputs, scaling, unsupported_scaling, scenarios)
        self.reports[analysis.id] = report
        result = analysis.result
        self.components[analysis.id] = result.components if result else ()
        self.bottlenecks[analysis.id] = result.bottlenecks if result else ()
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> AnalysisReport | None:
        report = self.reports.get(analysis_id)
        if report is None or (report.analysis.project_id, report.analysis.architecture_id) != (
            project_id,
            architecture_id,
        ):
            return None
        return report

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: AnalysisQuery
    ) -> list[AnalysisReport]:
        found = [
            r
            for r in self.reports.values()
            if (r.analysis.project_id, r.analysis.architecture_id) == (project_id, architecture_id)
            and (query.revision is None or r.analysis.revision_number == query.revision)
            and (query.after is None or (r.analysis.requested_at, r.analysis.id) < query.after)
        ]
        return sorted(found, key=lambda r: (r.analysis.requested_at, r.analysis.id), reverse=True)[
            : query.limit
        ]

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ComponentQuery
    ) -> list[ComponentResult]:
        report = self.reports.get(analysis_id)
        if report is None or report.analysis.project_id != project_id:
            return []
        return [
            c
            for c in self.components[analysis_id]
            if (query.status is None or c.status is query.status)
            and (query.after is None or c.node_id > query.after)
        ][: query.limit]

    async def list_bottlenecks(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: BottleneckQuery
    ) -> list[Bottleneck]:
        report = self.reports.get(analysis_id)
        if report is None or report.analysis.project_id != project_id:
            return []
        return [
            b
            for b in self.bottlenecks[analysis_id]
            if query.certainty is None or b.certainty is query.certainty
        ]


class FakeUnitOfWork:
    def __init__(self, clock: FakeClock) -> None:
        self._users = FakeUserRepository(clock)
        self._email_verification_tokens = FakeTokenRepository()
        self._sessions = FakeSessionRepository()
        self._password_reset_tokens = FakeTokenRepository()
        self._organizations = FakeOrganizationRepository(clock)
        self._memberships = FakeMembershipRepository(self._organizations, clock)
        self._invitations = FakeInvitationRepository()
        self._audit = FakeAuditRepository()
        self._projects = FakeProjectRepository(clock, self._memberships)
        self._requirements = FakeRequirementRepository(clock)
        self._requirement_sets = FakeRequirementSetRepository(clock)
        self._requirement_analyses = FakeRequirementAnalysisRepository(clock)
        self._architectures = FakeArchitectureRepository(clock)
        self._validations = FakeValidationRunRepository()
        self._capacity = FakeCapacityAnalysisRepository()
        self.commits = 0
        self.rollbacks = 0

    @property
    def users(self) -> FakeUserRepository:
        return self._users

    @property
    def email_verification_tokens(self) -> FakeTokenRepository:
        return self._email_verification_tokens

    @property
    def sessions(self) -> FakeSessionRepository:
        return self._sessions

    @property
    def password_reset_tokens(self) -> FakeTokenRepository:
        return self._password_reset_tokens

    @property
    def organizations(self) -> FakeOrganizationRepository:
        return self._organizations

    @property
    def memberships(self) -> FakeMembershipRepository:
        return self._memberships

    @property
    def invitations(self) -> FakeInvitationRepository:
        return self._invitations

    @property
    def audit(self) -> FakeAuditRepository:
        return self._audit

    @property
    def projects(self) -> FakeProjectRepository:
        return self._projects

    @property
    def requirements(self) -> FakeRequirementRepository:
        return self._requirements

    @property
    def requirement_sets(self) -> FakeRequirementSetRepository:
        return self._requirement_sets

    @property
    def requirement_analyses(self) -> FakeRequirementAnalysisRepository:
        return self._requirement_analyses

    @property
    def architectures(self) -> FakeArchitectureRepository:
        return self._architectures

    @property
    def validations(self) -> FakeValidationRunRepository:
        return self._validations

    @property
    def capacity(self) -> FakeCapacityAnalysisRepository:
        return self._capacity

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        # Audit entries share the transaction: kept on commit, discarded on rollback.
        if exc_type is None:
            self.commits += 1
            self._audit.events.extend(self._audit.pending)
        else:
            self.rollbacks += 1
        self._audit.pending.clear()


@dataclass(frozen=True)
class SentEmail:
    kind: str
    to: str
    token: str | None = None


class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    async def send_email_verification(self, *, to: str, name: str, token: str) -> None:
        self.sent.append(SentEmail("verification", to, token))

    async def send_account_exists(self, *, to: str, name: str) -> None:
        self.sent.append(SentEmail("account_exists", to))

    async def send_password_reset(self, *, to: str, name: str, token: str) -> None:
        self.sent.append(SentEmail("password_reset", to, token))

    async def send_password_changed(self, *, to: str, name: str) -> None:
        self.sent.append(SentEmail("password_changed", to))

    async def send_invitation(
        self, *, to: str, inviter_name: str, organization_name: str, role: str, token: str
    ) -> None:
        self.sent.append(SentEmail("invitation", to, token))
