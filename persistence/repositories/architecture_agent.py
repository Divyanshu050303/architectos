"""Architecture agent runs: one row per run, written when a pass ends and when a person decides. Every
read is scoped by project; a stored run is read back through the domain's own constructors (and its
candidate checked against its content hash), and listings never load the run's parts."""

import uuid
from datetime import datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.architecture_agent.records import run_document, run_from
from core.domain.architecture_agent.repository import RunListing
from core.domain.architecture_agent.runs import AgentRun
from core.domain.architecture_agent.values import FailureCode, RunStatus, Stage
from persistence.models import AgentRunRecord

R = AgentRunRecord
LISTED = (
    R.id,
    R.requirement_set_id,
    R.base_architecture_id,
    R.base_revision_number,
    R.status,
    R.stage,
    R.model,
    R.failure,
    R.candidate_content_hash,
    R.accepted_architecture_id,
    R.accepted_revision_number,
    R.requested_by_user_id,
    R.requested_at,
    R.completed_at,
)
MUTABLE = (
    "status", "stage", "usage", "model", "prompt_version", "questions", "answers", "proposal", "rejections",
    "candidate", "candidate_content_hash", "reports", "raw_output_sha256", "raw_output_bytes", "failure",
    "accepted_architecture_id", "accepted_revision_number", "decision_reason", "history", "limitations",
    "completed_at",
)  # fmt: skip


def to_run(record: AgentRunRecord) -> AgentRun:
    return run_from({column.key: getattr(record, column.key) for column in R.__table__.columns})


class SqlAlchemyAgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: AgentRun) -> AgentRun:
        self._session.add(AgentRunRecord(**run_document(run)))
        await self._session.flush()
        return run

    async def save(self, run: AgentRun) -> AgentRun:
        record = await self._session.scalar(
            select(R).where(R.id == run.id, R.project_id == run.project_id).with_for_update()
        )
        if record is None:
            raise LookupError(run.id)  # the service read it under the same lock: never expected
        document = run_document(run)
        for column in MUTABLE:
            setattr(record, column, document[column])
        await self._session.flush()
        return run

    async def get(
        self, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
    ) -> AgentRun | None:
        statement = select(R).where(R.project_id == project_id, R.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return to_run(record) if record else None

    async def list(
        self,
        project_id: uuid.UUID,
        *,
        status: RunStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[RunListing]:
        statement = select(*LISTED).where(R.project_id == project_id)
        if status is not None:
            statement = statement.where(R.status == status.value)
        if after is not None:
            requested_at, run_id = after
            statement = statement.where(
                tuple_(R.requested_at, R.id) < tuple_(literal(requested_at), literal(run_id))
            )
        ordered = statement.order_by(R.requested_at.desc(), R.id.desc()).limit(limit)
        rows = await self._session.execute(ordered)
        return [
            RunListing(
                id=row.id,
                requirement_set_id=row.requirement_set_id,
                base_architecture_id=row.base_architecture_id,
                base_revision_number=row.base_revision_number,
                status=RunStatus(row.status),
                stage=Stage(row.stage),
                model=row.model,
                failure=FailureCode(row.failure["code"]) if row.failure else None,
                candidate_content_hash=row.candidate_content_hash,
                accepted_architecture_id=row.accepted_architecture_id,
                accepted_revision_number=row.accepted_revision_number,
                requested_by_user_id=row.requested_by_user_id,
                requested_at=row.requested_at,
                completed_at=row.completed_at,
            )
            for row in rows
        ]
