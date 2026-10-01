"""Reading the other engines' stored analyses for a migration plan, through each engine's repository
and the evolution contract's adapters (``core.domain.evolution.evidence``) — never recomputed.

For each engine: the latest usable (not failed) analysis of the plan's source revision and of its
target revision; when neither revision has one, the latest usable analysis of any revision of the
architecture, which the plan reports as stale and never uses. Every lookup is scoped by project and
architecture, and bounded (a few recent analyses per revision, findings page by page).
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from core.domain.capacity.queries import AnalysisQuery, BottleneckQuery
from core.domain.cost.queries import CostAnalysisQuery
from core.domain.evolution.evidence import (
    from_capacity,
    from_cost,
    from_observability,
    from_reliability,
    from_security,
    from_validation,
)
from core.domain.observability.queries import ObservabilityAnalysisQuery, ObservabilityFindingQuery
from core.domain.reliability.queries import ReliabilityAnalysisQuery, ReliabilityFindingQuery
from core.domain.security.queries import SecurityAnalysisQuery, SecurityFindingQuery
from core.domain.unit_of_work import UnitOfWork
from core.domain.validation.queries import FindingQuery, RunQuery

from .evidence import AnalysisEvidence

LOOKBACK = 10  # the latest usable analysis among the newest few
FINDINGS_PAGE = 500
type Loader = Callable[[UnitOfWork, uuid.UUID, uuid.UUID, int | None], Awaitable[AnalysisEvidence | None]]


async def _all[T](fetch: Callable[[int | None], Awaitable[list[tuple[int, T]]]]) -> list[T]:
    """Every row of a positioned listing, page by page."""
    found: list[T] = []
    after: int | None = None
    while True:
        page = await fetch(after)
        found += [row for _, row in page]
        if len(page) < FINDINGS_PAGE:
            return found
        after = page[-1][0]


def _usable[R](reports: list[R], status: Callable[[R], str]) -> R | None:
    return next((r for r in reports if status(r) != "failed"), None)


async def _capacity(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, revision: int | None
) -> AnalysisEvidence | None:
    repo = uow.capacity
    reports = await repo.list_for_architecture(
        project_id, architecture_id, AnalysisQuery(revision=revision, limit=LOOKBACK)
    )
    report = _usable(reports, lambda r: r.analysis.status)
    if report is None:
        return None
    bottlenecks = await repo.list_bottlenecks(project_id, report.analysis.id, BottleneckQuery())
    return AnalysisEvidence(from_capacity(report, bottlenecks), report.inputs)


async def _cost(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, revision: int | None
) -> AnalysisEvidence | None:
    reports = await uow.cost.list_for_architecture(
        project_id, architecture_id, CostAnalysisQuery(revision=revision, limit=LOOKBACK)
    )
    report = _usable(reports, lambda r: r.analysis.status)
    return AnalysisEvidence(from_cost(report), report.inputs) if report else None


async def _reliability(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, revision: int | None
) -> AnalysisEvidence | None:
    repo = uow.reliability
    reports = await repo.list_for_architecture(
        project_id, architecture_id, ReliabilityAnalysisQuery(revision=revision, limit=LOOKBACK)
    )
    report = _usable(reports, lambda r: r.analysis.status)
    if report is None:
        return None
    analysis_id = report.analysis.id
    findings = await _all(
        lambda after: repo.list_findings(
            project_id, analysis_id, ReliabilityFindingQuery(after=after, limit=FINDINGS_PAGE)
        )
    )
    return AnalysisEvidence(from_reliability(report, findings), report.inputs)


async def _security(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, revision: int | None
) -> AnalysisEvidence | None:
    repo = uow.security
    reports = await repo.list_for_architecture(
        project_id, architecture_id, SecurityAnalysisQuery(revision=revision, limit=LOOKBACK)
    )
    report = _usable(reports, lambda r: r.analysis.status)
    if report is None:
        return None
    analysis_id = report.analysis.id
    findings = await _all(
        lambda after: repo.list_findings(
            project_id, analysis_id, SecurityFindingQuery(after=after, limit=FINDINGS_PAGE)
        )
    )
    return AnalysisEvidence(from_security(report, findings), report.inputs)


async def _observability(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, revision: int | None
) -> AnalysisEvidence | None:
    repo = uow.observability
    reports = await repo.list_for_architecture(
        project_id, architecture_id, ObservabilityAnalysisQuery(revision=revision, limit=LOOKBACK)
    )
    report = _usable(reports, lambda r: r.analysis.status)
    if report is None:
        return None
    analysis_id = report.analysis.id
    findings = await _all(
        lambda after: repo.list_findings(
            project_id, analysis_id, ObservabilityFindingQuery(after=after, limit=FINDINGS_PAGE)
        )
    )
    return AnalysisEvidence(from_observability(report, findings), report.inputs)


async def _validation(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, revision: int | None
) -> AnalysisEvidence | None:
    repo = uow.validations
    reports = await repo.list_for_architecture(
        project_id, architecture_id, RunQuery(revision=revision, limit=LOOKBACK)
    )
    report = _usable(reports, lambda r: r.run.status.value)
    if report is None:
        return None
    run_id = report.run.id
    findings = await _all(
        lambda after: repo.list_findings(project_id, run_id, FindingQuery(after=after, limit=FINDINGS_PAGE))
    )
    summary: dict[str, Any] = report.summary.to_dict() if report.summary else {}
    return AnalysisEvidence(from_validation(report, findings), summary=summary)


LOADERS: tuple[Loader, ...] = (_validation, _capacity, _cost, _reliability, _security, _observability)


async def load_evidence(
    uow: UnitOfWork,
    project_id: uuid.UUID,
    architecture_id: uuid.UUID,
    revisions: tuple[int, ...],
) -> tuple[AnalysisEvidence, ...]:
    """For each engine, the latest usable analysis of each of ``revisions`` — or, when none of them
    has one, the latest usable analysis of any revision (reported as stale by the plan)."""
    found: list[AnalysisEvidence] = []
    for loader in LOADERS:
        current = [
            a for r in dict.fromkeys(revisions) if (a := await loader(uow, project_id, architecture_id, r))
        ]
        if not current:
            latest = await loader(uow, project_id, architecture_id, None)
            current = [latest] if latest is not None else []
        found += current
    return tuple(found)
