import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.projects.entities import NewProject, Project
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.errors import ProjectSlugTaken
from core.domain.projects.value_objects import ProjectSettings
from persistence.models import ProjectRecord

from ._errors import violated_constraint

SLUG_UNIQUE_INDEX = "uq_projects_organization_id_slug_live"


def to_project(record: ProjectRecord) -> Project:
    return Project(
        id=record.id,
        organization_id=record.organization_id,
        name=record.name,
        slug=record.slug,
        description=record.description,
        status=ProjectStatus(record.status),
        settings=ProjectSettings.from_dict(record.settings),
        created_by_user_id=record.created_by_user_id,
        archived_at=record.archived_at,
        deleted_at=record.deleted_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class SqlAlchemyProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, project: NewProject) -> Project:
        record = ProjectRecord(
            organization_id=project.organization_id,
            name=project.name,
            slug=project.slug,
            description=project.description,
            settings=project.settings.to_dict(),
            created_by_user_id=project.created_by_user_id,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(record)
                await self._session.flush()
        except IntegrityError as error:
            if violated_constraint(error) == SLUG_UNIQUE_INDEX:
                raise ProjectSlugTaken from None
            raise
        await self._session.refresh(record)
        return to_project(record)

    async def get_live(self, project_id: uuid.UUID) -> Project | None:
        record = await self._session.scalar(
            select(ProjectRecord)
            .where(ProjectRecord.id == project_id, ProjectRecord.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
        return to_project(record) if record else None

    async def get_live_for_update(self, project_id: uuid.UUID) -> Project | None:
        record = await self._session.scalar(
            select(ProjectRecord)
            .where(ProjectRecord.id == project_id, ProjectRecord.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return to_project(record) if record else None

    async def save(self, project: Project) -> Project:
        record = await self._session.scalar(
            update(ProjectRecord)
            .where(ProjectRecord.id == project.id)
            .values(
                name=project.name,
                description=project.description,
                settings=project.settings.to_dict(),
                status=project.status.value,
                archived_at=project.archived_at,
                deleted_at=project.deleted_at,
                updated_at=func.now(),
            )
            .returning(ProjectRecord)
        )
        if record is None:
            msg = f"project {project.id} vanished inside its own transaction"
            raise LookupError(msg)
        return to_project(record)
