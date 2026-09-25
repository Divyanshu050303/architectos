"""Reading the audit trail. Writing happens inside each domain service's own transaction."""

import uuid
from datetime import datetime

from core.domain import pagination
from core.domain.organizations.entities import Membership
from core.domain.organizations.permissions import Permission
from core.domain.unit_of_work import UnitOfWork

from .entities import AuditCursor, AuditPage
from .errors import InvalidCursor

MAX_PAGE_SIZE = pagination.MAX_PAGE_SIZE


def encode_cursor(cursor: AuditCursor) -> str:
    return pagination.encode_cursor(["audit", cursor.created_at.isoformat(), str(cursor.id)])


def decode_cursor(value: str) -> AuditCursor:
    kind, created_at, entry_id = pagination.decode_cursor(value, length=3)
    try:
        parsed = datetime.fromisoformat(created_at)
        if kind != "audit" or parsed.tzinfo is None:
            raise ValueError("wrong cursor")
        return AuditCursor(created_at=parsed, id=uuid.UUID(entry_id))
    except ValueError:
        raise InvalidCursor from None


class AuditService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def organization_log(self, *, membership: Membership, cursor: str | None, limit: int) -> AuditPage:
        membership.require(Permission.AUDIT_READ)
        after = decode_cursor(cursor) if cursor else None
        size = max(1, min(limit, MAX_PAGE_SIZE))
        async with self._uow as uow:
            rows = await uow.audit.list_for_organization(
                membership.organization_id, after=after, limit=size + 1
            )
        entries, more = rows[:size], len(rows) > size
        last = entries[-1] if entries else None
        next_cursor = AuditCursor(created_at=last.created_at, id=last.id) if more and last else None
        return AuditPage(entries=entries, next_cursor=next_cursor)
