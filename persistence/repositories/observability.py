"""Observability analyses with their components and findings (append-only). Every read is scoped by
project (and, for analyses, architecture); rows are read only for an analysis already found."""

import uuid

from sqlalchemy import insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.engine_results import Limitation, ModelSet, Unsupported
from core.domain.observability.analyses import ObservabilityAnalysis, ObservabilityAnalysisError
from core.domain.observability.queries import (
    ObservabilityAnalysisQuery,
    ObservabilityComponentQuery,
    ObservabilityFindingQuery,
)
from core.domain.observability.reports import ObservabilityReport
from core.domain.observability.results import CheckResult, ComponentResult, ObservabilityFinding
from core.domain.observability.values import Dimension
from persistence.models import (
    ObservabilityAnalysisRecord,
    ObservabilityComponentRecord,
    ObservabilityFindingRecord,
)

_BATCH = 500
A, C, F = ObservabilityAnalysisRecord, ObservabilityComponentRecord, ObservabilityFindingRecord


def to_report(record: ObservabilityAnalysisRecord) -> ObservabilityReport:
    error = (
        ObservabilityAnalysisError(record.error_code, record.error_message or "")
        if record.error_code
        else None
    )
    analysis = ObservabilityAnalysis(
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
    return ObservabilityReport(
        analysis=analysis,
        inputs=record.inputs,
        analyzer_set=ModelSet.from_dict(record.analyzer_set) if record.analyzer_set else None,
        context_fingerprint=record.context_fingerprint,
        result_fingerprint=record.result_fingerprint,
        summary=record.summary,
        checks=tuple(CheckResult.from_dict(c) for c in record.checks),
        unsupported=tuple(Unsupported.from_dict(u) for u in record.unsupported),
        limitations=tuple(Limitation.from_dict(x) for x in record.limitations),
    )


class SqlAlchemyObservabilityAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        report: ObservabilityReport,
        components: tuple[ComponentResult, ...],
        findings: tuple[ObservabilityFinding, ...],
    ) -> ObservabilityReport:
        analysis = report.analysis
        self._session.add(
            ObservabilityAnalysisRecord(
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
                analyzer_set=report.analyzer_set.to_dict() if report.analyzer_set else None,
                context_fingerprint=report.context_fingerprint,
                result_fingerprint=report.result_fingerprint,
                summary=dict(report.summary) if report.summary is not None else None,
                checks=[c.to_dict() for c in report.checks],
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
                C,
                [
                    {
                        "id": uuid.uuid7(),
                        **common,
                        "node_id": c.node_id,
                        "criticality": c.criticality,
                        **{d.value: c.coverage[d].value for d in Dimension},
                        "data": c.to_dict(),
                    }
                    for c in components
                ],
            ),
            (
                F,
                [
                    {
                        "id": uuid.uuid7(),
                        **common,
                        "position": position,
                        "finding_id": f.id,
                        "type": f.type.value,
                        "category": f.category.value,
                        "basis": f.basis.value,
                        "severity": f.severity.value,
                        "certainty": f.certainty.value,
                        "dimension": f.dimension.value if f.dimension is not None else None,
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
    ) -> ObservabilityReport | None:
        record = await self._session.scalar(
            select(A).where(
                A.id == analysis_id, A.project_id == project_id, A.architecture_id == architecture_id
            )
        )
        return to_report(record) if record else None

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: ObservabilityAnalysisQuery
    ) -> list[ObservabilityReport]:
        statement = select(A).where(A.project_id == project_id, A.architecture_id == architecture_id)
        if query.revision is not None:
            statement = statement.where(A.revision_number == query.revision)
        if query.after is not None:
            requested_at, analysis_id = query.after
            statement = statement.where(
                tuple_(A.requested_at, A.id) < tuple_(literal(requested_at), literal(analysis_id))
            )
        ordered = statement.order_by(A.requested_at.desc(), A.id.desc())
        return [to_report(r) for r in await self._session.scalars(ordered.limit(query.limit))]

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ObservabilityComponentQuery
    ) -> list[ComponentResult]:
        statement = select(C.data).where(C.analysis_id == analysis_id, C.project_id == project_id)
        if query.criticality == "not_modeled":
            statement = statement.where(C.criticality.is_(None))
        elif query.criticality is not None:
            statement = statement.where(C.criticality == query.criticality)
        if query.dimension is not None and query.state is not None:
            statement = statement.where(getattr(C, query.dimension.value) == query.state.value)
        if query.after is not None:
            statement = statement.where(C.node_id > query.after)
        rows = await self._session.scalars(statement.order_by(C.node_id).limit(query.limit))
        return [ComponentResult.from_dict(data) for data in rows]

    async def list_findings(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ObservabilityFindingQuery
    ) -> list[tuple[int, ObservabilityFinding]]:
        statement = select(F.position, F.data).where(F.analysis_id == analysis_id, F.project_id == project_id)
        for column, value in (
            (F.severity, query.severity),
            (F.type, query.type),
            (F.category, query.category),
            (F.basis, query.basis),
            (F.certainty, query.certainty),
            (F.dimension, query.dimension),
        ):
            if value is not None:
                statement = statement.where(column == value.value)
        if query.after is not None:
            statement = statement.where(F.position > query.after)
        rows = await self._session.execute(statement.order_by(F.position).limit(query.limit))
        return [(position, ObservabilityFinding.from_dict(data)) for position, data in rows]
