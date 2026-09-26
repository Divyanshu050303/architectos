"""Cost analysis use cases: price an architecture revision with a pricing snapshot (optionally with
the usage of a stored capacity analysis, and scenarios), read analyses, and page through line items.

An analysis is synchronous, in three steps, like capacity analyses: read and authorize (a short
transaction: the revision, the organization's pricing snapshot, the cited capacity analysis and its
components), calculate (no transaction, no lock: every input is immutable), then store, finished,
in a transaction that re-checks access and modifiability under the project lock and records the
audit entry.

The provider is the project's cloud provider; the currency defaults to the project's; the pricing
date defaults to today (UTC); operating hours to 730 a month. What was used is stored with the
analysis (its inputs), so it can be read and reproduced.

Access: analyzing needs ``architecture.analyze`` and a modifiable project and architecture; reading
needs ``architecture.read``. Lookups go project -> architecture -> analysis; the pricing snapshot is
looked up in the project's organization only, the capacity analysis in the same architecture only
(another one is indistinguishable from a missing one). Audit entries carry identifiers and counts.
"""

import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from core.domain import pagination
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.capacity.errors import CapacityAnalysisNotFound
from core.domain.capacity.queries import ComponentQuery
from core.domain.capacity.results import ComponentResult
from core.domain.clock import Clock, utc_now
from core.domain.errors import DomainError
from core.domain.organizations.permissions import Permission
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.unit_of_work import UnitOfWork
from core.domain.validation.options import RevisionInfo

from .analyses import PENDING, CostAnalysis, CostAnalysisError, CostAnalysisRequest, CostAssumption
from .capacity import CapacityBasis
from .errors import CostAnalysisNotFound, InvalidCostRequest, PricingSnapshotNotFound
from .money import HOURS_PER_MONTH
from .ports import CostEngine, CostEngineOutput
from .projection import MAX_COST_SCENARIOS, CostScenario
from .queries import (
    CostAnalysisQuery,
    LineItemQuery,
    decode_cost_analysis_cursor,
    decode_line_item_cursor,
    encode_cost_analysis_cursor,
    encode_line_item_cursor,
)
from .reports import CostReport
from .results import LineItem

log = logging.getLogger("architectos.cost")

MAX_LINE_ITEM_PAGE = 500
CAPACITY_PAGE = 500
ENGINE_ERROR = CostAnalysisError("engine_error", "The cost engine could not complete this analysis.")


def check_scenarios(scenarios: tuple[CostScenario, ...]) -> None:
    if len(scenarios) > MAX_COST_SCENARIOS:
        raise InvalidCostRequest(details={"field": "scenarios", "reason": "too_many"})
    names = [s.name for s in scenarios]
    if len(names) != len(set(names)):
        raise InvalidCostRequest(details={"field": "scenarios", "reason": "duplicate_name"})


class CostService:
    def __init__(self, uow: UnitOfWork, engine: CostEngine, *, clock: Clock = utc_now) -> None:
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
        snapshot_id: uuid.UUID,
        revision_number: int | None = None,
        currency: str | None = None,
        pricing_date: date | None = None,
        operating_hours_per_month: Decimal | None = None,
        capacity_analysis_id: uuid.UUID | None = None,
        replicas_from_capacity: bool = False,
        assumptions: tuple[CostAssumption, ...] = (),
        label: str | None = None,
        scenarios: tuple[CostScenario, ...] = (),
    ) -> CostReport:
        """Prices ``revision_number`` (default: the current revision) and stores the analysis.
        Invalid requests, mismatched capacity analyses and scenarios are refused (4xx) and nothing
        is stored."""
        check_scenarios(scenarios)
        # 1. Read and authorize (a short transaction): every input is immutable once read.
        async with self._uow as uow:
            access = await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE)
            architecture = await _architecture(uow, project_id, architecture_id)
            architecture.ensure_modifiable()
            number = revision_number if revision_number is not None else architecture.current_revision
            revision = await uow.architectures.get_revision(architecture.id, number)
            if revision is None:
                raise ArchitectureRevisionNotFound
            organization_id = access.project.organization_id
            snapshot = await uow.pricing.get(organization_id, snapshot_id)
            if snapshot is None:
                raise PricingSnapshotNotFound
            basis = await _capacity(uow, project_id, architecture.id, capacity_analysis_id)
            settings = access.project.settings
        request = CostAnalysisRequest(
            architecture.id,
            revision.number,
            snapshot.id,
            currency or settings.currency,
            pricing_date or self._clock().date(),
            operating_hours_per_month if operating_hours_per_month is not None else HOURS_PER_MONTH,
            capacity_analysis_id,
            replicas_from_capacity,
            assumptions,
            label,
        )
        if basis is not None:
            basis.check(request, revision.content_hash)
        provider = settings.cloud_provider.value if settings.cloud_provider else None
        info = RevisionInfo(
            str(architecture.id), revision.number, revision.content_hash, revision.ir_schema_version
        )
        inputs = request.inputs() | {"provider": provider, "scenarios": [s.to_dict() for s in scenarios]}
        now = self._clock()
        analysis = CostAnalysis(
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
        # 2. Calculate, holding no transaction and no lock.
        output = self._run(analysis.id, lambda: self._engine.analyze(
            revision.ir, info, request, snapshot, provider, basis, scenarios
        ))  # fmt: skip
        # 3. Store, re-authorized under the project lock.
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE, lock=ProjectLock.SHARE
            )
            (await _architecture(uow, project_id, architecture_id)).ensure_modifiable()
            if output is None:
                report = CostReport.of(analysis.fail(ENGINE_ERROR, self._clock()), inputs, organization_id)
                lines: tuple[LineItem, ...] = ()
            else:
                finished = analysis.finish(output.result, self._clock())
                report = CostReport.of(
                    finished, inputs, organization_id, output.summary, output.assumptions, output.projections
                )
                lines = output.result.line_items
            report = await uow.cost.add(report, lines)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ARCHITECTURE_COST_ANALYZED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="architecture",
                    resource_id=architecture.id,
                    metadata=_facts(report, len(lines)),
                )
            )
        return report

    def _run(self, analysis_id: uuid.UUID, run: Callable[[], CostEngineOutput]) -> CostEngineOutput | None:
        try:
            output = run()
        except DomainError:
            raise  # a request the engine refuses: 4xx, nothing stored
        except Exception:  # an engine bug: recorded as a failed analysis, never a partial result
            log.exception("cost engine failed", extra={"analysis_id": str(analysis_id)})
            return None
        return output

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
    ) -> pagination.Page[CostReport]:
        size = pagination.page_size(limit)
        query = CostAnalysisQuery(revision, decode_cost_analysis_cursor(cursor) if cursor else None, size + 1)
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            rows = await uow.cost.list_for_architecture(project_id, architecture.id, query)
        items, more = rows[:size], len(rows) > size
        last = items[-1].analysis if more and items else None
        return pagination.Page(
            items=items, next_cursor=encode_cost_analysis_cursor(last.requested_at, last.id) if last else None
        )

    async def get(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID
    ) -> CostReport:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _analysis(uow, project_id, architecture_id, analysis_id)

    async def list_line_items(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        query: LineItemQuery,
        cursor: str | None = None,
    ) -> pagination.Page[LineItem]:
        size = max(1, min(query.limit, MAX_LINE_ITEM_PAGE))
        after = decode_line_item_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            rows = await uow.cost.list_line_items(
                project_id,
                report.analysis.id,
                LineItemQuery(query.element_id, query.status, query.category, after, size + 1),
            )
        items, more = rows[:size], len(rows) > size
        next_cursor = encode_line_item_cursor(items[-1].element_id, items[-1].resource) if more else None
        return pagination.Page(items=items, next_cursor=next_cursor)


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _capacity(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID | None
) -> CapacityBasis | None:
    """The cited capacity analysis of this architecture, with all its components."""
    if analysis_id is None:
        return None
    report = await uow.capacity.get(project_id, architecture_id, analysis_id)
    if report is None:
        raise CapacityAnalysisNotFound
    components: list[ComponentResult] = []
    after: str | None = None
    while True:
        page = await uow.capacity.list_components(
            project_id, analysis_id, ComponentQuery(None, after, CAPACITY_PAGE)
        )
        components += page
        if len(page) < CAPACITY_PAGE:
            break
        after = page[-1].node_id
    return CapacityBasis.of(report, components)


async def _analysis(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
) -> CostReport:
    await _architecture(uow, project_id, architecture_id)  # a deleted architecture hides its analyses
    report = await uow.cost.get(project_id, architecture_id, analysis_id)
    if report is None:
        raise CostAnalysisNotFound
    return report


def _facts(report: CostReport, line_items: int) -> dict[str, object]:
    analysis = report.analysis
    facts: dict[str, object] = {
        "project_id": str(analysis.project_id),
        "analysis_id": str(analysis.id),
        "revision": analysis.revision_number,
        "status": analysis.status,
        "snapshot_id": str(report.snapshot_id),
        "line_items": line_items,
        "scenarios": len(report.scenarios),
    }
    if report.totals is not None:
        facts |= {"unknown_items": report.totals.unknown_items, "unsupported": report.totals.unsupported}
    if analysis.error is not None:
        facts["error"] = analysis.error.code
    return facts
