"""Cost analyses with their line items (append-only). Every read is scoped by project (and, for
analyses, architecture); line items are read only for an analysis already found."""

import uuid

from sqlalchemy import insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.cost.analyses import CostAnalysis, CostAnalysisError
from core.domain.cost.queries import CostAnalysisQuery, LineItemQuery
from core.domain.cost.reports import CostReport
from core.domain.cost.results import LineItem, Totals
from core.domain.engine_results import Limitation, ModelSet, Unsupported, read_evidence
from persistence.models import CostAnalysisRecord, CostLineItemRecord

_BATCH = 500


def to_report(record: CostAnalysisRecord) -> CostReport:
    error = CostAnalysisError(record.error_code, record.error_message or "") if record.error_code else None
    analysis = CostAnalysis(
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
    return CostReport(
        analysis=analysis,
        inputs=record.inputs,
        organization_id=record.organization_id,
        snapshot_id=record.snapshot_id,
        currency=record.currency,
        snapshot_hash=record.snapshot_hash,
        model_set=ModelSet.from_dict(record.model_set) if record.model_set else None,
        context_fingerprint=record.context_fingerprint,
        result_fingerprint=record.result_fingerprint,
        totals=Totals.from_dict(record.totals) if record.totals else None,
        summary=record.summary,
        unsupported=tuple(Unsupported.from_dict(u) for u in record.unsupported),
        limitations=tuple(Limitation.from_dict(x) for x in record.limitations),
        assumptions=read_evidence(record.assumptions),
        scenarios=tuple(record.scenarios),
    )


class SqlAlchemyCostAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, report: CostReport, line_items: tuple[LineItem, ...]) -> CostReport:
        analysis = report.analysis
        self._session.add(
            CostAnalysisRecord(
                id=analysis.id,
                project_id=analysis.project_id,
                organization_id=report.organization_id,
                architecture_id=analysis.architecture_id,
                revision_number=analysis.revision_number,
                revision_content_hash=analysis.revision_content_hash,
                snapshot_id=report.snapshot_id,
                capacity_analysis_id=uuid.UUID(raw)
                if (raw := report.inputs.get("capacity_analysis_id"))
                else None,
                currency=report.currency,
                status=analysis.status,
                label=analysis.label,
                requested_by_user_id=analysis.requested_by_user_id,
                requested_at=analysis.requested_at,
                started_at=analysis.started_at,
                completed_at=analysis.completed_at,
                inputs=dict(report.inputs),
                snapshot_hash=report.snapshot_hash,
                model_set=report.model_set.to_dict() if report.model_set else None,
                context_fingerprint=report.context_fingerprint,
                result_fingerprint=report.result_fingerprint,
                totals=report.totals.to_dict() if report.totals else None,
                summary=dict(report.summary) if report.summary is not None else None,
                unsupported=[u.to_dict() for u in report.unsupported],
                limitations=[x.to_dict() for x in report.limitations],
                assumptions=[e.to_dict() for e in report.assumptions],
                scenarios=[dict(s) for s in report.scenarios],
                error_code=analysis.error.code if analysis.error else None,
                error_message=analysis.error.message if analysis.error else None,
            )
        )
        await self._session.flush()
        rows = [
            {
                "id": uuid.uuid7(),
                "analysis_id": analysis.id,
                "project_id": analysis.project_id,
                "element_id": line.element_id,
                "resource": line.resource,
                "category": line.category.value,
                "kind": line.kind.value,
                "status": line.status.value,
                "data": line.to_dict(),
            }
            for line in line_items
        ]
        for start in range(0, len(rows), _BATCH):
            await self._session.execute(insert(CostLineItemRecord), rows[start : start + _BATCH])
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> CostReport | None:
        record = await self._session.scalar(
            select(CostAnalysisRecord).where(
                CostAnalysisRecord.id == analysis_id,
                CostAnalysisRecord.project_id == project_id,
                CostAnalysisRecord.architecture_id == architecture_id,
            )
        )
        return to_report(record) if record else None

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: CostAnalysisQuery
    ) -> list[CostReport]:
        statement = select(CostAnalysisRecord).where(
            CostAnalysisRecord.project_id == project_id,
            CostAnalysisRecord.architecture_id == architecture_id,
        )
        if query.revision is not None:
            statement = statement.where(CostAnalysisRecord.revision_number == query.revision)
        if query.after is not None:
            requested_at, analysis_id = query.after
            statement = statement.where(
                tuple_(CostAnalysisRecord.requested_at, CostAnalysisRecord.id)
                < tuple_(literal(requested_at), literal(analysis_id))
            )
        ordered = statement.order_by(CostAnalysisRecord.requested_at.desc(), CostAnalysisRecord.id.desc())
        return [to_report(r) for r in await self._session.scalars(ordered.limit(query.limit))]

    async def list_line_items(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: LineItemQuery
    ) -> list[LineItem]:
        statement = select(CostLineItemRecord.data).where(
            CostLineItemRecord.analysis_id == analysis_id,
            CostLineItemRecord.project_id == project_id,
        )
        for column, value in (
            (CostLineItemRecord.element_id, query.element_id),
            (CostLineItemRecord.status, query.status.value if query.status else None),
            (CostLineItemRecord.category, query.category.value if query.category else None),
        ):
            if value is not None:
                statement = statement.where(column == value)
        if query.after is not None:
            statement = statement.where(
                tuple_(CostLineItemRecord.element_id, CostLineItemRecord.resource)
                > tuple_(literal(query.after[0]), literal(query.after[1]))
            )
        ordered = statement.order_by(CostLineItemRecord.element_id, CostLineItemRecord.resource)
        return [LineItem.from_dict(data) for data in await self._session.scalars(ordered.limit(query.limit))]
