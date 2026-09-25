import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.requirements.requirement_sets import NewRequirementSet, PinnedVersion, RequirementSet
from persistence.models import RequirementRecord, RequirementSetItemRecord, RequirementSetRecord

# Everything but the (possibly large) planning input, which is only loaded on request.
_SUMMARY = (
    RequirementSetRecord.id,
    RequirementSetRecord.project_id,
    RequirementSetRecord.number,
    RequirementSetRecord.name,
    RequirementSetRecord.description,
    RequirementSetRecord.schema_version,
    RequirementSetRecord.content_hash,
    RequirementSetRecord.requirement_count,
    RequirementSetRecord.created_by_user_id,
    RequirementSetRecord.created_at,
)


def _to_set(row: Any, items: tuple[PinnedVersion, ...] = ()) -> RequirementSet:
    return RequirementSet(
        id=row.id,
        project_id=row.project_id,
        number=row.number,
        name=row.name,
        description=row.description,
        schema_version=row.schema_version,
        content_hash=row.content_hash,
        requirement_count=row.requirement_count,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        items=items,
    )


class SqlAlchemyRequirementSetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, requirement_set: NewRequirementSet) -> RequirementSet:
        number = await self._session.scalar(
            select(func.coalesce(func.max(RequirementSetRecord.number), 0) + 1).where(
                RequirementSetRecord.project_id == requirement_set.project_id
            )
        )
        record = RequirementSetRecord(
            project_id=requirement_set.project_id,
            number=number,
            name=requirement_set.name,
            description=requirement_set.description,
            schema_version=requirement_set.schema_version,
            planning_input=requirement_set.planning_input,
            content_hash=requirement_set.content_hash,
            requirement_count=len(requirement_set.items),
            created_by_user_id=requirement_set.created_by_user_id,
        )
        self._session.add(record)
        await self._session.flush()
        self._session.add_all(
            RequirementSetItemRecord(
                requirement_set_id=record.id,
                requirement_id=item.requirement_id,
                project_id=requirement_set.project_id,
                version=item.version,
            )
            for item in requirement_set.items
        )
        await self._session.flush()
        row = (
            await self._session.execute(select(*_SUMMARY).where(RequirementSetRecord.id == record.id))
        ).one()
        return _to_set(row, requirement_set.items)

    async def get(self, project_id: uuid.UUID, set_id: uuid.UUID) -> RequirementSet | None:
        row = (
            await self._session.execute(
                select(*_SUMMARY).where(
                    RequirementSetRecord.id == set_id, RequirementSetRecord.project_id == project_id
                )
            )
        ).one_or_none()
        if row is None:
            return None
        items = await self._session.execute(
            select(
                RequirementSetItemRecord.requirement_id,
                RequirementRecord.number,
                RequirementSetItemRecord.version,
            )
            .join(RequirementRecord, RequirementRecord.id == RequirementSetItemRecord.requirement_id)
            .where(RequirementSetItemRecord.requirement_set_id == set_id)
            .order_by(RequirementRecord.number)
        )
        return _to_set(row, tuple(PinnedVersion(r.requirement_id, r.number, r.version) for r in items))

    async def list_for_project(
        self, project_id: uuid.UUID, *, before_number: int | None, limit: int
    ) -> list[RequirementSet]:
        statement = select(*_SUMMARY).where(RequirementSetRecord.project_id == project_id)
        if before_number is not None:
            statement = statement.where(RequirementSetRecord.number < before_number)
        rows = await self._session.execute(
            statement.order_by(RequirementSetRecord.number.desc()).limit(limit)
        )
        return [_to_set(row) for row in rows]

    async def get_planning_input(
        self, project_id: uuid.UUID, set_id: uuid.UUID
    ) -> tuple[RequirementSet, dict[str, Any]] | None:
        row = (
            await self._session.execute(
                select(*_SUMMARY, RequirementSetRecord.planning_input).where(
                    RequirementSetRecord.id == set_id, RequirementSetRecord.project_id == project_id
                )
            )
        ).one_or_none()
        if row is None:
            return None
        document: dict[str, Any] = row.planning_input
        return _to_set(row), document
