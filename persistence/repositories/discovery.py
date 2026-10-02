"""Discovery runs: one row per run, its request and result written once; reviews replace only the
decisions and acceptances. Every read is scoped by project, every stored result is verified against
its fingerprint when read back, and listings never load results."""

import uuid
from datetime import datetime

from sqlalchemy import delete, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.discovery.runs import Baseline, DiscoveryRun, RunListing
from core.domain.discovery.serialization import (
    acceptances_from_list,
    decisions_from_list,
    error_from_dict,
    result_from_dict,
)
from core.domain.discovery.values import RunStatus, SourceType
from persistence.models import DiscoveryRunRecord

R = DiscoveryRunRecord
LISTED = (
    R.id,
    R.project_id,
    R.status,
    R.requested_by_user_id,
    R.requested_at,
    R.source_type,
    R.baseline_architecture_id,
    R.baseline_revision_number,
    R.label,
    R.completed_at,
    R.summary,
    R.fingerprint,
    R.sources_fingerprint,
    R.error,
    R.decisions,
    R.acceptances,
)


def _baseline(architecture_id: uuid.UUID | None, revision_number: int | None) -> Baseline | None:
    if architecture_id is None or revision_number is None:
        return None
    return Baseline(architecture_id, revision_number)


def to_run(record: DiscoveryRunRecord) -> DiscoveryRun:
    fingerprint = record.fingerprint
    return DiscoveryRun(
        id=record.id,
        project_id=record.project_id,
        status=RunStatus(record.status),
        requested_by_user_id=record.requested_by_user_id,
        requested_at=record.requested_at,
        source_type=SourceType(record.source_type) if record.source_type else None,
        baseline=_baseline(record.baseline_architecture_id, record.baseline_revision_number),
        label=record.label,
        started_at=record.started_at,
        completed_at=record.completed_at,
        result=result_from_dict(record.result, fingerprint) if record.result and fingerprint else None,
        error=error_from_dict(record.error),
        decisions=decisions_from_list(record.decisions),
        acceptances=acceptances_from_list(record.acceptances),
    )


class SqlAlchemyDiscoveryRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: DiscoveryRun) -> DiscoveryRun:
        result, baseline = run.result, run.baseline
        self._session.add(
            DiscoveryRunRecord(
                id=run.id,
                project_id=run.project_id,
                status=run.status.value,
                source_type=run.source_type.value if run.source_type else None,
                label=run.label,
                baseline_architecture_id=baseline.architecture_id if baseline else None,
                baseline_revision_number=baseline.revision_number if baseline else None,
                requested_by_user_id=run.requested_by_user_id,
                requested_at=run.requested_at,
                started_at=run.started_at,
                completed_at=run.completed_at,
                result=result.to_dict() if result else None,
                summary=result.summary() if result else None,
                fingerprint=result.fingerprint if result else None,
                sources_fingerprint=result.sources_fingerprint if result else None,
                error=run.error.to_dict() if run.error else None,
                decisions=[d.to_dict() for d in run.decisions],
                acceptances=[a.to_dict() for a in run.acceptances],
            )
        )
        await self._session.flush()
        return run

    async def update_review(self, run: DiscoveryRun) -> DiscoveryRun:
        record = await self._session.scalar(
            select(R).where(R.id == run.id, R.project_id == run.project_id).with_for_update()
        )
        if record is None:
            raise LookupError(run.id)  # the service read it under the same lock: never expected
        record.decisions = [d.to_dict() for d in run.decisions]
        record.acceptances = [a.to_dict() for a in run.acceptances]
        await self._session.flush()
        return run

    async def get(
        self, project_id: uuid.UUID, run_id: uuid.UUID, *, for_update: bool = False
    ) -> DiscoveryRun | None:
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
        rows = await self._session.execute(
            statement.order_by(R.requested_at.desc(), R.id.desc()).limit(limit)
        )
        return [
            RunListing(
                id=row.id,
                project_id=row.project_id,
                status=RunStatus(row.status),
                requested_by_user_id=row.requested_by_user_id,
                requested_at=row.requested_at,
                source_type=SourceType(row.source_type) if row.source_type else None,
                baseline=_baseline(row.baseline_architecture_id, row.baseline_revision_number),
                label=row.label,
                completed_at=row.completed_at,
                summary=row.summary,
                fingerprint=row.fingerprint,
                sources_fingerprint=row.sources_fingerprint,
                error=error_from_dict(row.error),
                decisions=len(row.decisions),
                acceptances=len(row.acceptances),
            )
            for row in rows
        ]

    async def delete(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        await self._session.execute(delete(R).where(R.project_id == project_id, R.id == run_id))
