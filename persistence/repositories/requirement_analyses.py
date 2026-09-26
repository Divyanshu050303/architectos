import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.requirements.analyses import NewRequirementAnalysis, RequirementAnalysis
from persistence.models import RequirementAnalysisRecord


def to_analysis(record: RequirementAnalysisRecord) -> RequirementAnalysis:
    return RequirementAnalysis(
        id=record.id,
        project_id=record.project_id,
        raw_input=record.raw_input,
        input_sha256=record.input_sha256,
        engine_version=record.engine_version,
        result=record.result,
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
    )


class SqlAlchemyRequirementAnalysisRepository:
    """Append-only: there is no update or delete (the table's trigger refuses them)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, analysis: NewRequirementAnalysis) -> RequirementAnalysis:
        record = RequirementAnalysisRecord(
            project_id=analysis.project_id,
            raw_input=analysis.raw_input,
            input_sha256=analysis.input_sha256,
            engine_version=analysis.engine_version,
            result=analysis.result,
            created_by_user_id=analysis.created_by_user_id,
        )
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        return to_analysis(record)

    async def get(self, project_id: uuid.UUID, analysis_id: uuid.UUID) -> RequirementAnalysis | None:
        record = await self._session.scalar(
            select(RequirementAnalysisRecord).where(
                RequirementAnalysisRecord.id == analysis_id,
                RequirementAnalysisRecord.project_id == project_id,
            )
        )
        return to_analysis(record) if record else None
