import uuid
from datetime import datetime
from typing import Protocol

from .entities import MigrationPlanVersion
from .values import PlanStatus


class MigrationPlanRepository(Protocol):
    """Migration plan versions belong to one project; every read is scoped by it. A version's content
    is written once; only its review status and history are replaced afterwards."""

    async def add(self, version: MigrationPlanVersion) -> MigrationPlanVersion: ...

    async def update_review(self, version: MigrationPlanVersion) -> MigrationPlanVersion:
        """The version's new status and review history (nothing else changes)."""
        ...

    async def get(
        self,
        project_id: uuid.UUID,
        plan_id: uuid.UUID,
        version: int | None = None,
        *,
        for_update: bool = False,
    ) -> MigrationPlanVersion | None:
        """The version (``None``: the plan's latest); ``for_update`` locks its row."""
        ...

    async def history(self, project_id: uuid.UUID, plan_id: uuid.UUID) -> list[MigrationPlanVersion]:
        """Every version of the plan, by version number."""
        ...

    async def list_latest(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: PlanStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,  # the last (created_at, id) of the previous page
        limit: int = 50,
    ) -> list[MigrationPlanVersion]:
        """The latest version of each plan, newest first."""
        ...
