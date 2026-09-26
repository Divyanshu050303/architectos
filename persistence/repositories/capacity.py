"""Capacity analyses with their components and bottlenecks (append-only). Every read is scoped by
project (and, for analyses, architecture); rows are read only for an analysis already found."""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.capacity.analyses import AnalysisError, AnalysisReport, CapacityAnalysis
from core.domain.capacity.queries import AnalysisQuery, BottleneckQuery, ComponentQuery
from core.domain.capacity.results import (
    Bottleneck,
    ComponentResult,
    Demand,
    Limitation,
    ModelSet,
    Summary,
    Unsupported,
)
from core.domain.capacity.scenarios import ScalingOption, ScenarioOutcome
from persistence.models import CapacityAnalysisRecord, CapacityBottleneckRecord, CapacityComponentRecord

_BATCH = 500


def to_report(record: CapacityAnalysisRecord) -> AnalysisReport:
    error = AnalysisError(record.error_code, record.error_message or "") if record.error_code else None
    analysis = CapacityAnalysis(
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
    return AnalysisReport(
        analysis=analysis,
        inputs=record.inputs,
        model_set=ModelSet.from_dict(record.model_set) if record.model_set else None,
        context_fingerprint=record.context_fingerprint,
        result_fingerprint=record.result_fingerprint,
        summary=Summary.from_dict(record.summary) if record.summary else None,
        connections=tuple(Demand.from_dict(d) for d in record.connections),
        unsupported=tuple(Unsupported.from_dict(u) for u in record.unsupported),
        limitations=tuple(Limitation.from_dict(x) for x in record.limitations),
        scaling=tuple(ScalingOption.from_dict(o) for o in record.scaling),
        unsupported_scaling=tuple(Unsupported.from_dict(u) for u in record.unsupported_scaling),
        scenarios=tuple(ScenarioOutcome.from_dict(s) for s in record.scenarios),
    )


class SqlAlchemyCapacityAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        analysis: CapacityAnalysis,
        inputs: Mapping[str, Any],
        scaling: tuple[ScalingOption, ...],
        unsupported_scaling: tuple[Unsupported, ...],
        scenarios: tuple[ScenarioOutcome, ...],
    ) -> AnalysisReport:
        report = AnalysisReport.of(analysis, inputs, scaling, unsupported_scaling, scenarios)
        self._session.add(
            CapacityAnalysisRecord(
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
                inputs=dict(inputs),
                model_set=report.model_set.to_dict() if report.model_set else None,
                context_fingerprint=report.context_fingerprint,
                result_fingerprint=report.result_fingerprint,
                summary=report.summary.to_dict() if report.summary else None,
                connections=[d.to_dict() for d in report.connections],
                unsupported=[u.to_dict() for u in report.unsupported],
                limitations=[x.to_dict() for x in report.limitations],
                scaling=[o.to_dict() for o in scaling],
                unsupported_scaling=[u.to_dict() for u in unsupported_scaling],
                scenarios=[s.to_dict() for s in scenarios],
                error_code=analysis.error.code if analysis.error else None,
                error_message=analysis.error.message if analysis.error else None,
            )
        )
        await self._session.flush()
        result = analysis.result
        if result is not None:
            components = [
                {
                    "id": uuid.uuid7(),
                    "analysis_id": analysis.id,
                    "project_id": analysis.project_id,
                    "node_id": c.node_id,
                    "status": c.status.value,
                    "data": c.to_dict(),
                }
                for c in result.components
            ]
            bottlenecks = [
                {
                    "id": uuid.uuid7(),
                    "analysis_id": analysis.id,
                    "project_id": analysis.project_id,
                    "position": position,
                    "node_id": b.node_id,
                    "resource": b.resource,
                    "condition": b.condition.value,
                    "certainty": b.certainty.value,
                    "data": b.to_dict(),
                }
                for position, b in enumerate(result.bottlenecks)
            ]
            for model, rows in (
                (CapacityComponentRecord, components),
                (CapacityBottleneckRecord, bottlenecks),
            ):
                for start in range(0, len(rows), _BATCH):
                    await self._session.execute(insert(model), rows[start : start + _BATCH])
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> AnalysisReport | None:
        record = await self._session.scalar(
            select(CapacityAnalysisRecord).where(
                CapacityAnalysisRecord.id == analysis_id,
                CapacityAnalysisRecord.project_id == project_id,
                CapacityAnalysisRecord.architecture_id == architecture_id,
            )
        )
        return to_report(record) if record else None

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: AnalysisQuery
    ) -> list[AnalysisReport]:
        statement = select(CapacityAnalysisRecord).where(
            CapacityAnalysisRecord.project_id == project_id,
            CapacityAnalysisRecord.architecture_id == architecture_id,
        )
        if query.revision is not None:
            statement = statement.where(CapacityAnalysisRecord.revision_number == query.revision)
        if query.after is not None:
            requested_at, analysis_id = query.after
            statement = statement.where(
                tuple_(CapacityAnalysisRecord.requested_at, CapacityAnalysisRecord.id)
                < tuple_(literal(requested_at), literal(analysis_id))
            )
        ordered = statement.order_by(
            CapacityAnalysisRecord.requested_at.desc(), CapacityAnalysisRecord.id.desc()
        )
        return [to_report(r) for r in await self._session.scalars(ordered.limit(query.limit))]

    async def list_components(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: ComponentQuery
    ) -> list[ComponentResult]:
        statement = select(CapacityComponentRecord.data).where(
            CapacityComponentRecord.analysis_id == analysis_id,
            CapacityComponentRecord.project_id == project_id,
        )
        if query.status is not None:
            statement = statement.where(CapacityComponentRecord.status == query.status.value)
        if query.after is not None:
            statement = statement.where(CapacityComponentRecord.node_id > query.after)
        rows = await self._session.scalars(
            statement.order_by(CapacityComponentRecord.node_id).limit(query.limit)
        )
        return [ComponentResult.from_dict(data) for data in rows]

    async def list_bottlenecks(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: BottleneckQuery
    ) -> list[Bottleneck]:
        statement = select(CapacityBottleneckRecord.data).where(
            CapacityBottleneckRecord.analysis_id == analysis_id,
            CapacityBottleneckRecord.project_id == project_id,
        )
        if query.certainty is not None:
            statement = statement.where(CapacityBottleneckRecord.certainty == query.certainty.value)
        rows = await self._session.scalars(statement.order_by(CapacityBottleneckRecord.position))
        return [Bottleneck.from_dict(data) for data in rows]
