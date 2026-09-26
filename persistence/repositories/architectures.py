"""Architectures, their append-only revisions and their layout.

A revision keeps the document exactly as stored (``snapshot``, in the schema version it was
written in) and the same content read through the IR's reader (``ir``: upgraded to the current
schema for the engines, never written back). History is shown as it was stored.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.architecture_ir.serialization import from_dict, to_dict
from core.domain.architecture.entities import (
    Architecture,
    ArchitectureLayout,
    ArchitectureQuery,
    ArchitectureStatus,
    NewArchitecture,
    Position,
    RevisionSummary,
)
from core.domain.architecture.errors import ArchitectureNameTaken, ArchitectureVersionConflict
from core.domain.architecture.versions import ArchitectureRevision, NewRevision, RevisionSource
from persistence.models import ArchitectureLayoutRecord, ArchitectureRecord, ArchitectureRevisionRecord

from ._errors import violated_constraint
from ._search import escape_like

NAME_UNIQUE = "uq_architectures_project_id_name_live"

_SUMMARY = (
    ArchitectureRevisionRecord.number,
    ArchitectureRevisionRecord.parent_number,
    ArchitectureRevisionRecord.restored_from_number,
    ArchitectureRevisionRecord.source,
    ArchitectureRevisionRecord.summary,
    ArchitectureRevisionRecord.reason,
    ArchitectureRevisionRecord.content_hash,
    ArchitectureRevisionRecord.ir_schema_version,
    ArchitectureRevisionRecord.requirement_set_id,
    ArchitectureRevisionRecord.created_by_user_id,
    ArchitectureRevisionRecord.created_at,
)


def _to_architecture(record: ArchitectureRecord) -> Architecture:
    return Architecture(
        id=record.id,
        project_id=record.project_id,
        name=record.name,
        description=record.description,
        status=ArchitectureStatus(record.status),
        current_revision=record.current_revision,
        created_by_user_id=record.created_by_user_id,
        updated_by_user_id=record.updated_by_user_id,
        archived_at=record.archived_at,
        deleted_at=record.deleted_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_revision(record: ArchitectureRevisionRecord) -> ArchitectureRevision:
    return ArchitectureRevision(
        id=record.id,
        architecture_id=record.architecture_id,
        number=record.number,
        parent_number=record.parent_number,
        ir=from_dict(record.ir),
        content_hash=record.content_hash,
        source=RevisionSource(record.source),
        summary=record.summary,
        reason=record.reason,
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        requirement_set_id=record.requirement_set_id,
        restored_from=record.restored_from_number,
        snapshot=record.ir,
        stored_schema_version=record.ir_schema_version,
    )


def _to_summary(row: Any) -> RevisionSummary:
    return RevisionSummary(
        number=row.number,
        parent_number=row.parent_number,
        restored_from=row.restored_from_number,
        source=RevisionSource(row.source),
        summary=row.summary,
        reason=row.reason,
        content_hash=row.content_hash,
        ir_schema_version=row.ir_schema_version,
        requirement_set_id=row.requirement_set_id,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def _to_layout(record: ArchitectureLayoutRecord | None) -> ArchitectureLayout:
    if record is None:
        return ArchitectureLayout()
    positions = {node_id: Position(p["x"], p["y"]) for node_id, p in record.positions.items()}
    return ArchitectureLayout(positions, record.updated_by_user_id, record.updated_at)


class SqlAlchemyArchitectureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _revision_record(project_id: uuid.UUID, revision: NewRevision) -> ArchitectureRevisionRecord:
        return ArchitectureRevisionRecord(
            architecture_id=revision.architecture_id,
            project_id=project_id,
            number=revision.number,
            parent_number=revision.parent_number,
            restored_from_number=revision.restored_from,
            ir=to_dict(revision.ir),
            ir_schema_version=revision.ir_schema_version,
            content_hash=revision.content_hash,
            source=revision.source.value,
            summary=revision.summary,
            reason=revision.reason,
            requirement_set_id=revision.requirement_set_id,
            created_by_user_id=revision.created_by_user_id,
        )

    async def _flush_or_name_taken(self, *new: object) -> None:
        """Flushes (adding ``new``) in a savepoint: a duplicate live name leaves the surrounding
        transaction usable."""
        try:
            async with self._session.begin_nested():
                self._session.add_all(new)
                await self._session.flush()
        except IntegrityError as error:
            if violated_constraint(error) != NAME_UNIQUE:
                raise
            raise ArchitectureNameTaken from None

    async def add(
        self, architecture: NewArchitecture, first: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        record = ArchitectureRecord(
            id=architecture.id,
            project_id=architecture.project_id,
            name=architecture.name,
            description=architecture.description,
            status=ArchitectureStatus.ACTIVE.value,
            current_revision=first.number,
            created_by_user_id=architecture.created_by_user_id,
            updated_by_user_id=architecture.created_by_user_id,
        )
        await self._flush_or_name_taken(record)
        revision = self._revision_record(architecture.project_id, first)
        self._session.add(revision)
        await self._session.flush()
        await self._session.refresh(record)
        await self._session.refresh(revision)
        return _to_architecture(record), _to_revision(revision)

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
    ) -> Architecture | None:
        query = select(ArchitectureRecord).where(
            ArchitectureRecord.id == architecture_id,
            ArchitectureRecord.project_id == project_id,
            ArchitectureRecord.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        record = await self._session.scalar(query.execution_options(populate_existing=True))
        return _to_architecture(record) if record else None

    async def list_for_project(self, project_id: uuid.UUID, query: ArchitectureQuery) -> list[Architecture]:
        statement = select(ArchitectureRecord).where(
            ArchitectureRecord.project_id == project_id, ArchitectureRecord.deleted_at.is_(None)
        )
        if query.status is not None:
            statement = statement.where(ArchitectureRecord.status == query.status.value)
        if query.search:
            pattern = f"%{escape_like(query.search.strip().lower())}%"
            statement = statement.where(func.lower(ArchitectureRecord.name).like(pattern, escape="\\"))
        if query.after is not None:
            created_at, architecture_id = query.after
            statement = statement.where(
                or_(
                    ArchitectureRecord.created_at < created_at,
                    and_(
                        ArchitectureRecord.created_at == created_at, ArchitectureRecord.id < architecture_id
                    ),
                )
            )
        statement = statement.order_by(ArchitectureRecord.created_at.desc(), ArchitectureRecord.id.desc())
        records = await self._session.scalars(statement.limit(query.limit))
        return [_to_architecture(r) for r in records]

    async def save(self, architecture: Architecture) -> Architecture:
        record = await self._session.get(ArchitectureRecord, architecture.id, populate_existing=True)
        if record is None or record.project_id != architecture.project_id:
            raise LookupError("architecture vanished")  # the caller holds its row lock
        record.name = architecture.name
        record.description = architecture.description
        record.status = architecture.status.value
        record.archived_at = architecture.archived_at
        record.deleted_at = architecture.deleted_at
        record.updated_by_user_id = architecture.updated_by_user_id
        record.updated_at = func.now()
        await self._flush_or_name_taken()
        await self._session.refresh(record)
        return _to_architecture(record)

    async def add_revision(
        self, architecture: Architecture, revision: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        record = self._revision_record(architecture.project_id, revision)
        self._session.add(record)
        await self._session.flush()
        moved = await self._session.scalar(
            update(ArchitectureRecord)
            .where(
                ArchitectureRecord.id == architecture.id,
                ArchitectureRecord.current_revision == revision.parent_number,
            )
            .values(
                current_revision=revision.number,
                updated_at=func.now(),
                updated_by_user_id=revision.created_by_user_id,
            )
            .returning(ArchitectureRecord)
            .execution_options(populate_existing=True)
        )
        if moved is None:  # the caller's row lock makes this impossible; never overwrite silently
            raise ArchitectureVersionConflict(details={"latest_version": None})
        await self._session.refresh(record)
        return _to_architecture(moved), _to_revision(record)

    async def get_revision(self, architecture_id: uuid.UUID, number: int) -> ArchitectureRevision | None:
        record = await self._session.scalar(
            select(ArchitectureRevisionRecord).where(
                ArchitectureRevisionRecord.architecture_id == architecture_id,
                ArchitectureRevisionRecord.number == number,
            )
        )
        return _to_revision(record) if record else None

    async def list_revisions(
        self, architecture_id: uuid.UUID, *, before: int | None, limit: int
    ) -> list[RevisionSummary]:
        query = select(*_SUMMARY).where(ArchitectureRevisionRecord.architecture_id == architecture_id)
        if before is not None:
            query = query.where(ArchitectureRevisionRecord.number < before)
        rows = await self._session.execute(
            query.order_by(ArchitectureRevisionRecord.number.desc()).limit(limit)
        )
        return [_to_summary(row) for row in rows]

    async def get_layout(self, architecture_id: uuid.UUID) -> ArchitectureLayout:
        record = await self._session.get(ArchitectureLayoutRecord, architecture_id, populate_existing=True)
        return _to_layout(record)

    async def save_layout(
        self, architecture: Architecture, positions: Mapping[str, Position], user_id: uuid.UUID
    ) -> ArchitectureLayout:
        stored = {node_id: {"x": p.x, "y": p.y} for node_id, p in sorted(positions.items())}
        statement = (
            insert(ArchitectureLayoutRecord)
            .values(
                architecture_id=architecture.id,
                project_id=architecture.project_id,
                positions=stored,
                updated_by_user_id=user_id,
            )
            .on_conflict_do_update(
                index_elements=[ArchitectureLayoutRecord.architecture_id],
                set_={"positions": stored, "updated_by_user_id": user_id, "updated_at": func.now()},
            )
            .returning(ArchitectureLayoutRecord)
            .execution_options(populate_existing=True)
        )
        return _to_layout(await self._session.scalar(statement))
