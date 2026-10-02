"""Drift analyses (one row each, written once), drift items (identity fixed; only the latest detection,
review status, history, links and artifacts are saved) and identity mappings (appended). Every read is
scoped by project, and every stored result is verified against its fingerprint when read back."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import exists, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.drift.analyses import DriftAnalysis
from core.domain.drift.identity import IdentityMapping
from core.domain.drift.items import DriftItem
from core.domain.drift.serialization import (
    error_from_dict,
    item_from_dict,
    mapping_from_dict,
    request_from_dict,
    result_from_dict,
)
from core.domain.drift.values import AnalysisStatus, ReviewStatus
from persistence.models import DriftAnalysisRecord, DriftIdentityMappingRecord, DriftItemRecord

A = DriftAnalysisRecord
T = DriftItemRecord
M = DriftIdentityMappingRecord


def to_analysis(record: DriftAnalysisRecord) -> DriftAnalysis:
    fingerprint = record.fingerprint
    return DriftAnalysis(
        id=record.id,
        project_id=record.project_id,
        request=request_from_dict(record.request),
        status=AnalysisStatus(record.status),
        requested_by_user_id=record.requested_by_user_id,
        requested_at=record.requested_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        result=result_from_dict(record.result, fingerprint) if record.result and fingerprint else None,
        error=error_from_dict(record.error),
    )


def to_item(record: DriftItemRecord) -> DriftItem:
    return item_from_dict(
        {
            "id": str(record.id),
            "project_id": str(record.project_id),
            "architecture_id": str(record.architecture_id),
            "key": record.key,
            "element": record.element,
            "subject": record.subject,
            "path": record.path,
            "type": record.type,
            "classification": record.classification,
            "first_analysis_id": str(record.first_analysis_id),
            "last_analysis_id": str(record.last_analysis_id),
            "status": record.status,
            "history": record.history,
            "links": record.links,
            "artifacts": record.artifacts,
        }
    )


def to_mapping(record: DriftIdentityMappingRecord) -> IdentityMapping:
    return mapping_from_dict(
        {
            "architecture_id": str(record.architecture_id),
            "baseline_id": record.baseline_id,
            "discovered_key": record.discovered_key,
            "confirmed_by_user_id": str(record.confirmed_by_user_id),
            "confirmed_at": record.confirmed_at.isoformat(),
            "note": record.note,
        }
    )


def _detection(item: DriftItem) -> dict[str, Any]:
    """What a later analysis or a review changes (the guard trigger refuses anything else)."""
    data = item.to_dict()
    return {
        "type": data["type"],
        "classification": data["classification"],
        "last_analysis_id": item.last_analysis_id,
        "status": data["status"],
        "history": data["history"],
        "links": data["links"],
        "artifacts": data["artifacts"],
    }


class SqlAlchemyDriftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_analysis(self, analysis: DriftAnalysis) -> DriftAnalysis:
        result, request = analysis.result, analysis.request
        self._session.add(
            DriftAnalysisRecord(
                id=analysis.id,
                project_id=analysis.project_id,
                architecture_id=request.architecture_id,
                baseline_revision_number=request.baseline_revision,
                discovery_run_id=request.discovery_run_id,
                status=analysis.status.value,
                request=request.to_dict(),
                result=result.to_dict() if result else None,
                summary=result.summary() if result else None,
                fingerprint=result.fingerprint if result else None,
                error=analysis.error.to_dict() if analysis.error else None,
                requested_by_user_id=analysis.requested_by_user_id,
                requested_at=analysis.requested_at,
                started_at=analysis.started_at,
                completed_at=analysis.completed_at,
            )
        )
        await self._session.flush()
        return analysis

    async def get_analysis(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> DriftAnalysis | None:
        record = await self._session.scalar(select(A).where(A.project_id == project_id, A.id == analysis_id))
        return to_analysis(record) if record else None

    async def list_analyses(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: AnalysisStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[DriftAnalysis]:
        statement = select(A).where(A.project_id == project_id)
        if architecture_id is not None:
            statement = statement.where(A.architecture_id == architecture_id)
        if status is not None:
            statement = statement.where(A.status == status.value)
        if after is not None:
            requested_at, analysis_id = after
            statement = statement.where(
                tuple_(A.requested_at, A.id) < tuple_(literal(requested_at), literal(analysis_id))
            )
        records = await self._session.scalars(
            statement.order_by(A.requested_at.desc(), A.id.desc()).limit(limit)
        )
        return [to_analysis(r) for r in records]

    async def uses_discovery_run(self, project_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        used = await self._session.scalar(
            select(exists().where(A.project_id == project_id, A.discovery_run_id == run_id))
        )
        return bool(used)

    async def add_items(self, items: tuple[DriftItem, ...]) -> None:
        for item in items:
            self._session.add(
                DriftItemRecord(
                    id=item.id,
                    project_id=item.project_id,
                    architecture_id=item.architecture_id,
                    key=item.key,
                    element=item.element.value,
                    subject=item.subject,
                    path=item.path,
                    first_analysis_id=item.first_analysis_id,
                    **_detection(item),
                )
            )
        await self._session.flush()

    async def save_item(self, item: DriftItem) -> DriftItem:
        record = await self._session.scalar(
            select(T).where(T.id == item.id, T.project_id == item.project_id).with_for_update()
        )
        if record is None:
            raise LookupError(item.id)  # the service read it under the same lock: never expected
        for column, value in _detection(item).items():
            setattr(record, column, value)
        await self._session.flush()
        return item

    async def get_item(
        self, project_id: uuid.UUID, item_id: uuid.UUID, *, for_update: bool = False
    ) -> DriftItem | None:
        statement = select(T).where(T.project_id == project_id, T.id == item_id)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return to_item(record) if record else None

    async def items_of(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
    ) -> list[DriftItem]:
        statement = select(T).where(T.project_id == project_id, T.architecture_id == architecture_id)
        if for_update:
            statement = statement.with_for_update()
        return [to_item(r) for r in await self._session.scalars(statement.order_by(T.id))]

    async def list_items(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: ReviewStatus | None = None,
        after: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[DriftItem]:
        statement = select(T).where(T.project_id == project_id)
        if architecture_id is not None:
            statement = statement.where(T.architecture_id == architecture_id)
        if status is not None:
            statement = statement.where(T.status == status.value)
        if after is not None:
            statement = statement.where(T.id > after)
        records = await self._session.scalars(statement.order_by(T.id).limit(limit))
        return [to_item(r) for r in records]

    async def add_mapping(self, project_id: uuid.UUID, mapping: IdentityMapping) -> IdentityMapping:
        self._session.add(
            DriftIdentityMappingRecord(
                project_id=project_id,
                architecture_id=mapping.architecture_id,
                baseline_id=mapping.baseline_id,
                discovered_key=mapping.discovered_key,
                confirmed_by_user_id=mapping.confirmed_by_user_id,
                confirmed_at=mapping.confirmed_at,
                note=mapping.note,
            )
        )
        await self._session.flush()
        return mapping

    async def mappings(self, project_id: uuid.UUID, architecture_id: uuid.UUID) -> list[IdentityMapping]:
        records = await self._session.scalars(
            select(M)
            .where(M.project_id == project_id, M.architecture_id == architecture_id)
            .order_by(M.confirmed_at, M.id)
        )
        return [to_mapping(r) for r in records]
