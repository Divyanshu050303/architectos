import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, func, literal, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.projects.entities import NewProject, Project, ProjectAccess
from core.domain.projects.enums import ProjectStatus
from core.domain.projects.errors import ProjectSlugTaken
from core.domain.projects.queries import ProjectQuery, ProjectSort
from core.domain.projects.repository import ProjectLock
from core.domain.projects.value_objects import ProjectSettings
from persistence.models import OrganizationMemberRecord, OrganizationRecord, ProjectRecord

from ._errors import violated_constraint
from ._search import escape_like
from .organizations import to_membership

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

    async def get_for_member(
        self, project_id: uuid.UUID, *, user_id: uuid.UUID, lock: ProjectLock | None = None
    ) -> ProjectAccess | None:
        # Project, organization and membership in one statement: a project of another tenant, a
        # deleted project or a deleted organization simply produce no row.
        statement = (
            select(ProjectRecord, OrganizationMemberRecord)
            .join(OrganizationRecord, OrganizationRecord.id == ProjectRecord.organization_id)
            .join(
                OrganizationMemberRecord,
                (OrganizationMemberRecord.organization_id == ProjectRecord.organization_id)
                & (OrganizationMemberRecord.user_id == user_id),
            )
            .where(
                ProjectRecord.id == project_id,
                ProjectRecord.deleted_at.is_(None),
                OrganizationRecord.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if lock is not None:
            # FOR SHARE / FOR UPDATE on the project row only (not the organization or membership).
            statement = statement.with_for_update(of=ProjectRecord, read=lock is ProjectLock.SHARE)
        row = (await self._session.execute(statement)).first()
        if row is None:
            return None
        project, member = row
        return ProjectAccess(project=to_project(project), membership=to_membership(member))

    async def list_for_organization(self, organization_id: uuid.UUID, query: ProjectQuery) -> list[Project]:
        statement = select(ProjectRecord).where(
            ProjectRecord.organization_id == organization_id, ProjectRecord.deleted_at.is_(None)
        )
        if query.status is not None:
            statement = statement.where(ProjectRecord.status == query.status.value)
        if query.search:
            pattern = f"%{escape_like(query.search.strip().lower())}%"
            statement = statement.where(
                or_(
                    func.lower(ProjectRecord.name).like(pattern, escape="\\"),
                    ProjectRecord.slug.like(pattern, escape="\\"),
                )
            )
        key, descending = _SORT_KEYS[query.sort]
        if query.after is not None:
            after_value: object = (
                query.after.value
                if query.sort is ProjectSort.NAME
                else datetime.fromisoformat(query.after.value)
            )
            boundary = tuple_(key, ProjectRecord.id)
            position = tuple_(literal(after_value), literal(query.after.id))
            statement = statement.where(boundary < position if descending else boundary > position)
        order = (key.desc(), ProjectRecord.id.desc()) if descending else (key.asc(), ProjectRecord.id.asc())
        records = await self._session.scalars(statement.order_by(*order).limit(query.limit))
        return [to_project(r) for r in records]


# Sort key and direction per whitelisted ordering. Keyset pagination compares (key, id).
_SORT_KEYS: dict[ProjectSort, tuple[ColumnElement[object], bool]] = {
    ProjectSort.CREATED_AT: (ProjectRecord.created_at, True),  # type: ignore[dict-item]
    ProjectSort.UPDATED_AT: (ProjectRecord.updated_at, True),  # type: ignore[dict-item]
    ProjectSort.NAME: (func.lower(ProjectRecord.name), False),
}
