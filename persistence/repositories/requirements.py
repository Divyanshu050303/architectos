import uuid
from decimal import Decimal

from sqlalchemy import func, literal, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.requirements.entities import (
    NewRequirement,
    Requirement,
    RequirementContent,
    RequirementVersion,
    Revision,
)
from core.domain.requirements.enums import (
    RequirementPriority,
    RequirementScope,
    RequirementSource,
    RequirementStatus,
    RequirementType,
)
from core.domain.requirements.queries import RequirementQuery
from core.domain.requirements.value_objects import parse_structured_data
from persistence.models import RequirementRecord, RequirementVersionRecord
from persistence.models.requirement import RequirementContent as ContentColumns

from ._search import escape_like


def _content(record: ContentColumns) -> RequirementContent:
    # Stored states are loaded as they are, never re-validated against today's rules.
    return RequirementContent(
        type=RequirementType(record.type),
        category=record.category,
        title=record.title,
        statement=record.statement,
        priority=RequirementPriority(record.priority),
        status=RequirementStatus(record.status),
        constraint=parse_structured_data(record.structured_data),
        scope=RequirementScope(record.scope),
    )


def _confidence(stored: Decimal | None) -> Decimal | None:
    """numeric(4,3) pads to three places; the domain value is the plain number (0.850 -> 0.85)."""
    return stored.normalize() + 0 if stored is not None else None


def _content_columns(content: RequirementContent) -> dict[str, object]:
    return {
        "type": content.type.value,
        "category": content.category,
        "title": content.title,
        "statement": content.statement,
        "priority": content.priority.value,
        "status": content.status.value,
        "structured_data": content.structured_data,
        "scope": content.scope.value,
    }


def to_requirement(record: RequirementRecord) -> Requirement:
    return Requirement(
        id=record.id,
        project_id=record.project_id,
        number=record.number,
        version=record.current_version,
        content=_content(record),
        source=RequirementSource(record.source),
        confidence=_confidence(record.confidence),
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        deleted_at=record.deleted_at,
    )


def to_version(record: RequirementVersionRecord) -> RequirementVersion:
    return RequirementVersion(
        requirement_id=record.requirement_id,
        version=record.version,
        content=_content(record),
        source=RequirementSource(record.source),
        confidence=_confidence(record.confidence),
        change_reason=record.change_reason,
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
    )


class SqlAlchemyRequirementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, requirement: NewRequirement) -> Requirement:
        # Numbers include deleted requirements, so a reference is never reused. Callers hold the
        # project row lock, which serializes allocation; the unique constraint is the backstop.
        number = await self._session.scalar(
            select(func.coalesce(func.max(RequirementRecord.number), 0) + 1).where(
                RequirementRecord.project_id == requirement.project_id
            )
        )
        columns = _content_columns(requirement.content)
        record = RequirementRecord(
            project_id=requirement.project_id,
            number=number,
            current_version=1,
            source=requirement.source.value,
            confidence=requirement.confidence,
            created_by_user_id=requirement.created_by_user_id,
            **columns,
        )
        self._session.add(record)
        await self._session.flush()
        self._session.add(
            RequirementVersionRecord(
                requirement_id=record.id,
                version=1,
                source=record.source,
                confidence=record.confidence,
                created_by_user_id=requirement.created_by_user_id,
                **columns,
            )
        )
        await self._session.flush()
        await self._session.refresh(record)
        return to_requirement(record)

    def _live(self, project_id: uuid.UUID, requirement_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return select(RequirementRecord).where(
            RequirementRecord.id == requirement_id,
            RequirementRecord.project_id == project_id,
            RequirementRecord.deleted_at.is_(None),
        )

    async def get(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, for_update: bool = False
    ) -> Requirement | None:
        statement = self._live(project_id, requirement_id).execution_options(populate_existing=True)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return to_requirement(record) if record else None

    async def save(self, revision: Revision) -> Requirement:
        requirement = revision.requirement
        columns = _content_columns(requirement.content)
        self._session.add(
            RequirementVersionRecord(
                requirement_id=requirement.id,
                version=requirement.version,
                source=requirement.source.value,
                confidence=requirement.confidence,
                change_reason=revision.change_reason,
                created_by_user_id=revision.author_user_id,
                **columns,
            )
        )
        await self._session.flush()
        # Guarded on the previous version: a lost update is impossible even without the row lock.
        record = await self._session.scalar(
            update(RequirementRecord)
            .where(
                RequirementRecord.id == requirement.id,
                RequirementRecord.current_version == requirement.version - 1,
            )
            .values(current_version=requirement.version, updated_at=func.now(), **columns)
            .returning(RequirementRecord)
            .execution_options(populate_existing=True)
        )
        if record is None:
            msg = f"requirement {requirement.id} moved past version {requirement.version - 1} under its lock"
            raise LookupError(msg)
        return to_requirement(record)

    async def save_deleted(self, requirement: Requirement) -> None:
        await self._session.execute(
            update(RequirementRecord)
            .where(RequirementRecord.id == requirement.id)
            .values(deleted_at=requirement.deleted_at, updated_at=func.now())
        )

    async def list_for_project(self, project_id: uuid.UUID, query: RequirementQuery) -> list[Requirement]:
        statement = select(RequirementRecord).where(
            RequirementRecord.project_id == project_id, RequirementRecord.deleted_at.is_(None)
        )
        if query.type is not None:
            statement = statement.where(RequirementRecord.type == query.type.value)
        if query.category is not None:
            statement = statement.where(RequirementRecord.category == query.category)
        if query.status is not None:
            statement = statement.where(RequirementRecord.status == query.status.value)
        if query.priority is not None:
            statement = statement.where(RequirementRecord.priority == query.priority.value)
        if query.search:
            pattern = f"%{escape_like(query.search.strip().lower())}%"
            statement = statement.where(
                or_(
                    func.lower(RequirementRecord.title).like(pattern, escape="\\"),
                    func.lower(RequirementRecord.statement).like(pattern, escape="\\"),
                )
            )
        if query.after is not None:
            statement = statement.where(
                tuple_(RequirementRecord.created_at, RequirementRecord.id)
                < tuple_(literal(query.after.created_at), literal(query.after.id))
            )
        records = await self._session.scalars(
            statement.order_by(RequirementRecord.created_at.desc(), RequirementRecord.id.desc()).limit(
                query.limit
            )
        )
        return [to_requirement(r) for r in records]

    async def list_by_status(
        self, project_id: uuid.UUID, statuses: frozenset[RequirementStatus], *, limit: int
    ) -> list[Requirement]:
        records = await self._session.scalars(
            select(RequirementRecord)
            .where(
                RequirementRecord.project_id == project_id,
                RequirementRecord.deleted_at.is_(None),
                RequirementRecord.status.in_(sorted(status.value for status in statuses)),
            )
            .order_by(RequirementRecord.number)
            .limit(limit)
        )
        return [to_requirement(r) for r in records]

    async def list_by_ids(self, project_id: uuid.UUID, requirement_ids: list[uuid.UUID]) -> list[Requirement]:
        if not requirement_ids:
            return []
        records = await self._session.scalars(
            select(RequirementRecord).where(
                RequirementRecord.project_id == project_id,
                RequirementRecord.deleted_at.is_(None),
                RequirementRecord.id.in_(requirement_ids),
            )
        )
        return [to_requirement(r) for r in records]

    def _versions_of_live(self, project_id: uuid.UUID, requirement_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return (
            select(RequirementVersionRecord)
            .join(RequirementRecord, RequirementRecord.id == RequirementVersionRecord.requirement_id)
            .where(
                RequirementRecord.id == requirement_id,
                RequirementRecord.project_id == project_id,
                RequirementRecord.deleted_at.is_(None),
            )
        )

    async def list_versions(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, *, after: int | None, limit: int
    ) -> list[RequirementVersion]:
        statement = self._versions_of_live(project_id, requirement_id)
        if after is not None:
            statement = statement.where(RequirementVersionRecord.version > after)
        records = await self._session.scalars(
            statement.order_by(RequirementVersionRecord.version).limit(limit)
        )
        return [to_version(r) for r in records]

    async def get_version(
        self, project_id: uuid.UUID, requirement_id: uuid.UUID, version: int
    ) -> RequirementVersion | None:
        record = await self._session.scalar(
            self._versions_of_live(project_id, requirement_id).where(
                RequirementVersionRecord.version == version
            )
        )
        return to_version(record) if record else None
