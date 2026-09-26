"""Capacity analysis use cases: analyze an architecture revision under a workload (with optional
scenarios), read analyses, page through components and bottlenecks, and describe the models.

An analysis is synchronous, in three steps: read and authorize (a short transaction), calculate
(no transaction, no lock: the revision's content is immutable), then store, finished, in a
transaction that re-checks access and modifiability under the project lock and records the audit
entry. The engine is deterministic and pure; the analysis stores the request's inputs (the workload
snapshot, models, parameters, assumptions, entries) and the model set, so it stays explainable.

Access: analyzing needs ``architecture.analyze`` and a modifiable project and architecture (an
analysis is a write under them); reading needs ``architecture.read``. Every lookup goes
project -> architecture -> analysis. Requirements the workload cites must belong to the project.
Audit entries carry identifiers and counts only.
"""

import logging
import uuid
from collections.abc import Mapping
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
from core.domain.unit_of_work import UnitOfWork
from core.domain.validation.options import RevisionInfo

from .analyses import (
    PENDING,
    AnalysisError,
    AnalysisReport,
    AnalysisRequest,
    CapacityAnalysis,
    check_scenarios,
)
from .errors import CapacityAnalysisNotFound, InvalidWorkload
from .ports import CapacityEngine, EngineOutput
from .queries import (
    AnalysisQuery,
    BottleneckQuery,
    ComponentQuery,
    decode_analysis_cursor,
    decode_component_cursor,
    encode_analysis_cursor,
    encode_component_cursor,
)
from .results import Bottleneck, ComponentResult
from .scenarios import Scenario, ScenarioOutcome
from .workload import WorkloadAssumption, WorkloadProfile

log = logging.getLogger("architectos.capacity")

MAX_COMPONENT_PAGE = 500
ENGINE_ERROR = AnalysisError("engine_error", "The capacity engine could not complete this analysis.")


class CapacityService:
    def __init__(self, uow: UnitOfWork, engine: CapacityEngine, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock

    def models(self) -> tuple[Mapping[str, Any], ...]:
        return self._engine.models()

    async def analyze(  # noqa: PLR0913 -- keyword-only, one argument per part of the request
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        workload: WorkloadProfile,
        revision_number: int | None = None,
        models: tuple[str, ...] | None = None,
        parameters: Mapping[str, Mapping[str, Any]] | None = None,
        assumptions: tuple[WorkloadAssumption, ...] = (),
        entries: tuple[str, ...] | None = None,
        label: str | None = None,
        scenarios: tuple[Scenario, ...] = (),
    ) -> AnalysisReport:
        """Analyzes ``revision_number`` (default: the current revision) and stores the analysis.
        Invalid workloads, configurations and scenarios are refused (422) and nothing is stored."""
        check_scenarios(scenarios)
        # 1. Read and authorize (a short transaction): the revision's content is immutable, so the
        #    engine may work on it after the transaction ends.
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE)
            architecture = await _architecture(uow, project_id, architecture_id)
            architecture.ensure_modifiable()
            number = revision_number if revision_number is not None else architecture.current_revision
            revision = await uow.architectures.get_revision(architecture.id, number)
            if revision is None:
                raise ArchitectureRevisionNotFound
            await _check_requirements(uow, project_id, workload)
        request = AnalysisRequest(
            architecture.id,
            revision.number,
            workload,
            models,
            dict(parameters or {}),
            assumptions,
            label,
            entries,
        )
        info = RevisionInfo(
            str(architecture.id), revision.number, revision.content_hash, revision.ir_schema_version
        )
        now = self._clock()
        analysis = CapacityAnalysis(
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
        # 2. Calculate, holding no transaction and no lock (seconds on the largest architectures).
        output = self._run(analysis.id, revision.ir, info, request, scenarios)
        # 3. Store, re-authorized under the project lock: an archive or a lost permission in the
        #    meantime refuses the write, as if it had happened before the request.
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE, lock=ProjectLock.SHARE
            )
            (await _architecture(uow, project_id, architecture_id)).ensure_modifiable()
            if output is None:
                finished = analysis.fail(ENGINE_ERROR, self._clock())
                report = await uow.capacity.add(finished, request.inputs(), (), (), ())
            else:
                finished = analysis.finish(output.result, self._clock())
                report = await uow.capacity.add(
                    finished,
                    request.inputs(),
                    output.scaling,
                    output.unsupported_scaling,
                    tuple(ScenarioOutcome.of(s) for s in output.scenarios),
                )
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ARCHITECTURE_CAPACITY_ANALYZED,
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
        analysis_id: uuid.UUID,
        ir: Any,
        revision: RevisionInfo,
        request: AnalysisRequest,
        scenarios: tuple[Scenario, ...],
    ) -> EngineOutput | None:
        try:
            return self._engine.analyze(ir, revision, request, scenarios)
        except DomainError:
            raise  # a request the engine refuses: 4xx, nothing stored
        except Exception:  # an engine bug: recorded as a failed analysis, never a partial result
            log.exception("capacity engine failed", extra={"analysis_id": str(analysis_id)})
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
    ) -> pagination.Page[AnalysisReport]:
        size = pagination.page_size(limit)
        query = AnalysisQuery(revision, decode_analysis_cursor(cursor) if cursor else None, size + 1)
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            rows = await uow.capacity.list_for_architecture(project_id, architecture.id, query)
        items, more = rows[:size], len(rows) > size
        last = items[-1].analysis if more and items else None
        return pagination.Page(
            items=items, next_cursor=encode_analysis_cursor(last.requested_at, last.id) if last else None
        )

    async def get(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID
    ) -> AnalysisReport:
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
        query: ComponentQuery,
        cursor: str | None = None,
    ) -> pagination.Page[ComponentResult]:
        size = max(1, min(query.limit, MAX_COMPONENT_PAGE))
        after = decode_component_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            rows = await uow.capacity.list_components(
                project_id, report.analysis.id, ComponentQuery(query.status, after, size + 1)
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(
            items=items, next_cursor=encode_component_cursor(items[-1].node_id) if more else None
        )

    async def list_bottlenecks(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        query: BottleneckQuery,
    ) -> list[Bottleneck]:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            return await uow.capacity.list_bottlenecks(project_id, report.analysis.id, query)


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _analysis(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
) -> AnalysisReport:
    await _architecture(uow, project_id, architecture_id)  # a deleted architecture hides its analyses
    report = await uow.capacity.get(project_id, architecture_id, analysis_id)
    if report is None:
        raise CapacityAnalysisNotFound
    return report


async def _check_requirements(uow: UnitOfWork, project_id: uuid.UUID, workload: WorkloadProfile) -> None:
    """Requirements a workload cites must be live requirements of this project."""
    if not workload.requirement_ids:
        return
    found = {r.id for r in await uow.requirements.list_by_ids(project_id, list(workload.requirement_ids))}
    if set(workload.requirement_ids) - found:
        raise InvalidWorkload(details={"field": "requirement_ids", "reason": "unknown_requirement"})


def _facts(report: AnalysisReport) -> dict[str, object]:
    analysis, summary = report.analysis, report.summary
    facts: dict[str, object] = {
        "project_id": str(analysis.project_id),
        "analysis_id": str(analysis.id),
        "revision": analysis.revision_number,
        "status": analysis.status,
        "scenarios": len(report.scenarios),
    }
    if summary is not None:
        facts |= {
            "components": sum(summary.components.values()),
            "bottlenecks": sum(summary.bottlenecks.values()),
            "unsupported": summary.unsupported,
        }
    if analysis.error is not None:
        facts["error"] = analysis.error.code
    return facts
