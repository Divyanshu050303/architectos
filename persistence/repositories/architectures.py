"""Architectures, their append-only revisions and their layout.

A revision's IR is stored in its canonical JSON form and read back through the IR's own reader,
so a revision written in an older IR schema is upgraded when read (never rewritten in place).
"""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.architecture_ir.serialization import from_dict, to_dict
from core.domain.architecture.entities import (
    Architecture,
    ArchitectureLayout,
    NewArchitecture,
    Position,
    RevisionSummary,
)
from core.domain.architecture.errors import ArchitectureAlreadyExists, ArchitectureVersionConflict
from core.domain.architecture.versions import ArchitectureRevision, NewRevision, RevisionSource
from persistence.models import ArchitectureLayoutRecord, ArchitectureRecord, ArchitectureRevisionRecord

from ._errors import violated_constraint

ONE_PER_PROJECT = "uq_architectures_project_id"

_SUMMARY = (
    ArchitectureRevisionRecord.number,
    ArchitectureRevisionRecord.parent_number,
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
        current_revision=record.current_revision,
        created_by_user_id=record.created_by_user_id,
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
    )


def _to_summary(row: Any) -> RevisionSummary:
    return RevisionSummary(
        number=row.number,
        parent_number=row.parent_number,
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
            ir=to_dict(revision.ir),
            ir_schema_version=revision.ir_schema_version,
            content_hash=revision.content_hash,
            source=revision.source.value,
            summary=revision.summary,
            reason=revision.reason,
            requirement_set_id=revision.requirement_set_id,
            created_by_user_id=revision.created_by_user_id,
        )

    async def add(
        self, architecture: NewArchitecture, first: NewRevision
    ) -> tuple[Architecture, ArchitectureRevision]:
        record = ArchitectureRecord(
            id=architecture.id,
            project_id=architecture.project_id,
            current_revision=first.number,
            created_by_user_id=architecture.created_by_user_id,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(record)
                await self._session.flush()
        except IntegrityError as error:
            if violated_constraint(error) != ONE_PER_PROJECT:
                raise
            raise ArchitectureAlreadyExists from None
        revision = self._revision_record(architecture.project_id, first)
        self._session.add(revision)
        await self._session.flush()
        await self._session.refresh(record)
        await self._session.refresh(revision)
        return _to_architecture(record), _to_revision(revision)

    async def get(self, project_id: uuid.UUID, *, for_update: bool = False) -> Architecture | None:
        query = select(ArchitectureRecord).where(ArchitectureRecord.project_id == project_id)
        if for_update:
            query = query.with_for_update()
        record = await self._session.scalar(query.execution_options(populate_existing=True))
        return _to_architecture(record) if record else None

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
            .values(current_revision=revision.number, updated_at=func.now())
            .returning(ArchitectureRecord)
            .execution_options(populate_existing=True)
        )
        if moved is None:  # the caller's row lock makes this impossible; never overwrite silently
            raise ArchitectureVersionConflict(details={"latest_version": None})
        await self._session.refresh(record)
        return _to_architecture(moved), _to_revision(record)

    async def get_revision(self, project_id: uuid.UUID, number: int) -> ArchitectureRevision | None:
        record = await self._session.scalar(
            select(ArchitectureRevisionRecord).where(
                ArchitectureRevisionRecord.project_id == project_id,
                ArchitectureRevisionRecord.number == number,
            )
        )
        return _to_revision(record) if record else None

    async def list_revisions(
        self, project_id: uuid.UUID, *, before: int | None, limit: int
    ) -> list[RevisionSummary]:
        query = select(*_SUMMARY).where(ArchitectureRevisionRecord.project_id == project_id)
        if before is not None:
            query = query.where(ArchitectureRevisionRecord.number < before)
        rows = await self._session.execute(
            query.order_by(ArchitectureRevisionRecord.number.desc()).limit(limit)
        )
        return [_to_summary(row) for row in rows]

    async def get_layout(self, project_id: uuid.UUID) -> ArchitectureLayout:
        record = await self._session.scalar(
            select(ArchitectureLayoutRecord).where(ArchitectureLayoutRecord.project_id == project_id)
        )
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
