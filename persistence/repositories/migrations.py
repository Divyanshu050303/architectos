"""Migration plan versions: one row per version, its content written once; reviews replace only the
status and review history. Every read is scoped by project, and every stored proposal is verified
against its fingerprint when read back."""

import uuid
from datetime import datetime

from sqlalchemy import literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.migrations.entities import MigrationPlanVersion
from core.domain.migrations.serialization import proposal_from_dict, request_from_dict, reviews_from_list
from core.domain.migrations.values import PlanStatus
from persistence.models import MigrationPlanVersionRecord

R = MigrationPlanVersionRecord


def to_version(record: MigrationPlanVersionRecord) -> MigrationPlanVersion:
    return MigrationPlanVersion(
        id=record.id,
        plan_id=record.plan_id,
        version=record.version,
        project_id=record.project_id,
        request=request_from_dict(record.request),
        proposal=proposal_from_dict(record.proposal, record.fingerprint),
        status=PlanStatus(record.status),
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        reviews=reviews_from_list(record.reviews),
    )


class SqlAlchemyMigrationPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: MigrationPlanVersion) -> MigrationPlanVersion:
        proposal, target = version.proposal, version.proposal.target
        self._session.add(
            MigrationPlanVersionRecord(
                id=version.id,
                plan_id=version.plan_id,
                version=version.version,
                project_id=version.project_id,
                architecture_id=proposal.source.architecture_id,
                source_revision_number=proposal.source.revision_number,
                source_content_hash=proposal.source.content_hash,
                target_kind=target.kind.value,
                target_revision_number=target.revision_number,
                target_content_hash=target.content_hash,
                target_analysis_id=target.analysis_id,
                target_candidate_id=target.candidate_id,
                strategy=proposal.strategy,
                status=version.status.value,
                title=version.title,
                request=version.request.to_dict(),
                proposal=proposal.to_dict(),
                fingerprint=proposal.fingerprint,
                reviews=[r.to_dict() for r in version.reviews],
                created_by_user_id=version.created_by_user_id,
                created_at=version.created_at,
            )
        )
        await self._session.flush()
        return version

    async def update_review(self, version: MigrationPlanVersion) -> MigrationPlanVersion:
        record = await self._session.scalar(
            select(R).where(R.id == version.id, R.project_id == version.project_id).with_for_update()
        )
        if record is None:
            raise LookupError(version.id)  # the service read it under the same lock: never expected
        record.status = version.status.value
        record.reviews = [r.to_dict() for r in version.reviews]
        await self._session.flush()
        return version

    async def get(
        self,
        project_id: uuid.UUID,
        plan_id: uuid.UUID,
        version: int | None = None,
        *,
        for_update: bool = False,
    ) -> MigrationPlanVersion | None:
        statement = select(R).where(R.project_id == project_id, R.plan_id == plan_id)
        if version is not None:
            statement = statement.where(R.version == version)
        statement = statement.order_by(R.version.desc()).limit(1)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        return to_version(record) if record else None

    async def history(self, project_id: uuid.UUID, plan_id: uuid.UUID) -> list[MigrationPlanVersion]:
        records = await self._session.scalars(
            select(R).where(R.project_id == project_id, R.plan_id == plan_id).order_by(R.version)
        )
        return [to_version(r) for r in records]

    async def list_latest(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: PlanStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[MigrationPlanVersion]:
        # The latest version of a plan is the one no later version superseded.
        statement = select(R).where(R.project_id == project_id, R.status != PlanStatus.SUPERSEDED.value)
        if architecture_id is not None:
            statement = statement.where(R.architecture_id == architecture_id)
        if status is not None:
            statement = statement.where(R.status == status.value)
        if after is not None:
            created_at, version_id = after
            statement = statement.where(
                tuple_(R.created_at, R.id) < tuple_(literal(created_at), literal(version_id))
            )
        records = await self._session.scalars(
            statement.order_by(R.created_at.desc(), R.id.desc()).limit(limit)
        )
        return [to_version(r) for r in records]
