"""Decisions: one row per decision, updated through its lifecycle. Every read is scoped by project."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.decisions.entities import (
    Decision,
    DecisionOption,
    DecisionSource,
    DecisionStatus,
    ResultingRevision,
)
from core.domain.engine_results import read_evidence
from core.domain.evolution.candidates import EvidenceRef
from persistence.models import DecisionRecord

R = DecisionRecord


def to_decision(record: DecisionRecord) -> Decision:
    linked = None
    if (
        record.resulting_revision_number is not None
        and record.resulting_content_hash is not None
        and record.linked_by_user_id is not None
        and record.linked_at is not None
    ):
        linked = ResultingRevision(
            record.resulting_revision_number,
            record.resulting_content_hash,
            record.linked_by_user_id,
            record.linked_at,
        )
    return Decision(
        id=record.id,
        project_id=record.project_id,
        architecture_id=record.architecture_id,
        number=record.number,
        title=record.title,
        status=DecisionStatus(record.status),
        context=record.context,
        options=tuple(DecisionOption.from_dict(o) for o in record.options),
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        source=DecisionSource.from_dict(record.source) if record.source else None,
        goals=tuple(record.goals),
        evidence=tuple(EvidenceRef.from_dict(e) for e in record.evidence),
        assumptions=read_evidence(record.assumptions),
        related_element_ids=tuple(record.related_element_ids),
        chosen_option=record.chosen_option,
        rationale=record.rationale,
        decided_by_user_id=record.decided_by_user_id,
        decided_at=record.decided_at,
        superseded_by=record.superseded_by,
        resulting_revision=linked,
    )


def _state(decision: Decision) -> dict[str, object]:
    """The fields a lifecycle change may set."""
    linked = decision.resulting_revision
    return {
        "status": decision.status.value,
        "chosen_option": decision.chosen_option,
        "rationale": decision.rationale,
        "decided_by_user_id": decision.decided_by_user_id,
        "decided_at": decision.decided_at,
        "superseded_by": decision.superseded_by,
        "resulting_revision_number": linked.number if linked else None,
        "resulting_content_hash": linked.content_hash if linked else None,
        "linked_by_user_id": linked.linked_by_user_id if linked else None,
        "linked_at": linked.linked_at if linked else None,
    }


class SqlAlchemyDecisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, decision: Decision) -> Decision:
        self._session.add(
            DecisionRecord(
                id=decision.id,
                project_id=decision.project_id,
                architecture_id=decision.architecture_id,
                number=decision.number,
                title=decision.title,
                context=decision.context,
                options=[o.to_dict() for o in decision.options],
                source=decision.source.to_dict() if decision.source else None,
                goals=list(decision.goals),
                evidence=[e.to_dict() for e in decision.evidence],
                assumptions=[a.to_dict() for a in decision.assumptions],
                related_element_ids=list(decision.related_element_ids),
                created_by_user_id=decision.created_by_user_id,
                created_at=decision.created_at,
                **_state(decision),
            )
        )
        await self._session.flush()
        return decision

    async def update(self, decision: Decision) -> Decision:
        record = await self._session.scalar(
            select(R).where(R.id == decision.id, R.project_id == decision.project_id).with_for_update()
        )
        if record is None:
            raise LookupError(decision.id)  # the service read it under the same lock: never expected
        for name, value in _state(decision).items():
            setattr(record, name, value)
        await self._session.flush()
        return decision

    async def get(self, project_id: uuid.UUID, decision_id: uuid.UUID) -> Decision | None:
        record = await self._session.scalar(select(R).where(R.id == decision_id, R.project_id == project_id))
        return to_decision(record) if record else None

    async def next_number(self, project_id: uuid.UUID) -> int:
        highest = await self._session.scalar(select(func.max(R.number)).where(R.project_id == project_id))
        return (highest or 0) + 1

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        architecture_id: uuid.UUID | None = None,
        status: DecisionStatus | None = None,
        after: int | None = None,
        limit: int = 50,
    ) -> list[Decision]:
        statement = select(R).where(R.project_id == project_id)
        if architecture_id is not None:
            statement = statement.where(R.architecture_id == architecture_id)
        if status is not None:
            statement = statement.where(R.status == status.value)
        if after is not None:
            statement = statement.where(R.number > after)
        records = await self._session.scalars(statement.order_by(R.number).limit(limit))
        return [to_decision(r) for r in records]
