"""Evolution analyses with their candidates (append-only). Every read is scoped by project (and, for
analyses, architecture); candidates are read only for an analysis already found."""

import uuid

from sqlalchemy import cast, insert, literal, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.engine_results import Limitation, ModelSet, read_evidence
from core.domain.evolution.candidates import Candidate, EvidenceRef
from core.domain.evolution.entities import EvolutionAnalysis, EvolutionError
from core.domain.evolution.goals import EvolutionGoal
from core.domain.evolution.queries import CandidateQuery, EvolutionQuery
from core.domain.evolution.reports import EvolutionReport
from core.domain.evolution.results import EvolutionFinding
from persistence.models import EvolutionAnalysisRecord, EvolutionCandidateRecord

_BATCH = 200
E, C = EvolutionAnalysisRecord, EvolutionCandidateRecord


def to_report(record: EvolutionAnalysisRecord) -> EvolutionReport:
    error = EvolutionError(record.error_code, record.error_message or "") if record.error_code else None
    analysis = EvolutionAnalysis(
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
    return EvolutionReport(
        analysis=analysis,
        inputs=record.inputs,
        model_set=ModelSet.from_dict(record.model_set) if record.model_set else None,
        result_fingerprint=record.result_fingerprint,
        summary=record.summary,
        goals=tuple(EvolutionGoal.from_dict(g) for g in record.goals),
        findings=tuple(EvolutionFinding.from_dict(f) for f in record.findings),
        evidence=tuple(EvidenceRef.from_dict(e) for e in record.evidence),
        assumptions=read_evidence(record.assumptions),
        limitations=tuple(Limitation.from_dict(x) for x in record.limitations),
    )


class SqlAlchemyEvolutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, report: EvolutionReport, candidates: tuple[Candidate, ...]) -> EvolutionReport:
        analysis = report.analysis
        self._session.add(
            EvolutionAnalysisRecord(
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
                result_fingerprint=report.result_fingerprint,
                summary=dict(report.summary) if report.summary is not None else None,
                goals=[g.to_dict() for g in report.goals],
                findings=[f.to_dict() for f in report.findings],
                evidence=[e.to_dict() for e in report.evidence],
                assumptions=[e.to_dict() for e in report.assumptions],
                limitations=[x.to_dict() for x in report.limitations],
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
                "candidate_id": c.id,
                "position": position,
                "category": c.category.value,
                "validation": c.validation.value,
                "rule_id": c.rule.id,
                "data": c.to_dict(),
            }
            for position, c in enumerate(candidates)
        ]
        for start in range(0, len(rows), _BATCH):
            await self._session.execute(insert(C), rows[start : start + _BATCH])
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
    ) -> EvolutionReport | None:
        record = await self._session.scalar(
            select(E).where(
                E.id == analysis_id, E.project_id == project_id, E.architecture_id == architecture_id
            )
        )
        return to_report(record) if record else None

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: EvolutionQuery
    ) -> list[EvolutionReport]:
        statement = select(E).where(E.project_id == project_id, E.architecture_id == architecture_id)
        if query.revision is not None:
            statement = statement.where(E.revision_number == query.revision)
        if query.after is not None:
            requested_at, analysis_id = query.after
            statement = statement.where(
                tuple_(E.requested_at, E.id) < tuple_(literal(requested_at), literal(analysis_id))
            )
        ordered = statement.order_by(E.requested_at.desc(), E.id.desc())
        return [to_report(r) for r in await self._session.scalars(ordered.limit(query.limit))]

    async def list_candidates(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, query: CandidateQuery
    ) -> list[tuple[int, Candidate]]:
        statement = select(C.position, C.data).where(C.analysis_id == analysis_id, C.project_id == project_id)
        if query.category is not None:
            statement = statement.where(C.category == query.category.value)
        if query.validation is not None:
            statement = statement.where(C.validation == query.validation.value)
        if query.goal is not None:
            statement = statement.where(C.data["goals"].contains(cast([query.goal], JSONB)))
        if query.after is not None:
            statement = statement.where(C.position > query.after)
        rows = await self._session.execute(statement.order_by(C.position).limit(query.limit))
        return [(position, Candidate.from_dict(data)) for position, data in rows]

    async def get_candidate(
        self, project_id: uuid.UUID, analysis_id: uuid.UUID, candidate_id: str
    ) -> Candidate | None:
        data = await self._session.scalar(
            select(C.data).where(
                C.analysis_id == analysis_id, C.project_id == project_id, C.candidate_id == candidate_id
            )
        )
        return Candidate.from_dict(data) if data is not None else None

    async def candidates(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> tuple[Candidate, ...]:
        rows = await self._session.scalars(
            select(C.data)
            .where(C.analysis_id == analysis_id, C.project_id == project_id)
            .order_by(C.position)
        )
        return tuple(Candidate.from_dict(data) for data in rows)
