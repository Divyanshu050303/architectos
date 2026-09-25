import logging
import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.audit.entities import AuditCursor, AuditEntry, AuditEvent
from core.domain.client import ClientInfo
from persistence.models import AuditLogRecord

MAX_USER_AGENT_LENGTH = 512
events_log = logging.getLogger("architectos.events")


def to_entry(record: AuditLogRecord) -> AuditEntry:
    return AuditEntry(
        id=record.id,
        organization_id=record.organization_id,
        actor_user_id=record.actor_user_id,
        action=record.action,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        metadata=dict(record.event_metadata),
        ip_address=str(record.ip_address) if record.ip_address is not None else None,
        user_agent=record.user_agent,
        created_at=record.created_at,
    )


class SqlAlchemyAuditRepository:
    """Append-only (the table's trigger rejects UPDATE/DELETE). The request's origin is attached
    here, from the ClientInfo the unit of work was built with."""

    def __init__(self, session: AsyncSession, client: ClientInfo) -> None:
        self._session = session
        self._client = client
        self._uncommitted: list[AuditEvent] = []

    def publish_committed(self) -> None:
        """Called by the unit of work after a commit: each recorded event becomes one structured
        log line with the request id. Identifiers only: metadata, which can
        hold names, never reaches the logs, and events of rolled-back transactions never do."""
        for event in self._uncommitted:
            project_id = event.metadata.get("project_id") or (
                str(event.resource_id) if event.resource_type == "project" else None
            )
            events_log.info(
                event.action.value,
                extra={
                    "event": event.action.value,
                    "request_id": self._client.request_id,
                    "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
                    "organization_id": str(event.organization_id) if event.organization_id else None,
                    "project_id": project_id,
                    "resource_type": event.resource_type,
                    "resource_id": str(event.resource_id) if event.resource_id is not None else None,
                },
            )
        self._uncommitted.clear()

    def discard_uncommitted(self) -> None:
        self._uncommitted.clear()

    async def record(self, event: AuditEvent) -> None:
        user_agent = self._client.user_agent
        self._session.add(
            AuditLogRecord(
                organization_id=event.organization_id,
                actor_user_id=event.actor_user_id,
                action=event.action.value,
                resource_type=event.resource_type,
                resource_id=str(event.resource_id) if event.resource_id is not None else None,
                event_metadata=event.metadata,
                ip_address=self._client.ip_address,
                user_agent=user_agent[:MAX_USER_AGENT_LENGTH] if user_agent else None,
            )
        )
        await self._session.flush()
        self._uncommitted.append(event)

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, after: AuditCursor | None, limit: int
    ) -> list[AuditEntry]:
        # Keyset pagination on (created_at, id), served by the partial index
        # ix_audit_logs_organization_id_created_at (organization_id, created_at, id).
        query = select(AuditLogRecord).where(AuditLogRecord.organization_id == organization_id)
        if after is not None:
            query = query.where(
                or_(
                    AuditLogRecord.created_at < after.created_at,
                    and_(AuditLogRecord.created_at == after.created_at, AuditLogRecord.id < after.id),
                )
            )
        records = await self._session.scalars(
            query.order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc()).limit(limit)
        )
        return [to_entry(r) for r in records]
