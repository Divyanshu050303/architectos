"""Validation use cases: validate an architecture revision, read runs, page through findings, and
describe the rules.

A run is synchronous: the engine runs inside the request, and the run is stored once, completed
or failed, in the transaction that also records the audit entry (``pending`` and ``running`` exist
for a future background worker). The engine is deterministic and pure; everything it reads (the
revision, the project's requirements and policy) is loaded in the same transaction, and the run
records what it was given (``RunInputs``) so its result can be explained after they change.

Access: validating needs ``architecture.validate`` and a modifiable project and architecture (a
run is a write under them: archived ones are read-only); reading needs ``architecture.read``.
Every lookup goes project → architecture → run, so nothing of another tenant can be reached.
Audit entries carry identifiers and counts only, never findings or content.
"""

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain import pagination
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.architecture.versions import ArchitectureRevision
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.errors import DomainError
from core.domain.organizations.permissions import Permission
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.entities import Requirement
from core.domain.requirements.enums import RequirementStatus
from core.domain.unit_of_work import UnitOfWork

from .errors import ValidationRunNotFound
from .options import RevisionInfo, ValidationConfig
from .ports import ValidationEngine
from .queries import (
    FindingQuery,
    RunQuery,
    decode_finding_cursor,
    decode_run_cursor,
    encode_finding_cursor,
    encode_run_cursor,
)
from .results import Finding
from .runs import RunError, RunInputs, RunReport, RunStatus, ValidationRun

log = logging.getLogger("architectos.validation")

MAX_CONTEXT_REQUIREMENTS = 5000
MAX_FINDINGS_PAGE = 500
ENGINE_ERROR = RunError("engine_error", "The validation engine could not complete this run.")
TOO_MANY_REQUIREMENTS = RunError(
    "too_many_requirements",
    f"The project has more than {MAX_CONTEXT_REQUIREMENTS} requirements; too many to validate against.",
)


class ValidationService:
    def __init__(self, uow: UnitOfWork, engine: ValidationEngine, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock

    def rules(self) -> tuple[Mapping[str, Any], ...]:
        """The engine's rules; public knowledge, the same for every tenant."""
        return self._engine.rules()

    async def validate(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        config: ValidationConfig,
        revision_number: int | None = None,
    ) -> RunReport:
        """Validates ``revision_number`` (default: the current revision) and stores the run.
        InvalidValidationConfig when the configuration asks for what the engine does not offer
        (nothing is stored then)."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_VALIDATE, lock=ProjectLock.SHARE
            )
            architecture = await _architecture(uow, project_id, architecture_id)
            architecture.ensure_modifiable()
            number = revision_number if revision_number is not None else architecture.current_revision
            revision = await uow.architectures.get_revision(architecture.id, number)
            if revision is None:
                raise ArchitectureRevisionNotFound
            requirements = await uow.requirements.list_by_status(
                project_id, frozenset(RequirementStatus), limit=MAX_CONTEXT_REQUIREMENTS + 1
            )
            policy = access.project.policy
            now = self._clock()
            run = ValidationRun(
                id=uuid.uuid7(),
                project_id=project_id,
                architecture_id=architecture.id,
                revision_number=revision.number,
                revision_content_hash=revision.content_hash,
                profile=config.profile,
                status=RunStatus.PENDING,
                requested_by_user_id=user_id,
                requested_at=now,
            ).start(now)
            inputs = RunInputs(
                config=config.to_dict(),
                policy=None if policy.is_empty else policy.to_dict(),
                requirements=tuple(
                    (str(r.id), r.version, r.content.status.value)
                    for r in sorted(requirements, key=lambda r: r.number)[:MAX_CONTEXT_REQUIREMENTS]
                ),
            )
            if len(requirements) > MAX_CONTEXT_REQUIREMENTS:
                finished = run.fail(TOO_MANY_REQUIREMENTS, self._clock())
            else:
                finished = self._run(
                    run, revision.ir, revision_info(revision, architecture), requirements, policy, config
                )
            report = await uow.validations.add(finished, inputs)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ARCHITECTURE_VALIDATED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="architecture",
                    resource_id=architecture.id,
                    metadata=_facts(report),
                )
            )
        return report

    def _run(
        self,
        run: ValidationRun,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        requirements: list[Requirement],
        policy: ArchitecturePolicy,
        config: ValidationConfig,
    ) -> ValidationRun:
        try:
            result = self._engine.validate(
                ir,
                revision,
                requirements=tuple(requirements),
                policy=None if policy.is_empty else policy,
                config=config,
            )
        except DomainError:
            raise  # a request the engine refuses (e.g. an unknown rule): 4xx, nothing stored
        except Exception:  # an engine bug: recorded as a failed run, never a partial result
            log.exception("validation engine failed", extra={"run_id": str(run.id)})
            return run.fail(ENGINE_ERROR, self._clock())
        return run.complete(result, self._clock())

    # --- reading ---------------------------------------------------------------------------------

    async def list_runs(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        revision: int | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[RunReport]:
        size = pagination.page_size(limit)
        query = RunQuery(revision, decode_run_cursor(cursor) if cursor else None, size + 1)
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            rows = await uow.validations.list_for_architecture(project_id, architecture.id, query)
        items, more = rows[:size], len(rows) > size
        last = items[-1].run if more and items else None
        return pagination.Page(
            items=items, next_cursor=encode_run_cursor(last.requested_at, last.id) if last else None
        )

    async def get_run(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID
    ) -> RunReport:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _run_of(uow, project_id, architecture_id, run_id)

    async def list_findings(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        query: FindingQuery,
        cursor: str | None = None,
    ) -> pagination.Page[Finding]:
        size = max(1, min(query.limit, MAX_FINDINGS_PAGE))
        after = decode_finding_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _run_of(uow, project_id, architecture_id, run_id)
            rows = await uow.validations.list_findings(
                report.run.id,
                FindingQuery(
                    query.severity,
                    query.category,
                    query.blocking,
                    query.rule_id,
                    query.entity_id,
                    after,
                    size + 1,
                ),
            )
        items, more = rows[:size], len(rows) > size
        next_cursor = encode_finding_cursor(items[-1][0]) if more and items else None
        return pagination.Page(items=[f for _, f in items], next_cursor=next_cursor)


def revision_info(revision: ArchitectureRevision, architecture: Architecture) -> RevisionInfo:
    return RevisionInfo(
        architecture_id=str(architecture.id),
        number=revision.number,
        content_hash=revision.content_hash,
        schema_version=revision.ir_schema_version,
    )


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _run_of(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, run_id: uuid.UUID
) -> RunReport:
    await _architecture(uow, project_id, architecture_id)  # a deleted architecture hides its runs
    report = await uow.validations.get(project_id, architecture_id, run_id)
    if report is None:
        raise ValidationRunNotFound
    return report


def _facts(report: RunReport) -> dict[str, object]:
    run, summary = report.run, report.summary
    facts: dict[str, object] = {
        "project_id": str(run.project_id),
        "run_id": str(run.id),
        "revision": run.revision_number,
        "profile": run.profile,
        "status": run.status.value,
    }
    if summary is not None:
        facts |= {
            "findings": summary.total,
            "blocking": summary.blocking,
            "rule_failures": summary.rule_failures,
        }
    if run.error is not None:
        facts["error"] = run.error.code
    return facts
