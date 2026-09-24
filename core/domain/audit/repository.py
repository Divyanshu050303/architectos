import uuid
from typing import Protocol

from .entities import AuditCursor, AuditEntry, AuditEvent


class AuditRepository(Protocol):
    async def record(self, event: AuditEvent) -> None:
        """Appends one entry inside the current transaction (committed or rolled back with it)."""
        ...

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, after: AuditCursor | None, limit: int
    ) -> list[AuditEntry]:
        """Newest first, strictly older than ``after``."""
        ...
