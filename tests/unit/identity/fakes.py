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
from core.domain.architecture_agent.records import run_document, run_from
from core.domain.architecture_agent.repository import RunListing as AgentRunListing
from core.domain.architecture_agent.runs import AgentRun
from core.domain.architecture_agent.values import RunStatus as AgentRunStatus
from core.domain.architecture_diff.diffs import ArchitectureDiff
from core.domain.architecture_diff.explanations import ExplanationRun
from core.domain.architecture_diff.records import (
    diff_document,
    diff_from,
    explanation_document,
    explanation_run_from,
)
from core.domain.architecture_diff.repository import DiffListing
from core.domain.architecture_workflow.candidates import WorkflowCandidate
from core.domain.architecture_workflow.records import (
    candidate_document,
    candidate_from,
    workflow_document,
    workflow_from,
)
from core.domain.architecture_workflow.repository import WorkflowListing
from core.domain.architecture_workflow.steps import WorkflowStep
from core.domain.architecture_workflow.values import WorkflowStatus
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow
from core.domain.audit.entities import AuditCursor, AuditEntry, AuditEvent
from core.domain.capacity.analyses import AnalysisReport, CapacityAnalysis
from core.domain.capacity.queries import AnalysisQuery, BottleneckQuery, ComponentQuery
from core.domain.capacity.results import Bottleneck, ComponentResult, Unsupported
from core.domain.capacity.scenarios import ScalingOption, ScenarioOutcome
from core.domain.cost.pricing import PricingRecord, PricingSnapshot
from core.domain.cost.queries import (
    CostAnalysisQuery,
    LineItemQuery,
    RecordQuery,
    SnapshotQuery,
    SnapshotSummary,
)
from core.domain.cost.reports import CostReport
from core.domain.cost.results import LineItem
from core.domain.decisions.entities import Decision, DecisionStatus
from core.domain.discovery.runs import DiscoveryRun, RunListing
from core.domain.discovery.values import RunStatus
from core.domain.drift.analyses import DriftAnalysis
from core.domain.drift.identity import IdentityMapping
from core.domain.drift.items import DriftItem
from core.domain.drift.values import AnalysisStatus as DriftStatus
from core.domain.drift.values import ReviewStatus
from core.domain.evolution.candidates import Candidate
from core.domain.evolution.queries import CandidateQuery, EvolutionQuery
from core.domain.evolution.reports import EvolutionReport
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
from core.domain.knowledge.errors import KnowledgeSourceExists
from core.domain.knowledge.ingestion import IngestionRun
from core.domain.knowledge.ports import Indexed
from core.domain.knowledge.retrieval import Candidate as KnowledgeCandidate
from core.domain.knowledge.retrieval import RetrievalQuery, Scope
from core.domain.knowledge.sources import KnowledgeSource
from core.domain.knowledge.values import IndexStatus as KnowledgeStatus
from core.domain.knowledge.values import Lifecycle as KnowledgeLifecycle
from core.domain.knowledge.values import SourceType as KnowledgeSourceType
from core.domain.migrations.entities import MigrationPlanVersion
from core.domain.migrations.values import PlanStatus
from core.domain.observability.queries import (
    ObservabilityAnalysisQuery,
    ObservabilityComponentQuery,
    ObservabilityFindingQuery,
)
from core.domain.observability.reports import ObservabilityReport
from core.domain.observability.results import ComponentResult as ObservabilityComponent
from core.domain.observability.results import ObservabilityFinding
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
from core.domain.reliability.queries import (
    ReliabilityAnalysisQuery,
    ReliabilityComponentQuery,
    ReliabilityFindingQuery,
)
from core.domain.reliability.reports import ReliabilityReport
from core.domain.reliability.results import ComponentResult as ReliabilityComponent
from core.domain.reliability.results import ReliabilityFinding
from core.domain.requirements.analyses import NewRequirementAnalysis, RequirementAnalysis
from core.domain.requirements.entities import NewRequirement, Requirement, RequirementVersion, Revision
from core.domain.requirements.enums import RequirementStatus
from core.domain.requirements.errors import CandidateAlreadyPromoted
from core.domain.requirements.queries import RequirementQuery
from core.domain.requirements.requirement_sets import NewRequirementSet, RequirementSet
from core.domain.security.queries import SecurityAnalysisQuery, SecurityComponentQuery, SecurityFindingQuery
from core.domain.security.reports import SecurityReport
from core.domain.security.results import ComponentResult as SecurityComponent
from core.domain.security.results import SecurityFinding
from core.domain.simulations.queries import SimulationComponentQuery, SimulationDeltaQuery, SimulationQuery
from core.domain.simulations.reports import SimulationReport
from core.domain.simulations.results import ComponentOutcome, Delta
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


class FakePricingSnapshotRepository:
    """Keeps each snapshot whole, like the append-only tables."""

    def __init__(self) -> None:
        self.snapshots: dict[uuid.UUID, PricingSnapshot] = {}

    async def add(self, snapshot: PricingSnapshot) -> None:
        self.snapshots[snapshot.id] = snapshot

    async def get(self, organization_id: uuid.UUID, snapshot_id: uuid.UUID) -> PricingSnapshot | None:
        snapshot = self.snapshots.get(snapshot_id)
        return snapshot if snapshot is not None and snapshot.organization_id == organization_id else None

    async def get_summary(self, organization_id: uuid.UUID, snapshot_id: uuid.UUID) -> SnapshotSummary | None:
        snapshot = await self.get(organization_id, snapshot_id)
        return SnapshotSummary.of(snapshot) if snapshot else None

    async def list_for_organization(
        self, organization_id: uuid.UUID, query: SnapshotQuery
    ) -> list[SnapshotSummary]:
        found = [
            SnapshotSummary.of(s)
            for s in self.snapshots.values()
            if s.organization_id == organization_id
            and (query.after is None or (s.created_at, s.id) < query.after)
        ]
        return sorted(found, key=lambda s: (s.created_at, s.id), reverse=True)[: query.limit]

    async def list_records(
        self, organization_id: uuid.UUID, snapshot_id: uuid.UUID, query: RecordQuery
    ) -> list[PricingRecord]:
        snapshot = await self.get(organization_id, snapshot_id)
        if snapshot is None:
            return []
        return sorted(
            (
                r
                for r in snapshot.records
                if (query.provider is None or r.provider == query.provider)
                and (query.service is None or r.service == query.service)
                and (query.region is None or r.region == query.region)
                and (query.after is None or r.id > query.after)
            ),
            key=lambda r: r.id,
        )[: query.limit]


class FakeCostAnalysisRepository:
    """Keeps each analysis's report and line items, like the append-only tables."""

    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, CostReport] = {}
        self.line_items: dict[uuid.UUID, tuple[LineItem, ...]] = {}

    async def add(self, report: CostReport, line_items: tuple[LineItem, ...]) -> CostReport:
        self.reports[report.analysis.id] = report
        self.line_items[report.analysis.id] = line_items
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> CostReport | None:
        report = self.reports.get(analysis_id)
        if report is None or (report.analysis.project_id, report.analysis.architecture_id) != (
            project_id,
            architecture_id,
        ):
            return None
        return report

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: CostAnalysisQuery
    ) -> list[CostReport]:
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

    async def list_line_items(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: LineItemQuery
    ) -> list[LineItem]:
        report = self.reports.get(analysis_id)
        if report is None or report.analysis.project_id != project_id:
            return []
        return [
            line
            for line in sorted(self.line_items[analysis_id], key=lambda x: (x.element_id, x.resource))
            if (query.element_id is None or line.element_id == query.element_id)
            and (query.status is None or line.status is query.status)
            and (query.category is None or line.category is query.category)
            and (query.after is None or (line.element_id, line.resource) > query.after)
        ][: query.limit]


class FakeReliabilityAnalysisRepository:
    """Keeps each analysis's report, components and findings, like the append-only tables."""

    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, ReliabilityReport] = {}
        self.components: dict[uuid.UUID, tuple[ReliabilityComponent, ...]] = {}
        self.findings: dict[uuid.UUID, tuple[ReliabilityFinding, ...]] = {}

    async def add(
        self,
        report: ReliabilityReport,
        components: tuple[ReliabilityComponent, ...],
        findings: tuple[ReliabilityFinding, ...],
    ) -> ReliabilityReport:
        self.reports[report.analysis.id] = report
        self.components[report.analysis.id] = components
        self.findings[report.analysis.id] = findings
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> ReliabilityReport | None:
        report = self.reports.get(analysis_id)
        if report is None or (report.analysis.project_id, report.analysis.architecture_id) != (
            project_id,
            architecture_id,
        ):
            return None
        return report

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: ReliabilityAnalysisQuery
    ) -> list[ReliabilityReport]:
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
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ReliabilityComponentQuery
    ) -> list[ReliabilityComponent]:
        report = self.reports.get(analysis_id)
        if report is None or report.analysis.project_id != project_id:
            return []
        return [
            c
            for c in sorted(self.components[analysis_id], key=lambda c: c.node_id)
            if (query.status is None or c.status is query.status)
            and (query.after is None or c.node_id > query.after)
        ][: query.limit]

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ReliabilityFindingQuery
    ) -> list[tuple[int, ReliabilityFinding]]:
        report = self.reports.get(analysis_id)
        if report is None or report.analysis.project_id != project_id:
            return []
        return [
            (position, f)
            for position, f in enumerate(self.findings[analysis_id])
            if (query.severity is None or f.severity is query.severity)
            and (query.type is None or f.type is query.type)
            and (query.certainty is None or f.certainty is query.certainty)
            and (query.after is None or position > query.after)
        ][: query.limit]


class _FakeAnalysisRows[Report: (SecurityReport, ObservabilityReport), Component, Finding]:
    """Keeps each analysis's report, components and findings, like the append-only tables of the
    security and observability analyses."""

    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, Report] = {}
        self.components: dict[uuid.UUID, tuple[Component, ...]] = {}
        self.findings: dict[uuid.UUID, tuple[Finding, ...]] = {}

    async def add(
        self, report: Report, components: tuple[Component, ...], findings: tuple[Finding, ...]
    ) -> Report:
        self.reports[report.analysis.id] = report
        self.components[report.analysis.id] = components
        self.findings[report.analysis.id] = findings
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> Report | None:
        report = self.reports.get(analysis_id)
        if report is None or (report.analysis.project_id, report.analysis.architecture_id) != (
            project_id,
            architecture_id,
        ):
            return None
        return report

    async def list_for_architecture(
        self,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        query: SecurityAnalysisQuery | ObservabilityAnalysisQuery,
    ) -> list[Report]:
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

    def _of(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> bool:
        report = self.reports.get(analysis_id)
        return report is not None and report.analysis.project_id == project_id


class FakeSecurityAnalysisRepository(_FakeAnalysisRows[SecurityReport, SecurityComponent, SecurityFinding]):
    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: SecurityComponentQuery
    ) -> list[SecurityComponent]:
        if not self._of(project_id, analysis_id):
            return []
        return [
            c
            for c in sorted(self.components[analysis_id], key=lambda c: c.node_id)
            if (query.coverage is None or c.coverage is query.coverage)
            and (query.after is None or c.node_id > query.after)
        ][: query.limit]

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: SecurityFindingQuery
    ) -> list[tuple[int, SecurityFinding]]:
        if not self._of(project_id, analysis_id):
            return []
        return [
            (position, f)
            for position, f in enumerate(self.findings[analysis_id])
            if (query.severity is None or f.severity is query.severity)
            and (query.type is None or f.type is query.type)
            and (query.category is None or f.category is query.category)
            and (query.basis is None or f.basis is query.basis)
            and (query.certainty is None or f.certainty is query.certainty)
            and (query.threat is None or f.threat is query.threat)
            and (query.after is None or position > query.after)
        ][: query.limit]


class FakeObservabilityAnalysisRepository(
    _FakeAnalysisRows[ObservabilityReport, ObservabilityComponent, ObservabilityFinding]
):
    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ObservabilityComponentQuery
    ) -> list[ObservabilityComponent]:
        if not self._of(project_id, analysis_id):
            return []
        criticality = None if query.criticality == "not_modeled" else query.criticality
        return [
            c
            for c in sorted(self.components[analysis_id], key=lambda c: c.node_id)
            if (query.criticality is None or c.criticality == criticality)
            and (query.dimension is None or c.coverage[query.dimension] is query.state)
            and (query.after is None or c.node_id > query.after)
        ][: query.limit]

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ObservabilityFindingQuery
    ) -> list[tuple[int, ObservabilityFinding]]:
        if not self._of(project_id, analysis_id):
            return []
        return [
            (position, f)
            for position, f in enumerate(self.findings[analysis_id])
            if (query.severity is None or f.severity is query.severity)
            and (query.type is None or f.type is query.type)
            and (query.category is None or f.category is query.category)
            and (query.basis is None or f.basis is query.basis)
            and (query.certainty is None or f.certainty is query.certainty)
            and (query.dimension is None or f.dimension is query.dimension)
            and (query.after is None or position > query.after)
        ][: query.limit]


class FakeSimulationRepository:
    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, SimulationReport] = {}
        self.components: dict[uuid.UUID, tuple[ComponentOutcome, ...]] = {}
        self.deltas: dict[uuid.UUID, tuple[Delta, ...]] = {}

    async def add(
        self, report: SimulationReport, components: tuple[ComponentOutcome, ...], deltas: tuple[Delta, ...]
    ) -> SimulationReport:
        self.reports[report.simulation.id] = report
        self.components[report.simulation.id] = components
        self.deltas[report.simulation.id] = deltas
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, simulation_id: uuid.UUID
    ) -> SimulationReport | None:
        report = self.reports.get(simulation_id)
        if report is None or (report.simulation.project_id, report.simulation.architecture_id) != (
            project_id,
            architecture_id,
        ):
            return None
        return report

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: SimulationQuery
    ) -> list[SimulationReport]:
        found = [
            r
            for r in self.reports.values()
            if (r.simulation.project_id, r.simulation.architecture_id) == (project_id, architecture_id)
            and (query.revision is None or r.simulation.revision_number == query.revision)
            and (query.after is None or (r.simulation.requested_at, r.simulation.id) < query.after)
        ]
        return sorted(found, key=lambda r: (r.simulation.requested_at, r.simulation.id), reverse=True)[
            : query.limit
        ]

    def _of(self, project_id: uuid.UUID, simulation_id: uuid.UUID) -> bool:
        report = self.reports.get(simulation_id)
        return report is not None and report.simulation.project_id == project_id

    async def list_components(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID, query: SimulationComponentQuery
    ) -> list[ComponentOutcome]:
        if not self._of(project_id, simulation_id):
            return []
        return [
            c
            for c in sorted(self.components[simulation_id], key=lambda c: c.node_id)
            if (query.unavailable is None or c.unavailable is query.unavailable)
            and (query.impact is None or c.impact is query.impact)
            and (query.after is None or c.node_id > query.after)
        ][: query.limit]

    async def list_deltas(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID, query: SimulationDeltaQuery
    ) -> list[tuple[int, Delta]]:
        if not self._of(project_id, simulation_id):
            return []
        return [
            (position, d)
            for position, d in enumerate(self.deltas[simulation_id])
            if (query.analysis is None or d.analysis is query.analysis)
            and (query.element_id is None or d.element_id == query.element_id)
            and (query.comparable is None or d.comparable is query.comparable)
            and (query.after is None or position > query.after)
        ][: query.limit]

    async def rows(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID
    ) -> tuple[tuple[ComponentOutcome, ...], tuple[Delta, ...]]:
        if not self._of(project_id, simulation_id):
            return (), ()
        return tuple(sorted(self.components[simulation_id], key=lambda c: c.node_id)), self.deltas[
            simulation_id
        ]


class FakeEvolutionRepository:
    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, EvolutionReport] = {}
        self.stored: dict[uuid.UUID, tuple[Candidate, ...]] = {}

    async def add(self, report: EvolutionReport, candidates: tuple[Candidate, ...]) -> EvolutionReport:
        self.reports[report.analysis.id] = report
        self.stored[report.analysis.id] = candidates
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> EvolutionReport | None:
        report = self.reports.get(analysis_id)
        if report is None or (report.analysis.project_id, report.analysis.architecture_id) != (
            project_id,
            architecture_id,
        ):
            return None
        return report

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: EvolutionQuery
    ) -> list[EvolutionReport]:
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

    def _of(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> bool:
        report = self.reports.get(analysis_id)
        return report is not None and report.analysis.project_id == project_id

    async def list_candidates(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: CandidateQuery
    ) -> list[tuple[int, Candidate]]:
        if not self._of(project_id, analysis_id):
            return []
        return [
            (position, c)
            for position, c in enumerate(self.stored[analysis_id])
            if (query.category is None or c.category is query.category)
            and (query.validation is None or c.validation is query.validation)
            and (query.goal is None or query.goal in c.goals)
            and (query.after is None or position > query.after)
        ][: query.limit]

    async def get_candidate(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, candidate_id: str
    ) -> Candidate | None:
        if not self._of(project_id, analysis_id):
            return None
        return next((c for c in self.stored[analysis_id] if c.id == candidate_id), None)

    async def candidates(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> tuple[Candidate, ...]:
        return self.stored[analysis_id] if self._of(project_id, analysis_id) else ()


class FakeDecisionRepository:
    def __init__(self) -> None:
        self.decisions: dict[uuid.UUID, Decision] = {}

    async def add(self, decision: Decision) -> Decision:
        self.decisions[decision.id] = decision
        return decision

    async def update(self, decision: Decision) -> Decision:
        assert decision.id in self.decisions
        self.decisions[decision.id] = decision
        return decision

    async def get(self, project_id: uuid.UUID, decision_id: uuid.UUID) -> Decision | None:
        decision = self.decisions.get(decision_id)
        return decision if decision is not None and decision.project_id == project_id else None

    async def next_number(self, project_id: uuid.UUID) -> int:
        return max((d.number for d in self.decisions.values() if d.project_id == project_id), default=0) + 1

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: DecisionStatus | None = None,
        after: int | None = None,
        limit: int = 50,
    ) -> list[Decision]:
        found = [
            d
            for d in self.decisions.values()
            if d.project_id == project_id
            and (architecture_id is None or d.architecture_id == architecture_id)
            and (status is None or d.status is status)
            and (after is None or d.number > after)
        ]
        return sorted(found, key=lambda d: d.number)[:limit]


class FakeMigrationPlanRepository:
    def __init__(self) -> None:
        self.versions: dict[uuid.UUID, MigrationPlanVersion] = {}

    async def add(self, version: MigrationPlanVersion) -> MigrationPlanVersion:
        self.versions[version.id] = version
        return version

    async def update_review(self, version: MigrationPlanVersion) -> MigrationPlanVersion:
        assert version.id in self.versions
        self.versions[version.id] = version
        return version

    async def get(
        self,
        project_id: uuid.UUID,
        plan_id: uuid.UUID,
        version: int | None = None,
        *,
        for_update: bool = False,
    ) -> MigrationPlanVersion | None:
        found = [
            v
            for v in self.versions.values()
            if v.project_id == project_id
            and v.plan_id == plan_id
            and (version is None or v.version == version)
        ]
        return max(found, key=lambda v: v.version, default=None)

    async def history(self, project_id: uuid.UUID, plan_id: uuid.UUID) -> list[MigrationPlanVersion]:
        found = [v for v in self.versions.values() if v.project_id == project_id and v.plan_id == plan_id]
        return sorted(found, key=lambda v: v.version)

    async def list_latest(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: PlanStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[MigrationPlanVersion]:
        found = [
            v
            for v in self.versions.values()
            if v.project_id == project_id
            and v.status is not PlanStatus.SUPERSEDED
            and (architecture_id is None or v.request.architecture_id == architecture_id)
            and (status is None or v.status is status)
            and (after is None or (v.created_at, v.id) < after)
        ]
        return sorted(found, key=lambda v: (v.created_at, v.id), reverse=True)[:limit]


class FakeDiscoveryRunRepository:
    def __init__(self) -> None:
        self.runs: dict[uuid.UUID, DiscoveryRun] = {}

    async def add(self, run: DiscoveryRun) -> DiscoveryRun:
        self.runs[run.id] = run
        return run

    async def update_review(self, run: DiscoveryRun) -> DiscoveryRun:
        assert run.id in self.runs
        self.runs[run.id] = run
        return run

    async def get(
        self, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
    ) -> DiscoveryRun | None:
        found = self.runs.get(run_id)
        return found if found is not None and found.project_id == project_id else None

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: RunStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[RunListing]:
        found = [
            r
            for r in self.runs.values()
            if r.project_id == project_id
            and (status is None or r.status is status)
            and (after is None or (r.requested_at, r.id) < after)
        ]
        ordered = sorted(found, key=lambda r: (r.requested_at, r.id), reverse=True)[:limit]
        return [
            RunListing(
                r.id,
                r.project_id,
                r.status,
                r.requested_by_user_id,
                r.requested_at,
                r.source_type,
                r.baseline,
                r.label,
                r.completed_at,
                r.result.summary() if r.result else None,
                r.result.fingerprint if r.result else None,
                r.result.sources_fingerprint if r.result else None,
                r.error,
                len(r.decisions),
                len(r.acceptances),
            )
            for r in ordered
        ]

    async def accepted_for(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID
    ) -> tuple[DiscoveryRun, ...]:
        found = [
            r
            for r in self.runs.values()
            if r.project_id == project_id and any(a.architecture_id == architecture_id for a in r.acceptances)
        ]
        return tuple(sorted(found, key=lambda r: (r.requested_at, r.id)))

    async def delete(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        found = self.runs.get(run_id)
        if found is not None and found.project_id == project_id:
            del self.runs[run_id]


class FakeDriftRepository:
    def __init__(self) -> None:
        self.analyses: dict[uuid.UUID, DriftAnalysis] = {}
        self.items: dict[uuid.UUID, DriftItem] = {}
        self.identity: list[tuple[uuid.UUID, IdentityMapping]] = []

    async def add_analysis(self, analysis: DriftAnalysis) -> DriftAnalysis:
        assert analysis.id not in self.analyses  # append-only
        self.analyses[analysis.id] = analysis
        return analysis

    async def get_analysis(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> DriftAnalysis | None:
        found = self.analyses.get(analysis_id)
        return found if found is not None and found.project_id == project_id else None

    async def list_analyses(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: DriftStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[DriftAnalysis]:
        found = [
            a
            for a in self.analyses.values()
            if a.project_id == project_id
            and (architecture_id is None or a.request.architecture_id == architecture_id)
            and (status is None or a.status is status)
            and (after is None or (a.requested_at, a.id) < after)
        ]
        return sorted(found, key=lambda a: (a.requested_at, a.id), reverse=True)[:limit]

    async def uses_discovery_run(self, project_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        return any(
            a.project_id == project_id and a.request.discovery_run_id == run_id
            for a in self.analyses.values()
        )

    async def add_items(self, items: tuple[DriftItem, ...]) -> None:
        for item in items:
            assert item.id not in self.items
            assert not any(
                i.architecture_id == item.architecture_id and i.key == item.key for i in self.items.values()
            )
            self.items[item.id] = item

    async def save_item(self, item: DriftItem) -> DriftItem:
        stored = self.items[item.id]
        assert (stored.key, stored.first_analysis_id) == (item.key, item.first_analysis_id)
        assert item.history[: len(stored.history)] == stored.history  # the history only grows
        self.items[item.id] = item
        return item

    async def get_item(
        self, project_id: uuid.UUID, item_id: uuid.UUID, *, for_update: bool = False
    ) -> DriftItem | None:
        found = self.items.get(item_id)
        return found if found is not None and found.project_id == project_id else None

    async def items_of(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
    ) -> list[DriftItem]:
        return sorted(
            (
                i
                for i in self.items.values()
                if i.project_id == project_id and i.architecture_id == architecture_id
            ),
            key=lambda i: i.id,
        )

    async def list_items(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: ReviewStatus | None = None,
        after: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[DriftItem]:
        found = [
            i
            for i in self.items.values()
            if i.project_id == project_id
            and (architecture_id is None or i.architecture_id == architecture_id)
            and (status is None or i.status is status)
            and (after is None or i.id > after)
        ]
        return sorted(found, key=lambda i: i.id)[:limit]

    async def add_mapping(self, project_id: uuid.UUID, mapping: IdentityMapping) -> IdentityMapping:
        self.identity.append((project_id, mapping))
        return mapping

    async def mappings(self, project_id: uuid.UUID, architecture_id: uuid.UUID) -> list[IdentityMapping]:
        found = [m for p, m in self.identity if p == project_id and m.architecture_id == architecture_id]
        return sorted(found, key=lambda m: m.confirmed_at)


class FakeKnowledgeRepository:
    """In memory, with the SQL repository's scoping and prefilter (shared terms or named identifiers)."""

    def __init__(self) -> None:
        self.sources: dict[uuid.UUID, KnowledgeSource] = {}
        self.runs: dict[uuid.UUID, IngestionRun] = {}
        self.versions: dict[tuple[uuid.UUID, int], Indexed] = {}
        self.terms: dict[tuple[uuid.UUID, int, str], tuple[str, ...]] = {}

    def _active(self, source: KnowledgeSource) -> bool:
        return source.lifecycle is KnowledgeLifecycle.ACTIVE

    async def add_source(self, source: KnowledgeSource) -> KnowledgeSource:
        for other in self.sources.values():
            same = (source.path is not None and other.path == source.path) or (
                source.record is not None and other.record is not None
                and other.record.record_id == source.record.record_id
            )  # fmt: skip
            if other.project_id == source.project_id and self._active(other) and same:
                raise KnowledgeSourceExists(details={})
        self.sources[source.id] = source
        return source

    async def save_source(self, source: KnowledgeSource) -> KnowledgeSource:
        stored = self.sources[source.id]
        assert (stored.project_id, stored.type, stored.path) == (source.project_id, source.type, source.path)
        assert (stored.indexed_version or 0) <= (source.indexed_version or 0)  # never goes back
        self.sources[source.id] = source
        return source

    async def get_source(
        self, project_id: uuid.UUID, source_id: uuid.UUID, *, for_update: bool = False
    ) -> KnowledgeSource | None:
        found = self.sources.get(source_id)
        return found if found is not None and found.project_id == project_id else None

    async def find_source(
        self, project_id: uuid.UUID, *, path: str | None = None, record_id: uuid.UUID | None = None
    ) -> KnowledgeSource | None:
        for source in self.sources.values():
            if source.project_id != project_id or not self._active(source):
                continue
            if path is not None and source.path == path:
                return source
            if record_id is not None and source.record is not None and source.record.record_id == record_id:
                return source
        return None

    async def list_sources(
        self,
        project_id: uuid.UUID,
        *,
        status: KnowledgeStatus | None = None,
        source_type: KnowledgeSourceType | None = None,
        lifecycle: KnowledgeLifecycle | None = KnowledgeLifecycle.ACTIVE,
        after: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[KnowledgeSource]:
        found = [
            s
            for s in self.sources.values()
            if s.project_id == project_id
            and (status is None or s.status is status)
            and (source_type is None or s.type is source_type)
            and (lifecycle is None or s.lifecycle is lifecycle)
            and (after is None or s.id > after)
        ]
        return sorted(found, key=lambda s: s.id)[:limit]

    async def add_run(self, run: IngestionRun) -> IngestionRun:
        assert run.id not in self.runs  # written once
        self.runs[run.id] = run
        return run

    async def get_run(
        self, project_id: uuid.UUID, source_id: uuid.UUID, run_id: uuid.UUID
    ) -> IngestionRun | None:
        found = self.runs.get(run_id)
        ok = found is not None and (found.project_id, found.source_id) == (project_id, source_id)
        return found if ok else None

    async def list_runs(
        self, project_id: uuid.UUID, source_id: uuid.UUID, *, after: uuid.UUID | None = None, limit: int = 50
    ) -> list[IngestionRun]:
        found = [
            r
            for r in self.runs.values()
            if (r.project_id, r.source_id) == (project_id, source_id) and (after is None or r.id < after)
        ]
        return sorted(found, key=lambda r: r.id, reverse=True)[:limit]

    async def add_version(
        self, project_id: uuid.UUID, indexed: Indexed, terms: Mapping[str, tuple[str, ...]]
    ) -> None:
        key = (indexed.version.source_id, indexed.version.number)
        assert key not in self.versions  # append-only
        assert indexed.version.ingestion_run_id in self.runs  # the run is stored first
        self.versions[key] = indexed
        for chunk in indexed.chunks:
            self.terms[(*key, chunk.id)] = terms.get(chunk.id, ())

    def _in_scope(self, project_id: uuid.UUID, query: RetrievalQuery) -> list[KnowledgeSource]:
        return [
            s
            for s in self.sources.values()
            if s.project_id == project_id
            and self._active(s)
            and (not query.source_ids or s.id in query.source_ids)
            and (not query.source_types or s.type in query.source_types)
        ]

    async def scope(self, project_id: uuid.UUID, query: RetrievalQuery) -> Scope:
        sources = self._in_scope(project_id, query)
        stale = [s for s in sources if s.status is KnowledgeStatus.STALE]
        searched = [
            s for s in sources if s.indexed_version is not None and (query.include_stale or s not in stale)
        ]
        not_indexed = [s for s in sources if s.indexed_version is None]
        return Scope(project_id, len(searched), len(not_indexed), 0 if query.include_stale else len(stale))

    def _candidate(self, source: KnowledgeSource, indexed: Indexed, chunk_id: str) -> KnowledgeCandidate:
        chunk = next(c for c in indexed.chunks if c.id == chunk_id)
        document = indexed.document
        return KnowledgeCandidate(
            source.project_id, chunk, source.name, source.type, source.status is KnowledgeStatus.STALE,
            document.verification, document.record_status,
        )  # fmt: skip

    async def candidates(
        self, project_id: uuid.UUID, query: RetrievalQuery, terms: tuple[str, ...], limit: int
    ) -> list[KnowledgeCandidate]:
        found: list[KnowledgeCandidate] = []
        for source in self._in_scope(project_id, query):
            if source.indexed_version is None:
                continue
            if not query.include_stale and source.status is KnowledgeStatus.STALE:
                continue
            indexed = self.versions[(source.id, source.indexed_version)]
            for chunk in indexed.chunks:
                stored = self.terms[(source.id, source.indexed_version, chunk.id)]
                if set(chunk.identifiers) & set(query.identifiers) or set(stored) & set(terms):
                    found.append(self._candidate(source, indexed, chunk.id))
        return found[:limit]

    async def chunk(
        self, project_id: uuid.UUID, source_id: uuid.UUID, chunk_id: str, version: int | None = None
    ) -> KnowledgeCandidate | None:
        source = await self.get_source(project_id, source_id)
        if source is None or not self._active(source):
            return None
        number = version or source.indexed_version
        indexed = self.versions.get((source_id, number)) if number else None
        if indexed is None or not any(c.id == chunk_id for c in indexed.chunks):
            return None
        return self._candidate(source, indexed, chunk_id)


class FakeAgentRunRepository:
    """Runs kept as the database keeps them: written and read through the same record documents."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, dict[str, Any]] = {}

    async def add(self, run: AgentRun) -> AgentRun:
        self.rows[run.id] = run_document(run)
        return run

    async def save(self, run: AgentRun) -> AgentRun:
        if run.id not in self.rows:
            raise LookupError(run.id)
        self.rows[run.id] = run_document(run)
        return run

    async def get(
        self, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
    ) -> AgentRun | None:
        row = self.rows.get(run_id)
        return run_from(row) if row is not None and row["project_id"] == project_id else None

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: AgentRunStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[AgentRunListing]:
        runs = [run_from(r) for r in self.rows.values() if r["project_id"] == project_id]
        runs = [r for r in runs if status is None or r.status is status]
        runs.sort(key=lambda r: (r.requested_at, r.id), reverse=True)
        if after is not None:
            runs = [r for r in runs if (r.requested_at, r.id) < after]
        return [
            AgentRunListing(
                r.id, r.request.requirement_set_id,
                r.request.base.architecture_id if r.request.base else None,
                r.request.base.number if r.request.base else None, r.status, r.stage, r.model,
                r.failure.code if r.failure else None, r.candidate.content_hash if r.candidate else None,
                r.accepted.architecture_id if r.accepted else None, r.accepted.number if r.accepted else None,
                r.requested_by_user_id, r.requested_at, r.completed_at,
            )
            for r in runs[:limit]
        ]  # fmt: skip


class FakeArchitectureDiffRepository:
    """Diffs and explanation runs kept as the database keeps them: appended, read back from documents."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, dict[str, Any]] = {}
        self.explanations: list[dict[str, Any]] = []

    async def add(self, diff: ArchitectureDiff) -> ArchitectureDiff:
        if diff.id in self.rows:
            raise LookupError(diff.id)  # append-only
        self.rows[diff.id] = diff_document(diff)
        return diff

    async def get(self, project_id: uuid.UUID, diff_id: uuid.UUID) -> ArchitectureDiff | None:
        row = self.rows.get(diff_id)
        return diff_from(row) if row is not None and row["project_id"] == project_id else None

    async def add_explanation(self, project_id: uuid.UUID, run: ExplanationRun) -> ExplanationRun:
        row = self.rows.get(run.diff_id)
        if row is None or row["project_id"] != project_id:
            raise LookupError(run.diff_id)  # the foreign key
        self.explanations.append(explanation_document(project_id, run))
        return run

    async def list_explanations(self, project_id: uuid.UUID, diff_id: uuid.UUID) -> list[ExplanationRun]:
        rows = [r for r in self.explanations if r["project_id"] == project_id and r["diff_id"] == diff_id]
        return [explanation_run_from(r) for r in sorted(rows, key=lambda r: (r["requested_at"], r["id"]))]

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[DiffListing]:
        diffs = [diff_from(r) for r in self.rows.values() if r["project_id"] == project_id]
        if architecture_id is not None:
            diffs = [
                d
                for d in diffs
                if architecture_id in {d.base.ref.architecture_id, d.target.ref.architecture_id}
            ]
        diffs.sort(key=lambda d: (d.created_at, d.id), reverse=True)
        if after is not None:
            diffs = [d for d in diffs if (d.created_at, d.id) < after]
        return [
            DiffListing(
                d.id, d.base, d.target, len(d.semantic.changes), d.semantic.counts(),
                sum(1 for r in self.explanations if r["diff_id"] == d.id),
                d.requested_by_user_id, d.created_at,
            )
            for d in diffs[:limit]
        ]  # fmt: skip


class FakeWorkflowRepository:
    """Workflows and candidates kept as the database keeps them: written and read through documents."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, dict[str, Any]] = {}
        self.candidate_rows: dict[uuid.UUID, dict[str, Any]] = {}
        self.step_rows: list[WorkflowStep] = []

    async def add(self, workflow: ArchitectureWorkflow) -> ArchitectureWorkflow:
        self.rows[workflow.id] = workflow_document(workflow)
        return workflow

    async def get(
        self, project_id: uuid.UUID, workflow_id: uuid.UUID, *, for_update: bool = False
    ) -> ArchitectureWorkflow | None:
        row = self.rows.get(workflow_id)
        return workflow_from(row) if row is not None and row["project_id"] == project_id else None

    async def save(self, workflow: ArchitectureWorkflow) -> ArchitectureWorkflow:
        if workflow.id not in self.rows:
            raise LookupError(workflow.id)
        self.rows[workflow.id] = workflow_document(workflow)
        return workflow

    async def candidates(
        self, project_id: uuid.UUID, workflow_id: uuid.UUID
    ) -> tuple[WorkflowCandidate, ...]:
        found = [
            candidate_from(r)
            for r in self.candidate_rows.values()
            if r["project_id"] == project_id and r["workflow_id"] == workflow_id
        ]
        return tuple(sorted(found, key=lambda c: c.ordinal))

    async def save_candidates(self, project_id: uuid.UUID, candidates: tuple[WorkflowCandidate, ...]) -> None:
        for candidate in candidates:
            self.candidate_rows[candidate.id] = candidate_document(project_id, candidate)

    async def steps(self, project_id: uuid.UUID, workflow_id: uuid.UUID) -> tuple[WorkflowStep, ...]:
        return tuple(s for s in self.step_rows if s.workflow_id == workflow_id)

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: WorkflowStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[WorkflowListing]:
        flows = [workflow_from(r) for r in self.rows.values() if r["project_id"] == project_id]
        flows = [f for f in flows if status is None or f.status is status]
        flows.sort(key=lambda f: (f.requested_at, f.id), reverse=True)
        if after is not None:
            flows = [f for f in flows if (f.requested_at, f.id) < after]
        return [
            WorkflowListing(
                f.id, f.goal.objective, f.status, f.stage, f.iteration,
                sum(1 for r in self.candidate_rows.values() if r["workflow_id"] == f.id),
                f.failure.code if f.failure else None, f.requested_by_user_id, f.requested_at, f.completed_at,
            )
            for f in flows[:limit]
        ]  # fmt: skip


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
        self._pricing = FakePricingSnapshotRepository()
        self._cost = FakeCostAnalysisRepository()
        self._reliability = FakeReliabilityAnalysisRepository()
        self._security = FakeSecurityAnalysisRepository()
        self._observability = FakeObservabilityAnalysisRepository()
        self._simulations = FakeSimulationRepository()
        self._evolution = FakeEvolutionRepository()
        self._decisions = FakeDecisionRepository()
        self._migrations = FakeMigrationPlanRepository()
        self._discoveries = FakeDiscoveryRunRepository()
        self._drift = FakeDriftRepository()
        self._knowledge = FakeKnowledgeRepository()
        self._agent_runs = FakeAgentRunRepository()
        self._architecture_diffs = FakeArchitectureDiffRepository()
        self._architecture_workflows = FakeWorkflowRepository()
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

    @property
    def pricing(self) -> FakePricingSnapshotRepository:
        return self._pricing

    @property
    def cost(self) -> FakeCostAnalysisRepository:
        return self._cost

    @property
    def reliability(self) -> FakeReliabilityAnalysisRepository:
        return self._reliability

    @property
    def security(self) -> FakeSecurityAnalysisRepository:
        return self._security

    @property
    def observability(self) -> FakeObservabilityAnalysisRepository:
        return self._observability

    @property
    def simulations(self) -> FakeSimulationRepository:
        return self._simulations

    @property
    def evolution(self) -> FakeEvolutionRepository:
        return self._evolution

    @property
    def decisions(self) -> FakeDecisionRepository:
        return self._decisions

    @property
    def migrations(self) -> FakeMigrationPlanRepository:
        return self._migrations

    @property
    def discoveries(self) -> FakeDiscoveryRunRepository:
        return self._discoveries

    @property
    def drift(self) -> FakeDriftRepository:
        return self._drift

    @property
    def knowledge(self) -> FakeKnowledgeRepository:
        return self._knowledge

    @property
    def agent_runs(self) -> FakeAgentRunRepository:
        return self._agent_runs

    @property
    def architecture_diffs(self) -> FakeArchitectureDiffRepository:
        return self._architecture_diffs

    @property
    def architecture_workflows(self) -> FakeWorkflowRepository:
        return self._architecture_workflows

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
