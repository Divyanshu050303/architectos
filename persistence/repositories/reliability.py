"""Reliability analyses with their components and findings (append-only). Every read is scoped by
project (and, for analyses, architecture); rows are read only for an analysis already found."""

import uuid

from sqlalchemy import insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.engine_results import Limitation, ModelSet, Unsupported
from core.domain.reliability.analyses import ReliabilityAnalysis, ReliabilityAnalysisError
from core.domain.reliability.queries import (
    ReliabilityAnalysisQuery,
    ReliabilityComponentQuery,
    ReliabilityFindingQuery,
)
from core.domain.reliability.reports import ReliabilityReport
from core.domain.reliability.results import ComponentResult, ObjectiveResult, PathResult, ReliabilityFinding
from persistence.models import ReliabilityAnalysisRecord, ReliabilityComponentRecord, ReliabilityFindingRecord

_BATCH = 500


def to_report(record: ReliabilityAnalysisRecord) -> ReliabilityReport:
    error = (
        ReliabilityAnalysisError(record.error_code, record.error_message or "") if record.error_code else None
    )
    analysis = ReliabilityAnalysis(
        id=record.id,
        project_id=record.project_id,
        architecture_id=record.architecture_id,
        revision_number=record.revision_number,
        revision_content_hash=record.revision_content_hash,
        status=record.status,
        requested_by_user_id=record.requested_by_user_id,
        requested_at=record.requested_at,
        label=record.label,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error=error,
    )
    return ReliabilityReport(
        analysis=analysis,
        inputs=record.inputs,
        model_set=ModelSet.from_dict(record.model_set) if record.model_set else None,
        context_fingerprint=record.context_fingerprint,
        result_fingerprint=record.result_fingerprint,
        summary=record.summary,
        paths=tuple(PathResult.from_dict(p) for p in record.paths),
        objectives=tuple(ObjectiveResult.from_dict(o) for o in record.objectives),
        unsupported=tuple(Unsupported.from_dict(u) for u in record.unsupported),
        limitations=tuple(Limitation.from_dict(x) for x in record.limitations),
    )


class SqlAlchemyReliabilityAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        report: ReliabilityReport,
        components: tuple[ComponentResult, ...],
        findings: tuple[ReliabilityFinding, ...],
    ) -> ReliabilityReport:
        analysis = report.analysis
        self._session.add(
            ReliabilityAnalysisRecord(
                id=analysis.id,
                project_id=analysis.project_id,
                architecture_id=analysis.architecture_id,
                revision_number=analysis.revision_number,
                revision_content_hash=analysis.revision_content_hash,
                status=analysis.status,
                label=analysis.label,
                requested_by_user_id=analysis.requested_by_user_id,
                requested_at=analysis.requested_at,
                started_at=analysis.started_at,
                completed_at=analysis.completed_at,
                inputs=dict(report.inputs),
                model_set=report.model_set.to_dict() if report.model_set else None,
                context_fingerprint=report.context_fingerprint,
                result_fingerprint=report.result_fingerprint,
                summary=dict(report.summary) if report.summary is not None else None,
                paths=[p.to_dict() for p in report.paths],
                objectives=[o.to_dict() for o in report.objectives],
                unsupported=[u.to_dict() for u in report.unsupported],
                limitations=[x.to_dict() for x in report.limitations],
                error_code=analysis.error.code if analysis.error else None,
                error_message=analysis.error.message if analysis.error else None,
            )
        )
        await self._session.flush()
        common = {"analysis_id": analysis.id, "project_id": analysis.project_id}
        rows = (
            (
                ReliabilityComponentRecord,
                [
                    {
                        "id": uuid.uuid7(),
                        **common,
                        "node_id": c.node_id,
                        "status": c.status.value,
                        "data": c.to_dict(),
                    }
                    for c in components
                ],
            ),
            (
                ReliabilityFindingRecord,
                [
                    {
                        "id": uuid.uuid7(),
                        **common,
                        "position": position,
                        "finding_id": f.id,
                        "type": f.type.value,
                        "severity": f.severity.value,
                        "certainty": f.certainty.value,
                        "data": f.to_dict(),
                    }
                    for position, f in enumerate(findings)
                ],
            ),
        )
        for model, batch in rows:
            for start in range(0, len(batch), _BATCH):
                await self._session.execute(insert(model), batch[start : start + _BATCH])
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> ReliabilityReport | None:
        record = await self._session.scalar(
            select(ReliabilityAnalysisRecord).where(
                ReliabilityAnalysisRecord.id == analysis_id,
                ReliabilityAnalysisRecord.project_id == project_id,
                ReliabilityAnalysisRecord.architecture_id == architecture_id,
            )
        )
        return to_report(record) if record else None

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: ReliabilityAnalysisQuery
    ) -> list[ReliabilityReport]:
        statement = select(ReliabilityAnalysisRecord).where(
            ReliabilityAnalysisRecord.project_id == project_id,
            ReliabilityAnalysisRecord.architecture_id == architecture_id,
        )
        if query.revision is not None:
            statement = statement.where(ReliabilityAnalysisRecord.revision_number == query.revision)
        if query.after is not None:
            requested_at, analysis_id = query.after
            statement = statement.where(
                tuple_(ReliabilityAnalysisRecord.requested_at, ReliabilityAnalysisRecord.id)
                < tuple_(literal(requested_at), literal(analysis_id))
            )
        ordered = statement.order_by(
            ReliabilityAnalysisRecord.requested_at.desc(), ReliabilityAnalysisRecord.id.desc()
        )
        return [to_report(r) for r in await self._session.scalars(ordered.limit(query.limit))]

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ReliabilityComponentQuery
    ) -> list[ComponentResult]:
        statement = select(ReliabilityComponentRecord.data).where(
            ReliabilityComponentRecord.analysis_id == analysis_id,
            ReliabilityComponentRecord.project_id == project_id,
        )
        if query.status is not None:
            statement = statement.where(ReliabilityComponentRecord.status == query.status.value)
        if query.after is not None:
            statement = statement.where(ReliabilityComponentRecord.node_id > query.after)
        rows = await self._session.scalars(
            statement.order_by(ReliabilityComponentRecord.node_id).limit(query.limit)
        )
        return [ComponentResult.from_dict(data) for data in rows]

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ReliabilityFindingQuery
    ) -> list[tuple[int, ReliabilityFinding]]:
        statement = select(ReliabilityFindingRecord.position, ReliabilityFindingRecord.data).where(
            ReliabilityFindingRecord.analysis_id == analysis_id,
            ReliabilityFindingRecord.project_id == project_id,
        )
        for column, value in (
            (ReliabilityFindingRecord.severity, query.severity),
            (ReliabilityFindingRecord.type, query.type),
            (ReliabilityFindingRecord.certainty, query.certainty),
        ):
            if value is not None:
                statement = statement.where(column == value.value)
        if query.after is not None:
            statement = statement.where(ReliabilityFindingRecord.position > query.after)
        rows = await self._session.execute(
            statement.order_by(ReliabilityFindingRecord.position).limit(query.limit)
        )
        return [(position, ReliabilityFinding.from_dict(data)) for position, data in rows]
