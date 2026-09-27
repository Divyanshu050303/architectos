"""Evolution use cases: run an evolution analysis against an architecture revision, read analyses, page
through their candidates, read a candidate with its proposed diff, and describe what is supported.

An analysis is synchronous, in three steps, like the other analyses: read and authorize (a short
transaction: the revision, the project's in-force requirements, policy, provider and currency, and
the evidence — for each engine, the stored analysis the request cites or the latest usable one, with
all its findings; the current capacity analysis's workload and the current cost analysis's pricing
snapshot, reused for the candidates' impacts), calculate (on a worker thread, no transaction, no
lock: every input is immutable), then store, finished, in a transaction that re-checks access and
modifiability under the project lock and records the audit entry. The architecture is never changed.

Access: running needs ``architecture.evolve`` and a modifiable project and architecture; reading needs
``architecture.read``. Every lookup goes project -> architecture -> analysis -> candidate; cited
analyses must belong to the same architecture, and the pricing snapshot to the project's organization.
Audit entries carry identifiers and counts only; an engine failure is logged by the error's type.
"""

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from core.domain import pagination
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.capacity.queries import AnalysisQuery, BottleneckQuery
from core.domain.capacity.workload import WorkloadProfile
from core.domain.clock import Clock, utc_now
from core.domain.cost.queries import CostAnalysisQuery
from core.domain.engine_results import Evidence
from core.domain.errors import DomainError
from core.domain.observability.queries import ObservabilityAnalysisQuery, ObservabilityFindingQuery
from core.domain.organizations.permissions import Permission
from core.domain.projects.repository import ProjectLock
from core.domain.reliability.queries import ReliabilityAnalysisQuery, ReliabilityFindingQuery
from core.domain.requirements.access import project_access
from core.domain.requirements.entities import Requirement
from core.domain.requirements.requirements import IN_FORCE
from core.domain.security.queries import SecurityAnalysisQuery, SecurityFindingQuery
from core.domain.simulations.entities import PricingInputs
from core.domain.unit_of_work import UnitOfWork
from core.domain.validation.options import RevisionInfo
from core.domain.validation.queries import FindingQuery, RunQuery

from .candidates import Candidate
from .entities import (
    CITABLE,
    PENDING,
    EvidenceCitation,
    EvolutionAnalysis,
    EvolutionConstraints,
    EvolutionError,
    EvolutionRequest,
)
from .errors import CandidateNotFound, EvolutionAnalysisNotFound, InvalidCandidate, InvalidEvolutionRequest
from .evidence import (
    StoredAnalysis,
    from_capacity,
    from_cost,
    from_observability,
    from_reliability,
    from_security,
    from_validation,
)
from .goals import EvolutionGoal
from .overlays import apply_candidate
from .ports import EvolutionEngine, ImpactInputs
from .queries import (
    CandidateQuery,
    EvolutionQuery,
    decode_analysis_cursor,
    decode_candidate_cursor,
    encode_analysis_cursor,
    encode_candidate_cursor,
)
from .reports import EvolutionReport
from .tradeoffs import Alternatives, alternatives
from .values import EvidenceSource, EvidenceState, GoalType

log = logging.getLogger("architectos.evolution")

S = EvidenceSource
MAX_REQUIREMENTS = 5000
MAX_PAGE = 200
FINDINGS_PAGE = 500
USABLE_LOOKBACK = 10  # the latest usable (not failed) analysis among the newest few
ENGINE_ERROR = EvolutionError("engine_error", "The evolution engine could not complete this analysis.")
TOO_MANY_REQUIREMENTS = EvolutionError(
    "too_many_requirements",
    f"The project has more than {MAX_REQUIREMENTS} requirements in force; too many to analyze against.",
)
type Fetch[T] = Callable[[int | None], Awaitable[list[tuple[int, T]]]]
type Loaded = tuple[StoredAnalysis, Mapping[str, Any]]  # the evidence, and the analysis's stored inputs


async def _all[T](fetch: Fetch[T]) -> list[T]:
    """Every row of a positioned listing, page by page."""
    found: list[T] = []
    after: int | None = None
    while True:
        page = await fetch(after)
        found += [row for _, row in page]
        if len(page) < FINDINGS_PAGE:
            return found
        after = page[-1][0]


def _cited(source: EvidenceSource, report: object | None) -> None:
    if report is None:
        raise InvalidEvolutionRequest(
            details={"field": "evidence.analysis_id", "reason": "unknown_analysis", "source": source.value}
        )


def _usable[R](reports: list[R], status: Callable[[R], str]) -> R | None:
    return next((r for r in reports if status(r) != "failed"), None)


class EvolutionService:
    def __init__(self, uow: UnitOfWork, engine: EvolutionEngine, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock

    def catalog(self) -> Mapping[str, Any]:
        return self._engine.catalog()

    async def analyze(  # noqa: PLR0913 -- keyword-only, one argument per field of the request
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        goals: tuple[EvolutionGoal, ...],
        revision_number: int | None = None,
        requirement_ids: tuple[uuid.UUID, ...] | None = None,
        constraints: EvolutionConstraints | None = None,
        scope: tuple[str, ...] | None = None,
        evidence: tuple[EvidenceCitation, ...] = (),
        assumptions: tuple[Evidence, ...] = (),
        label: str | None = None,
    ) -> EvolutionReport:
        """Evaluates ``goals`` against ``revision_number`` (default: the current revision) and stores the
        analysis. Invalid requests, unknown cited analyses and requirements are refused (4xx) and nothing
        is stored."""
        # 1. Read and authorize (a short transaction).
        async with self._uow as uow:
            access = await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_EVOLVE)
            architecture = await _architecture(uow, project_id, architecture_id)
            architecture.ensure_modifiable()
            number = revision_number if revision_number is not None else architecture.current_revision
            revision = await uow.architectures.get_revision(architecture.id, number)
            if revision is None:
                raise ArchitectureRevisionNotFound
            request = EvolutionRequest(
                architecture.id,
                revision.number,
                goals,
                requirement_ids,
                constraints or EvolutionConstraints(),
                scope,
                evidence,
                assumptions,
                label,
            )
            in_force = await uow.requirements.list_by_status(project_id, IN_FORCE, limit=MAX_REQUIREMENTS + 1)
            requirements = _relevant(request, in_force)
            loaded = await _evidence(uow, project_id, architecture.id, request)
            settings = access.project.settings
            policy = access.project.policy
            inputs = await _impact_inputs(uow, access.project.organization_id, loaded, revision.content_hash)
        analyses = {source: stored for source, (stored, _) in loaded.items()}
        provider = settings.cloud_provider.value if settings.cloud_provider else None
        inputs = dataclasses.replace(
            inputs, requirements=requirements, policy=policy, provider=provider, currency=settings.currency
        )
        info = RevisionInfo(
            str(architecture.id), revision.number, revision.content_hash, revision.ir_schema_version
        )
        stored_inputs = request.inputs() | {
            "requirements": [[str(r.id), r.version, r.content.status.value] for r in requirements],
            "policy": policy.to_dict(),
            "provider": provider,
            "currency": settings.currency,
            "impact_inputs": {
                "capacity": inputs.capacity.to_dict() if inputs.capacity else None,
                "cost": inputs.cost.to_dict() if inputs.cost else None,
            },
        }
        now = self._clock()
        analysis = EvolutionAnalysis(
            id=uuid.uuid7(),
            project_id=project_id,
            architecture_id=architecture.id,
            revision_number=revision.number,
            revision_content_hash=revision.content_hash,
            status=PENDING,
            requested_by_user_id=user_id,
            requested_at=now,
            label=request.label,
        ).start(now)
        # 2. Calculate on a worker thread, holding no transaction and no lock.
        error: EvolutionError | None = None
        result = None
        if len(in_force) > MAX_REQUIREMENTS:
            error = TOO_MANY_REQUIREMENTS
        else:
            try:
                result = await asyncio.to_thread(
                    self._engine.analyze, revision.ir, info, request, analyses, inputs
                )
            except DomainError:
                raise  # a request the engine refuses: 4xx, nothing stored
            except Exception as failure:  # an engine bug: recorded as a failed analysis
                log.error(  # no traceback: its message could carry a configuration value
                    "evolution engine failed",
                    extra={"analysis_id": str(analysis.id), "error_type": type(failure).__name__},
                )
                error = ENGINE_ERROR
        # 3. Store, re-authorized under the project lock.
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_EVOLVE, lock=ProjectLock.SHARE
            )
            (await _architecture(uow, project_id, architecture_id)).ensure_modifiable()
            candidates: tuple[Candidate, ...] = ()
            if result is None:
                assert error is not None  # noqa: S101 -- set when there is no result
                report = EvolutionReport.of(analysis.fail(error, self._clock()), stored_inputs)
            else:
                report = EvolutionReport.of(analysis.finish(result, self._clock()), stored_inputs)
                candidates = result.candidates
            report = await uow.evolution.add(report, candidates)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ARCHITECTURE_EVOLUTION_ANALYZED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="architecture",
                    resource_id=architecture.id,
                    metadata=_facts(report, len(candidates)),
                )
            )
        return report

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
    ) -> pagination.Page[EvolutionReport]:
        size = pagination.page_size(limit)
        query = EvolutionQuery(revision, decode_analysis_cursor(cursor) if cursor else None, size + 1)
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            rows = await uow.evolution.list_for_architecture(project_id, architecture.id, query)
        items, more = rows[:size], len(rows) > size
        last = items[-1].analysis if more and items else None
        return pagination.Page(
            items=items, next_cursor=encode_analysis_cursor(last.requested_at, last.id) if last else None
        )

    async def get(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID
    ) -> EvolutionReport:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _analysis(uow, project_id, architecture_id, analysis_id)

    async def list_candidates(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        query: CandidateQuery,
        cursor: str | None = None,
    ) -> pagination.Page[Candidate]:
        size = max(1, min(query.limit, MAX_PAGE))
        after = decode_candidate_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            rows = await uow.evolution.list_candidates(
                project_id, report.analysis.id, dataclasses.replace(query, after=after, limit=size + 1)
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(
            items=[c for _, c in items], next_cursor=encode_candidate_cursor(items[-1][0]) if more else None
        )

    async def get_candidate(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        analysis_id: uuid.UUID,
        candidate_id: str,
        user_id: uuid.UUID,
    ) -> tuple[Candidate, Mapping[str, Any] | None]:
        """The candidate and its overlay (the proposed diff, re-applied to its exact baseline revision;
        ``None`` when the candidate cannot be applied — its validation says why)."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            candidate = await uow.evolution.get_candidate(project_id, report.analysis.id, candidate_id)
            if candidate is None:
                raise CandidateNotFound
            revision = await uow.architectures.get_revision(
                report.analysis.architecture_id, report.analysis.revision_number
            )
        if revision is None:
            raise ArchitectureRevisionNotFound
        try:
            return candidate, apply_candidate(revision.ir, candidate).to_dict()
        except InvalidCandidate:
            return candidate, None

    async def alternatives(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Alternatives, ...]:
        """For each goal, its candidates side by side (direction per dimension); nothing is chosen."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _analysis(uow, project_id, architecture_id, analysis_id)
            candidates = await uow.evolution.candidates(project_id, report.analysis.id)
        return alternatives(candidates, report.goals)


# --- evidence -------------------------------------------------------------------------------------


async def _evidence(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, request: EvolutionRequest
) -> dict[EvidenceSource, Loaded]:
    """For each engine, the analysis the request cites (it must be of this architecture) or the latest
    usable one, of any revision (the trigger engine tells current from stale); a failed analysis is
    never evidence."""
    loaders: dict[EvidenceSource, Callable[..., Awaitable[Loaded | None]]] = {
        S.CAPACITY: _capacity,
        S.COST: _cost,
        S.RELIABILITY: _reliability,
        S.SECURITY: _security,
        S.OBSERVABILITY: _observability,
        S.VALIDATION: _validation,
    }
    found: dict[EvidenceSource, Loaded] = {}
    for source in sorted(CITABLE, key=lambda s: s.value):
        loaded = await loaders[source](uow, project_id, architecture_id, request.cited(source))
        if loaded is not None:
            found[source] = loaded
    return found


async def _capacity(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, cited: uuid.UUID | None
) -> Loaded | None:
    repo = uow.capacity
    if cited is not None:
        report = await repo.get(project_id, architecture_id, cited)
        _cited(S.CAPACITY, report)
    else:
        reports = await repo.list_for_architecture(
            project_id, architecture_id, AnalysisQuery(limit=USABLE_LOOKBACK)
        )
        report = _usable(reports, lambda r: r.analysis.status)
    if report is None or report.analysis.status == "failed":
        return None
    bottlenecks = await repo.list_bottlenecks(project_id, report.analysis.id, BottleneckQuery())
    return from_capacity(report, bottlenecks), report.inputs


async def _cost(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, cited: uuid.UUID | None
) -> Loaded | None:
    repo = uow.cost
    if cited is not None:
        report = await repo.get(project_id, architecture_id, cited)
        _cited(S.COST, report)
    else:
        reports = await repo.list_for_architecture(
            project_id, architecture_id, CostAnalysisQuery(limit=USABLE_LOOKBACK)
        )
        report = _usable(reports, lambda r: r.analysis.status)
    if report is None or report.analysis.status == "failed":
        return None
    return from_cost(report), report.inputs


async def _reliability(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, cited: uuid.UUID | None
) -> Loaded | None:
    repo = uow.reliability
    if cited is not None:
        report = await repo.get(project_id, architecture_id, cited)
        _cited(S.RELIABILITY, report)
    else:
        reports = await repo.list_for_architecture(
            project_id, architecture_id, ReliabilityAnalysisQuery(limit=USABLE_LOOKBACK)
        )
        report = _usable(reports, lambda r: r.analysis.status)
    if report is None or report.analysis.status == "failed":
        return None
    analysis_id = report.analysis.id
    findings = await _all(
        lambda after: repo.list_findings(
            project_id, analysis_id, ReliabilityFindingQuery(after=after, limit=FINDINGS_PAGE)
        )
    )
    return from_reliability(report, findings), report.inputs


async def _security(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, cited: uuid.UUID | None
) -> Loaded | None:
    repo = uow.security
    if cited is not None:
        report = await repo.get(project_id, architecture_id, cited)
        _cited(S.SECURITY, report)
    else:
        reports = await repo.list_for_architecture(
            project_id, architecture_id, SecurityAnalysisQuery(limit=USABLE_LOOKBACK)
        )
        report = _usable(reports, lambda r: r.analysis.status)
    if report is None or report.analysis.status == "failed":
        return None
    analysis_id = report.analysis.id
    findings = await _all(
        lambda after: repo.list_findings(
            project_id, analysis_id, SecurityFindingQuery(after=after, limit=FINDINGS_PAGE)
        )
    )
    return from_security(report, findings), report.inputs


async def _observability(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, cited: uuid.UUID | None
) -> Loaded | None:
    repo = uow.observability
    if cited is not None:
        report = await repo.get(project_id, architecture_id, cited)
        _cited(S.OBSERVABILITY, report)
    else:
        reports = await repo.list_for_architecture(
            project_id, architecture_id, ObservabilityAnalysisQuery(limit=USABLE_LOOKBACK)
        )
        report = _usable(reports, lambda r: r.analysis.status)
    if report is None or report.analysis.status == "failed":
        return None
    analysis_id = report.analysis.id
    findings = await _all(
        lambda after: repo.list_findings(
            project_id, analysis_id, ObservabilityFindingQuery(after=after, limit=FINDINGS_PAGE)
        )
    )
    return from_observability(report, findings), report.inputs


async def _validation(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, cited: uuid.UUID | None
) -> Loaded | None:
    repo = uow.validations
    if cited is not None:
        report = await repo.get(project_id, architecture_id, cited)
        _cited(S.VALIDATION, report)
    else:
        reports = await repo.list_for_architecture(
            project_id, architecture_id, RunQuery(limit=USABLE_LOOKBACK)
        )
        report = _usable(reports, lambda r: r.run.status.value)
    if report is None or report.run.status.value == "failed":
        return None
    run_id = report.run.id
    findings = await _all(
        lambda after: repo.list_findings(project_id, run_id, FindingQuery(after=after, limit=FINDINGS_PAGE))
    )
    return from_validation(report, findings), {}


async def _impact_inputs(
    uow: UnitOfWork, organization_id: uuid.UUID, loaded: Mapping[EvidenceSource, Loaded], content_hash: str
) -> ImpactInputs:
    """The workload of the current capacity analysis and the pricing of the current cost analysis
    (of this exact content only), reused unchanged for every candidate's impact."""
    inputs = ImpactInputs()
    capacity = loaded.get(S.CAPACITY)
    if capacity is not None and capacity[0].content_hash == content_hash and capacity[1].get("workload"):
        stored, raw = capacity
        entries = raw.get("entries")
        inputs = dataclasses.replace(
            inputs,
            workload=WorkloadProfile.from_dict(raw["workload"]),
            entries=tuple(entries) if entries else None,
            capacity=stored.ref(EvidenceState.CURRENT),
        )
    cost = loaded.get(S.COST)
    if cost is not None and cost[0].content_hash == content_hash:
        stored, raw = cost
        pricing = PricingInputs(
            uuid.UUID(raw["snapshot_id"]),
            date.fromisoformat(raw["pricing_date"]),
            Decimal(raw["operating_hours_per_month"]),
        )
        snapshot = await uow.pricing.get(organization_id, pricing.snapshot_id)
        if snapshot is not None:
            inputs = dataclasses.replace(
                inputs, pricing=pricing, snapshot=snapshot, cost=stored.ref(EvidenceState.CURRENT)
            )
    return inputs


def _relevant(request: EvolutionRequest, in_force: list[Requirement]) -> tuple[Requirement, ...]:
    """The requirements the analysis reads: those named (each must be in force), or all in force;
    a requirement a goal names must be in force too."""
    by_id = {r.id: r for r in in_force}
    named = {g.requirement_id for g in request.goals if g.type is GoalType.SATISFY_REQUIREMENT}
    wanted = set(request.requirement_ids) if request.requirement_ids is not None else None
    if any(i not in by_id for i in named | (wanted or set()) if i is not None):
        raise InvalidEvolutionRequest(details={"field": "requirement_ids", "reason": "unknown_requirement"})
    chosen = [r for r in in_force if wanted is None or r.id in wanted]
    return tuple(sorted(chosen, key=lambda r: r.number)[:MAX_REQUIREMENTS])


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _analysis(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, analysis_id: uuid.UUID
) -> EvolutionReport:
    await _architecture(uow, project_id, architecture_id)  # a deleted architecture hides its analyses
    report = await uow.evolution.get(project_id, architecture_id, analysis_id)
    if report is None:
        raise EvolutionAnalysisNotFound
    return report


def _facts(report: EvolutionReport, candidates: int) -> dict[str, object]:
    analysis = report.analysis
    facts: dict[str, object] = {
        "project_id": str(analysis.project_id),
        "analysis_id": str(analysis.id),
        "revision": analysis.revision_number,
        "status": analysis.status,
        "goals": len(report.goals),
        "candidates": candidates,
        "findings": len(report.findings),
    }
    if analysis.error is not None:
        facts["error"] = analysis.error.code
    return facts
