"""Architecture diffs and their explanation runs: rows inserted once, never updated. Every read is
scoped by project; a stored diff or run is read back through the domain's own constructors, and
listings never load a diff's parts."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.architecture_diff.diffs import ArchitectureDiff
from core.domain.architecture_diff.explanations import ExplanationRun
from core.domain.architecture_diff.records import (
    diff_document,
    diff_from,
    explanation_document,
    explanation_run_from,
    state_ref,
)
from core.domain.architecture_diff.references import ComparedState
from core.domain.architecture_diff.repository import DiffListing
from persistence.models import ArchitectureDiffRecord, DiffExplanationRecord

D = ArchitectureDiffRecord
E = DiffExplanationRecord
SIDE = ("kind", "architecture_id", "revision_number", "run_id", "content_hash", "label")
LISTED = (
    D.id,
    *(getattr(D, f"{side}_{part}") for side in ("base", "target") for part in SIDE),
    D.change_count,
    D.counts,
    D.requested_by_user_id,
    D.compared_at,
)


def _row(record: object, table: type[D] | type[E]) -> dict[str, Any]:
    return {column.key: getattr(record, column.key) for column in table.__table__.columns}


def _compared(row: object, side: str) -> ComparedState:
    values: dict[str, Any] = {part: getattr(row, f"{side}_{part}") for part in SIDE}
    return ComparedState(state_ref(values), values["content_hash"], values["label"])


class SqlAlchemyArchitectureDiffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, diff: ArchitectureDiff) -> ArchitectureDiff:
        self._session.add(ArchitectureDiffRecord(**diff_document(diff)))
        await self._session.flush()
        return diff

    async def get(self, project_id: uuid.UUID, diff_id: uuid.UUID) -> ArchitectureDiff | None:
        record = await self._session.scalar(select(D).where(D.project_id == project_id, D.id == diff_id))
        return diff_from(_row(record, D)) if record else None

    async def add_explanation(self, project_id: uuid.UUID, run: ExplanationRun) -> ExplanationRun:
        self._session.add(DiffExplanationRecord(**explanation_document(project_id, run)))
        await self._session.flush()
        return run

    async def list_explanations(self, project_id: uuid.UUID, diff_id: uuid.UUID) -> list[ExplanationRun]:
        statement = (
            select(E).where(E.project_id == project_id, E.diff_id == diff_id).order_by(E.requested_at, E.id)
        )
        records = await self._session.scalars(statement)
        return [explanation_run_from(_row(r, E)) for r in records]

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[DiffListing]:
        explained = (
            select(func.count())
            .where(E.diff_id == D.id, E.project_id == D.project_id)
            .correlate(D)
            .scalar_subquery()
        )
        statement = select(*LISTED, explained.label("explanations")).where(D.project_id == project_id)
        if architecture_id is not None:
            statement = statement.where(
                or_(D.base_architecture_id == architecture_id, D.target_architecture_id == architecture_id)
            )
        if after is not None:
            compared_at, diff_id = after
            statement = statement.where(
                tuple_(D.compared_at, D.id) < tuple_(literal(compared_at), literal(diff_id))
            )
        ordered = statement.order_by(D.compared_at.desc(), D.id.desc()).limit(limit)
        rows = await self._session.execute(ordered)
        return [
            DiffListing(
                id=row.id,
                base=_compared(row, "base"),
                target=_compared(row, "target"),
                change_count=row.change_count,
                counts=dict(row.counts),
                explanations=row.explanations,
                requested_by_user_id=row.requested_by_user_id,
                compared_at=row.compared_at,
            )
            for row in rows
        ]
