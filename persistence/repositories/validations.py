"""Validation runs and their findings (append-only). Every read is scoped by project and
architecture; findings are read only for a run already found that way."""

import uuid
from typing import Any

from sqlalchemy import insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.validation.queries import FindingQuery, RunQuery
from core.domain.validation.results import (
    Finding,
    Limitation,
    RequirementResult,
    RuleFailure,
    RuleSet,
    Summary,
)
from core.domain.validation.runs import RunError, RunInputs, RunReport, RunStatus, ValidationRun
from persistence.models import ValidationFindingRecord, ValidationRunRecord

# Findings are inserted in batches: one statement per batch, bounded parameters per statement.
_BATCH = 500


def _summary(data: dict[str, Any] | None) -> Summary | None:
    if data is None:
        return None
    return Summary(
        total=data["total"],
        by_severity=data["by_severity"],
        by_category=data["by_category"],
        blocking=data["blocking"],
        requirements=data["requirements"],
        rule_failures=data["rule_failures"],
    )


def _rule_set(data: dict[str, Any] | None) -> RuleSet | None:
    if data is None:
        return None
    return RuleSet(data["id"], data["version"], tuple((r[0], r[1]) for r in data["rules"]))


def _inputs(data: dict[str, Any]) -> RunInputs:
    return RunInputs(
        config=data.get("config", {}),
        policy=data.get("policy"),
        requirements=tuple((r[0], r[1], r[2]) for r in data.get("requirements", ())),
    )


def to_report(record: ValidationRunRecord) -> RunReport:
    error = RunError(record.error_code, record.error_message or "") if record.error_code else None
    run = ValidationRun(
        id=record.id,
        project_id=record.project_id,
        architecture_id=record.architecture_id,
        revision_number=record.revision_number,
        revision_content_hash=record.revision_content_hash,
        profile=record.profile,
        status=RunStatus(record.status),
        requested_by_user_id=record.requested_by_user_id,
        requested_at=record.requested_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error=error,
    )
    return RunReport(
        run=run,
        inputs=_inputs(record.inputs),
        rule_set=_rule_set(record.rule_set),
        context_fingerprint=record.context_fingerprint,
        result_fingerprint=record.result_fingerprint,
        summary=_summary(record.summary),
        requirement_results=tuple(RequirementResult.from_dict(r) for r in record.requirement_results),
        failures=tuple(RuleFailure.from_dict(f) for f in record.failures),
        limitations=tuple(Limitation.from_dict(x) for x in record.limitations),
    )


class SqlAlchemyValidationRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: ValidationRun, inputs: RunInputs) -> RunReport:
        report = RunReport.of(run, inputs)
        record = ValidationRunRecord(
            id=run.id,
            project_id=run.project_id,
            architecture_id=run.architecture_id,
            revision_number=run.revision_number,
            revision_content_hash=run.revision_content_hash,
            profile=run.profile,
            status=run.status.value,
            requested_by_user_id=run.requested_by_user_id,
            requested_at=run.requested_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            rule_set=report.rule_set.to_dict() if report.rule_set else None,
            context_fingerprint=report.context_fingerprint,
            result_fingerprint=report.result_fingerprint,
            summary=report.summary.to_dict() if report.summary else None,
            requirement_results=[r.to_dict() for r in report.requirement_results],
            failures=[f.to_dict() for f in report.failures],
            limitations=[x.to_dict() for x in report.limitations],
            inputs=inputs.to_dict(),
            error_code=run.error.code if run.error else None,
            error_message=run.error.message if run.error else None,
        )
        self._session.add(record)
        await self._session.flush()
        findings = run.result.findings if run.result is not None else ()
        rows = [
            {
                "id": uuid.uuid7(),
                "run_id": run.id,
                "project_id": run.project_id,
                "position": position,
                "finding_id": finding.id,
                "rule_id": finding.rule_id,
                "severity": finding.severity.value,
                "category": finding.category.value,
                "blocking": finding.blocking,
                "entity_ids": list(finding.entity_ids),
                "data": finding.to_dict(),
            }
            for position, finding in enumerate(findings)
        ]
        for start in range(0, len(rows), _BATCH):
            await self._session.execute(insert(ValidationFindingRecord), rows[start : start + _BATCH])
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, run_id: uuid.UUID
    ) -> RunReport | None:
        record = await self._session.scalar(
            select(ValidationRunRecord).where(
                ValidationRunRecord.id == run_id,
                ValidationRunRecord.project_id == project_id,
                ValidationRunRecord.architecture_id == architecture_id,
            )
        )
        return to_report(record) if record else None

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: RunQuery
    ) -> list[RunReport]:
        statement = select(ValidationRunRecord).where(
            ValidationRunRecord.project_id == project_id,
            ValidationRunRecord.architecture_id == architecture_id,
        )
        if query.revision is not None:
            statement = statement.where(ValidationRunRecord.revision_number == query.revision)
        if query.after is not None:
            requested_at, run_id = query.after
            statement = statement.where(
                tuple_(ValidationRunRecord.requested_at, ValidationRunRecord.id)
                < tuple_(literal(requested_at), literal(run_id))
            )
        ordered = statement.order_by(ValidationRunRecord.requested_at.desc(), ValidationRunRecord.id.desc())
        records = await self._session.scalars(ordered.limit(query.limit))
        return [to_report(r) for r in records]

    async def list_findings(
        self, project_id: uuid.UUID, run_id: uuid.UUID, query: FindingQuery
    ) -> list[tuple[int, Finding]]:
        statement = select(ValidationFindingRecord.position, ValidationFindingRecord.data).where(
            ValidationFindingRecord.run_id == run_id, ValidationFindingRecord.project_id == project_id
        )
        if query.severity is not None:
            statement = statement.where(ValidationFindingRecord.severity == query.severity.value)
        if query.category is not None:
            statement = statement.where(ValidationFindingRecord.category == query.category.value)
        if query.blocking is not None:
            statement = statement.where(ValidationFindingRecord.blocking.is_(query.blocking))
        if query.rule_id is not None:
            statement = statement.where(ValidationFindingRecord.rule_id == query.rule_id)
        if query.entity_id is not None:
            statement = statement.where(ValidationFindingRecord.entity_ids.contains([query.entity_id]))
        if query.after is not None:
            statement = statement.where(ValidationFindingRecord.position > query.after)
        rows = await self._session.execute(
            statement.order_by(ValidationFindingRecord.position).limit(query.limit)
        )
        return [(position, Finding.from_dict(data)) for position, data in rows]
