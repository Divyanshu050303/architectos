"""Reliability analysis use cases: analyze an architecture revision (entries, objectives,
assumptions), read analyses, page through components and findings, and describe the models.

An analysis is synchronous, in three steps, like capacity and cost analyses: read and authorize (a
short transaction: the revision and the project's in-force requirements), calculate (on a worker
thread, no transaction, no lock: every input is immutable), then store, finished, in a transaction
that re-checks access and modifiability under the project lock and records the audit entry. The
requirements read are recorded with the analysis (id, version, status), so it stays explainable.

Access: analyzing needs ``architecture.analyze`` and a modifiable project and architecture; reading
needs ``architecture.read``. Every lookup goes project -> architecture -> analysis. Audit entries
carry identifiers and counts only.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from core.domain import pagination
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.errors import DomainError
from core.domain.organizations.permissions import Permission
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.requirements import IN_FORCE
from core.domain.unit_of_work import UnitOfWork
from core.domain.validation.options import RevisionInfo

from .analyses import (
    PENDING,
    Objective,
    ReliabilityAnalysis,
    ReliabilityAnalysisError,
    ReliabilityAnalysisRequest,
    ReliabilityAssumption,
)
from .errors import ReliabilityAnalysisNotFound
from .ports import ReliabilityEngine
from .queries import (
    ReliabilityAnalysisQuery,
    ReliabilityComponentQuery,
    ReliabilityFindingQuery,
    decode_analysis_cursor,
    decode_component_cursor,
    decode_finding_cursor,
    encode_analysis_cursor,
    encode_component_cursor,
    encode_finding_cursor,
)
from .reports import ReliabilityReport
from .results import ComponentResult, ReliabilityFinding, ReliabilityResult

log = logging.getLogger("architectos.reliability")

MAX_REQUIREMENTS = 5000
MAX_PAGE = 500
ENGINE_ERROR = ReliabilityAnalysisError(
    "engine_error", "The reliability engine could not complete this analysis."
)
TOO_MANY_REQUIREMENTS = ReliabilityAnalysisError(
    "too_many_requirements",
    f"The project has more than {MAX_REQUIREMENTS} requirements in force; too many to analyze against.",
)


class ReliabilityService:
    def __init__(self, uow: UnitOfWork, engine: ReliabilityEngine, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock

    def models(self) -> tuple[Mapping[str, Any], ...]:
        return self._engine.models()

    async def analyze(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        revision_number: int | None = None,
        entries: tuple[str, ...] | None = None,
        objectives: tuple[Objective, ...] = (),
        assumptions: tuple[ReliabilityAssumption, ...] = (),
        label: str | None = None,
    ) -> ReliabilityReport:
        """Analyzes ``revision_number`` (default: the current revision) and stores the analysis.
        Invalid requests (entries or objectives naming nodes the revision lacks) are refused (422)
        and nothing is stored."""
        # 1. Read and authorize (a short transaction).
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE)
            architecture = await _architecture(uow, project_id, architecture_id)
            architecture.ensure_modifiable()
            number = revision_number if revision_number is not None else architecture.current_revision
            revision = await uow.architectures.get_revision(architecture.id, number)
            if revision is None:
                raise ArchitectureRevisionNotFound
            requirements = await uow.requirements.list_by_status(
                project_id, IN_FORCE, limit=MAX_REQUIREMENTS + 1
            )
        request = ReliabilityAnalysisRequest(
            architecture.id, revision.number, entries, objectives, assumptions, label
        )
        info = RevisionInfo(
            str(architecture.id), revision.number, revision.content_hash, revision.ir_schema_version
        )
        ordered = tuple(sorted(requirements, key=lambda r: r.number)[:MAX_REQUIREMENTS])
        inputs = request.inputs() | {
            "requirements": [[str(r.id), r.version, r.content.status.value] for r in ordered]
        }
        now = self._clock()
        analysis = ReliabilityAnalysis(
            id=uuid.uuid7(),
            project_id=project_id,
            architecture_id=architecture.id,
            revision_number=revision.number,
            revision_content_hash=revision.content_hash,
            status=PENDING,
            requested_by_user_id=user_id,
            requested_at=now,
            label=label,
        ).start(now)
        # 2. Calculate on a worker thread, holding no transaction and no lock.
        result: ReliabilityResult | None = None
        error: ReliabilityAnalysisError | None = None
        if len(requirements) > MAX_REQUIREMENTS:
            error = TOO_MANY_REQUIREMENTS
        else:
            result = await self._run(
                analysis.id, lambda: self._engine.analyze(revision.ir, info, request, ordered)
            )
            error = ENGINE_ERROR if result is None else None
        # 3. Store, re-authorized under the project lock.
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE, lock=ProjectLock.SHARE
            )
            (await _architecture(uow, project_id, architecture_id)).ensure_modifiable()
            if result is None:
                assert error is not None  # noqa: S101 -- set with a None result
                report = ReliabilityReport.of(analysis.fail(error, self._clock()), inputs)
                components: tuple[ComponentResult, ...] = ()
                findings: tuple[ReliabilityFinding, ...] = ()
            else:
                report = ReliabilityReport.of(analysis.finish(result, self._clock()), inputs)
                components, findings = result.components, result.findings
            report = await uow.reliability.add(report, components, findings)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ARCHITECTURE_RELIABILITY_ANALYZED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="architecture",
                    resource_id=architecture.id,
                    metadata=_facts(report, len(components), len(findings)),
                )
            )
        return report

    async def _run(
        self, analysis_id: uuid.UUID, run: Callable[[], ReliabilityResult]
    ) -> ReliabilityResult | None:
        try:
            return await asyncio.to_thread(run)
        except DomainError:
            raise  # a request the engine refuses: 4xx, nothing stored
        except Exception:  # an engine bug: recorded as a failed analysis, never a partial result
            log.exception("reliability engine failed", extra={"analysis_id": str(analysis_id)})
            return None

    # --- reading ---------------------------------------------------------------------------------

    async def list_analyses(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        revision: int | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[ReliabilityReport]:
        size = pagination.page_size(limit)
        query = ReliabilityAnalysisQuery(
            revision, decode_analysis_cursor(cursor) if cursor else None, size + 1
        )
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            rows = await uow.reliability.list_for_architecture(project_id, architecture.id, query)
        items, more = rows[:size], len(rows) > size
        last = items[-1].analysis if more and items else None
        return pagination.Page(
            items=items, next_cursor=encode_analysis_cursor(last.requested_at, last.id) if last else None
        )

    async def get(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID
    ) -> ReliabilityReport:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _analysis(uow, project_id, architecture_id, analysis_id)

    async def list_components(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        query: ReliabilityComponentQuery,
        cursor: str | None = None,
    ) -> pagination.Page[ComponentResult]:
        size = max(1, min(query.limit, MAX_PAGE))
        after = decode_component_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            rows = await uow.reliability.list_components(
                project_id, report.analysis.id, ReliabilityComponentQuery(query.status, after, size + 1)
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(
            items=items, next_cursor=encode_component_cursor(items[-1].node_id) if more else None
        )

    async def list_findings(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        query: ReliabilityFindingQuery,
        cursor: str | None = None,
    ) -> pagination.Page[ReliabilityFinding]:
        size = max(1, min(query.limit, MAX_PAGE))
        after = decode_finding_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            rows = await uow.reliability.list_findings(
                project_id,
                report.analysis.id,
                ReliabilityFindingQuery(query.severity, query.type, query.certainty, after, size + 1),
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(
            items=[f for _, f in items], next_cursor=encode_finding_cursor(items[-1][0]) if more else None
        )


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _analysis(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
) -> ReliabilityReport:
    await _architecture(uow, project_id, architecture_id)  # a deleted architecture hides its analyses
    report = await uow.reliability.get(project_id, architecture_id, analysis_id)
    if report is None:
        raise ReliabilityAnalysisNotFound
    return report


def _facts(report: ReliabilityReport, components: int, findings: int) -> dict[str, object]:
    analysis = report.analysis
    facts: dict[str, object] = {
        "project_id": str(analysis.project_id),
        "analysis_id": str(analysis.id),
        "revision": analysis.revision_number,
        "status": analysis.status,
        "components": components,
        "findings": findings,
        "objectives": len(report.objectives),
    }
    if analysis.error is not None:
        facts["error"] = analysis.error.code
    return facts
