import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AuditAction(StrEnum):
    USER_REGISTERED = "user.registered"
    USER_EMAIL_VERIFIED = "user.email_verified"
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    USER_PASSWORD_RESET = "user.password_reset"  # noqa: S105 — an event name
    USER_PASSWORD_CHANGED = "user.password_changed"  # noqa: S105 — an event name
    USER_PROFILE_UPDATED = "user.profile_updated"
    USER_DELETED = "user.deleted"
    SESSION_REVOKED = "session.revoked"
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_DELETED = "organization.deleted"
    MEMBER_INVITED = "member.invited"
    MEMBER_INVITATION_REVOKED = "member.invitation_revoked"
    MEMBER_INVITATION_ACCEPTED = "member.invitation_accepted"
    MEMBER_ROLE_CHANGED = "member.role_changed"
    MEMBER_REMOVED = "member.removed"
    PRICING_SNAPSHOT_CREATED = "pricing_snapshot.created"
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    PROJECT_DELETED = "project.deleted"
    PROJECT_POLICY_UPDATED = "project.policy_updated"
    REQUIREMENT_CREATED = "requirement.created"
    REQUIREMENT_UPDATED = "requirement.updated"  # content (anything but status) changed
    REQUIREMENT_STATUS_CHANGED = "requirement.status_changed"
    REQUIREMENT_VERSION_CREATED = "requirement.version_created"  # every revision
    REQUIREMENT_DELETED = "requirement.deleted"
    REQUIREMENT_SET_CREATED = "requirement_set.created"
    REQUIREMENT_ANALYSIS_CREATED = "requirement_analysis.created"
    REQUIREMENT_PROMOTED = "requirement.promoted"  # created from an analysis's candidate
    ARCHITECTURE_CREATED = "architecture.created"
    ARCHITECTURE_UPDATED = "architecture.updated"  # name or description (no revision)
    ARCHITECTURE_REVISED = "architecture.revised"  # a new content revision after the first
    ARCHITECTURE_REVISION_RESTORED = "architecture.revision_restored"  # a new revision restoring an older one
    ARCHITECTURE_ARCHIVED = "architecture.archived"
    ARCHITECTURE_RESTORED = "architecture.restored"  # back from the archive
    ARCHITECTURE_DELETED = "architecture.deleted"
    ARCHITECTURE_VALIDATED = "architecture.validated"  # a validation run was stored
    ARCHITECTURE_CAPACITY_ANALYZED = "architecture.capacity_analyzed"  # a capacity analysis was stored
    ARCHITECTURE_COST_ANALYZED = "architecture.cost_analyzed"  # a cost analysis was stored
    ARCHITECTURE_RELIABILITY_ANALYZED = (
        "architecture.reliability_analyzed"  # a reliability analysis was stored
    )
    ARCHITECTURE_SECURITY_ANALYZED = "architecture.security_analyzed"  # a security analysis was stored
    ARCHITECTURE_OBSERVABILITY_ANALYZED = (
        "architecture.observability_analyzed"  # an observability analysis was stored
    )
    ARCHITECTURE_SIMULATED = "architecture.simulated"  # a simulation was stored
    ARCHITECTURE_EVOLUTION_ANALYZED = "architecture.evolution_analyzed"  # an evolution analysis was stored
    DECISION_PROPOSED = "decision.proposed"  # a decision record was drafted
    DECISION_ACCEPTED = "decision.accepted"  # a person accepted one of its options
    DECISION_REJECTED = "decision.rejected"  # a person rejected its options
    DECISION_SUPERSEDED = "decision.superseded"  # replaced by a later decision
    DECISION_REVISION_LINKED = "decision.revision_linked"  # a person linked the implementing revision
    MIGRATION_PLAN_CREATED = "migration_plan.created"  # version 1 of a migration plan was generated
    MIGRATION_PLAN_REGENERATED = "migration_plan.regenerated"  # a new version superseded the latest
    MIGRATION_PLAN_SUBMITTED = "migration_plan.submitted"  # a version was submitted for review
    MIGRATION_PLAN_APPROVED = "migration_plan.approved"  # a person approved an exact version
    MIGRATION_PLAN_REJECTED = "migration_plan.rejected"  # a person rejected an exact version
    MIGRATION_PLAN_ARCHIVED = "migration_plan.archived"
    DISCOVERY_RUN_CREATED = "discovery_run.created"  # artifacts were read; a result (or failure) stored
    DISCOVERY_RUN_REVIEWED = "discovery_run.reviewed"  # a person decided about one candidate
    DISCOVERY_RUN_ACCEPTED = "discovery_run.accepted"  # a person accepted the proposal as a revision
    DISCOVERY_RUN_DELETED = "discovery_run.deleted"
    DRIFT_ANALYSIS_CREATED = "drift_analysis.created"  # a baseline revision compared with a discovery run
    DRIFT_ITEM_REVIEWED = "drift_item.reviewed"  # a person acted on a drift item (never the architecture)
    DRIFT_IDENTITY_CONFIRMED = "drift_identity.confirmed"  # a person confirmed (or retracted) an identity
    KNOWLEDGE_SOURCE_REGISTERED = "knowledge_source.registered"  # a document or record made searchable
    KNOWLEDGE_SOURCE_INGESTED = "knowledge_source.ingested"  # read again: indexed, unchanged or failed
    KNOWLEDGE_SOURCE_ARCHIVED = "knowledge_source.archived"  # no longer searched; its versions are kept
    AGENT_RUN_CREATED = "agent_run.created"  # the architecture agent ran: waiting, a candidate, or failed
    AGENT_RUN_ANSWERED = "agent_run.answered"  # a person answered its questions; the run went on
    AGENT_RUN_CANCELLED = "agent_run.cancelled"
    AGENT_RUN_REJECTED = "agent_run.rejected"  # a person rejected the candidate
    AGENT_RUN_ACCEPTED = "agent_run.accepted"  # a person accepted the candidate as a revision
    ARCHITECTURE_DIFF_CREATED = "architecture_diff.created"  # two exact states compared and stored
    ARCHITECTURE_DIFF_EXPLAINED = "architecture_diff.explained"  # an explanation run appended to a diff


# Defense in depth: metadata keys that look like secrets are refused outright.
_SECRET_KEY = re.compile(r"password|token|secret|hash|cookie|authorization", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """What a service records. Request origin (IP, user agent) is attached by the unit of work."""

    action: AuditAction
    actor_user_id: uuid.UUID | None
    organization_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        leaked = [key for key in self.metadata if _SECRET_KEY.search(key)]
        if leaked:
            msg = f"audit metadata must not contain secrets: {leaked}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: uuid.UUID
    organization_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    metadata: dict[str, Any]
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditCursor:
    """Position in a newest-first listing: the last entry of the previous page."""

    created_at: datetime
    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AuditPage:
    entries: list[AuditEntry]
    next_cursor: AuditCursor | None
