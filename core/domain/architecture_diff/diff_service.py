"""Architecture diff use cases: compare two exact states, read and list diffs, and — on request — ask
for an AI explanation of a stored diff (appended as a run; the diff never changes).

A comparison runs in three steps like the analyses: authorize and load (a short transaction: both
states, the requirements a state may trace to and those in force, the project's ADRs, and the inputs of
a named capacity or cost analysis), compare (on a worker thread, no transaction: every input is
immutable), then store — re-authorized under the project lock — and audit. An explanation is the same:
load the diff, interpret (knowledge through the retriever, which authorizes on its own; the model),
store the run and audit it.

**Nothing here changes an architecture, a revision, an agent run or an analysis.** Each state is named
exactly (a revision, or an agent run's candidate); one that is missing, hidden or not comparable gets
the same answer (``ComparedStateNotFound`` with its side), so a hidden state cannot be told apart.

Access: comparing and explaining need ``architecture.analyze``; reading needs ``architecture.read``.
Every lookup goes project → diff. Audit entries carry identifiers, statuses and counts — never the
person's context, architecture content, prompts, passages or model output.
"""

import asyncio
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.domain import pagination
from core.domain.architecture_agent.values import RunStatus
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.errors import CapacityAnalysisNotFound
from core.domain.clock import Clock, utc_now
from core.domain.cost.errors import CostAnalysisNotFound, PricingSnapshotNotFound
from core.domain.decisions.entities import Decision
from core.domain.knowledge.ports import KnowledgeRetriever
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.entities import Requirement
from core.domain.requirements.requirements import IN_FORCE
from core.domain.unit_of_work import UnitOfWork

from .diffs import ArchitectureDiff
from .errors import ArchitectureDiffNotFound, ComparedStateNotFound
from .explanations import ExplanationRun
from .ports import (
    CapacityInputs,
    CostInputs,
    DiffComputer,
    DiffInputs,
    DiffInterpreter,
    ExplanationBudget,
    ImpactInputs,
    ResolvedState,
)
from .references import ComparedState, DiffRequest, StateRef
from .repository import DiffListing
from .values import StateKind

MAX_PAGE = 100
MAX_REQUIREMENTS = 5000
MAX_DECISIONS = 500
MAX_LABEL = 200
WITH_CANDIDATE = frozenset({RunStatus.CANDIDATE_READY, RunStatus.ACCEPTED, RunStatus.REJECTED})
DIFFERENT_ARCHITECTURES = (
    "The states belong to different architectures: an element is matched by its id alone, so a "
    "component kept under another id appears removed and added."
)
TOO_MANY_REQUIREMENTS = (
    f"The project has more than {MAX_REQUIREMENTS} requirements in force; only the first were evaluated."
)
_LISTING = "architecture_diffs"


@dataclass(frozen=True, slots=True)
class DiffReport:
    diff: ArchitectureDiff
    explanations: tuple[ExplanationRun, ...] = ()  # oldest first


@dataclass(frozen=True, slots=True)
class _Loaded:
    access: ProjectAccess
    base: ResolvedState
    target: ResolvedState
    inputs: DiffInputs
    warnings: tuple[str, ...]


def encode_diff_cursor(listing: DiffListing) -> str:
    return pagination.encode_cursor([_LISTING, listing.compared_at.isoformat(), str(listing.id)])


def decode_diff_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    sort, compared_at, diff_id = pagination.decode_cursor(raw, length=3)
    if sort != _LISTING:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(compared_at), uuid.UUID(diff_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def _traced(ir: ArchitectureIR) -> set[uuid.UUID]:
    refs = (
        *ir.requirement_refs,
        *(r for n in ir.nodes for r in n.requirement_refs),
        *(r for c in ir.connections for r in c.requirement_refs),
        *(r for a in ir.assumptions for r in a.requirement_refs),
    )
    return {ref.requirement_id for ref in refs}


class ArchitectureDiffService:
    def __init__(
        self,
        uow: UnitOfWork,
        computer: DiffComputer,
        interpreter: DiffInterpreter,
        retriever: KnowledgeRetriever,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._uow = uow
        self._computer = computer
        self._interpreter = interpreter
        self._retriever = retriever
        self._clock = clock

    # --- comparing -------------------------------------------------------------------------------

    async def compare(self, *, project_id: uuid.UUID, user_id: uuid.UUID, request: DiffRequest) -> DiffReport:
        """The diff of two exact states, stored; with ``request.explain``, also one explanation run."""
        loaded = await self._load(project_id, user_id, request)
        outcome = await asyncio.to_thread(self._computer.compare, loaded.base, loaded.target, loaded.inputs)
        diff = ArchitectureDiff(
            id=uuid.uuid7(),
            project_id=project_id,
            request=request,
            base=loaded.base.compared,
            target=loaded.target.compared,
            semantic=outcome.semantic,
            requested_by_user_id=user_id,
            created_at=self._clock(),
            requirements=outcome.requirements,
            decisions=outcome.decisions,
            engines=outcome.engines,
            warnings=loaded.warnings,
            unknowns=outcome.unknowns,
        )
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE, lock=ProjectLock.SHARE
            )
            stored = await uow.architecture_diffs.add(diff)
            await _audit(
                uow, access, AuditAction.ARCHITECTURE_DIFF_CREATED, user_id, stored, _diff_facts(stored)
            )
        if not request.explain:
            return DiffReport(stored)
        return DiffReport(stored, (await self._explain(stored, user_id),))

    async def explain(
        self, *, project_id: uuid.UUID, diff_id: uuid.UUID, user_id: uuid.UUID
    ) -> ExplanationRun:
        """Another explanation of a stored diff, appended (never replacing an earlier one)."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE)
            diff = await _diff(uow, project_id, diff_id)
        return await self._explain(diff, user_id)

    async def _explain(self, diff: ArchitectureDiff, user_id: uuid.UUID) -> ExplanationRun:
        project_id = diff.project_id

        async def retrieve(query: RetrievalQuery) -> RetrievalResult:
            return await self._retriever.retrieve(project_id=project_id, user_id=user_id, query=query)

        run = await self._interpreter.interpret(
            diff, retrieve, ExplanationBudget(), run_id=uuid.uuid7(), user_id=user_id, now=self._clock()
        )
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE, lock=ProjectLock.SHARE
            )
            stored = await uow.architecture_diffs.add_explanation(project_id, run)
            await _audit(
                uow, access, AuditAction.ARCHITECTURE_DIFF_EXPLAINED, user_id, diff, _run_facts(stored)
            )
        return stored

    # --- reading ---------------------------------------------------------------------------------

    async def get(self, *, project_id: uuid.UUID, diff_id: uuid.UUID, user_id: uuid.UUID) -> DiffReport:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            diff = await _diff(uow, project_id, diff_id)
            runs = await uow.architecture_diffs.list_explanations(project_id, diff_id)
        return DiffReport(diff, tuple(runs))

    async def list(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        architecture_id: uuid.UUID | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[DiffListing]:
        size = min(pagination.page_size(limit), MAX_PAGE)
        after = decode_diff_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.architecture_diffs.list(
                project_id, architecture_id=architecture_id, after=after, limit=size + 1
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(items=items, next_cursor=encode_diff_cursor(items[-1]) if more else None)

    # --- loading ---------------------------------------------------------------------------------

    async def _load(self, project_id: uuid.UUID, user_id: uuid.UUID, request: DiffRequest) -> _Loaded:
        async with self._uow as uow:
            access = await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE)
            base, base_architecture = await _resolve(uow, project_id, request.base, "base")
            target, target_architecture = await _resolve(uow, project_id, request.target, "target")
            architectures = (base_architecture, target_architecture)
            in_force = await uow.requirements.list_by_status(project_id, IN_FORCE, limit=MAX_REQUIREMENTS + 1)
            wanted = _traced(base.ir) | _traced(target.ir) | set(request.requirement_ids or ())
            traced = await uow.requirements.list_by_ids(project_id, sorted(wanted, key=str)) if wanted else []
            decisions = await _decisions(uow, project_id)
            capacity = await capacity_inputs(uow, project_id, request.capacity_analysis_id, architectures)
            cost = await cost_inputs(uow, access, request.cost_analysis_id, architectures)
        warnings: list[str] = []
        if base_architecture is None or base_architecture != target_architecture:
            warnings.append(DIFFERENT_ARCHITECTURES)
        if len(in_force) > MAX_REQUIREMENTS:
            warnings.append(TOO_MANY_REQUIREMENTS)
        evaluated = tuple(sorted(in_force, key=lambda r: r.number)[:MAX_REQUIREMENTS])
        impact = ImpactInputs(evaluated, access.project.policy, capacity, cost)
        known: dict[uuid.UUID, Requirement] = {r.id: r for r in (*evaluated, *traced)}
        inputs = DiffInputs(impact, known, decisions, request.requirement_ids)
        return _Loaded(access, base, target, inputs, tuple(warnings))


async def _resolve(
    uow: UnitOfWork, project_id: uuid.UUID, ref: StateRef, side: str
) -> tuple[ResolvedState, uuid.UUID | None]:
    """The exact state, of this project — or ``ComparedStateNotFound`` whatever the reason."""
    if ref.kind is StateKind.REVISION:
        assert ref.architecture_id is not None  # noqa: S101 - a revision reference has both
        assert ref.revision_number is not None  # noqa: S101
        architecture = await uow.architectures.get(project_id, ref.architecture_id)
        revision = (
            await uow.architectures.get_revision(architecture.id, ref.revision_number)
            if architecture
            else None
        )
        if architecture is None or revision is None:
            raise ComparedStateNotFound(details={"side": side})
        label = f"{architecture.name[: MAX_LABEL - 12]} r{revision.number}"
        compared = ComparedState(ref, revision.content_hash, label)
        return ResolvedState(compared, revision.ir, architecture.id, revision.number), architecture.id
    assert ref.run_id is not None  # noqa: S101 - StateRef
    run = await uow.agent_runs.get(project_id, ref.run_id)
    if run is None or run.status not in WITH_CANDIDATE or run.candidate is None:
        raise ComparedStateNotFound(details={"side": side})  # no candidate: not comparable, said the same
    compared = ComparedState(ref, run.candidate.content_hash, f"Agent candidate (run {str(run.id)[:8]})")
    base = run.request.base
    return ResolvedState(compared, run.candidate.ir, run.id, 1), base.architecture_id if base else None


async def _decisions(uow: UnitOfWork, project_id: uuid.UUID) -> tuple[Decision, ...]:
    found: list[Decision] = []
    after: int | None = None
    while len(found) < MAX_DECISIONS:
        page = await uow.decisions.list_for_project(project_id, after=after, limit=100)
        found += page
        if len(page) < 100:
            break
        after = page[-1].number
    return tuple(found[:MAX_DECISIONS])


def _of_compared(architectures: Iterable[uuid.UUID | None]) -> list[uuid.UUID]:
    return list(dict.fromkeys(a for a in architectures if a is not None))


async def capacity_inputs(
    uow: UnitOfWork,
    project_id: uuid.UUID,
    analysis_id: uuid.UUID | None,
    architectures: tuple[uuid.UUID | None, uuid.UUID | None],
) -> CapacityInputs | None:
    """The workload of a stored capacity analysis of a compared architecture."""
    if analysis_id is None:
        return None
    for architecture_id in _of_compared(architectures):
        report = await uow.capacity.get(project_id, architecture_id, analysis_id)
        if report is not None:
            stated = AnalysisRequest.from_inputs(report.inputs)
            return CapacityInputs(analysis_id, stated.workload, stated.entries)
    raise CapacityAnalysisNotFound


async def cost_inputs(
    uow: UnitOfWork,
    access: ProjectAccess,
    analysis_id: uuid.UUID | None,
    architectures: tuple[uuid.UUID | None, uuid.UUID | None],
) -> CostInputs | None:
    """The pricing inputs of a stored cost analysis of a compared architecture, and its snapshot."""
    if analysis_id is None:
        return None
    project = access.project
    for architecture_id in _of_compared(architectures):
        report = await uow.cost.get(project.id, architecture_id, analysis_id)
        if report is None:
            continue
        snapshot = await uow.pricing.get(project.organization_id, report.snapshot_id)
        if snapshot is None:
            raise PricingSnapshotNotFound
        provider = project.settings.cloud_provider
        return CostInputs(
            analysis_id,
            snapshot,
            report.currency,
            date.fromisoformat(report.inputs["pricing_date"]),
            Decimal(report.inputs["operating_hours_per_month"]),
            provider.value if provider else None,
        )
    raise CostAnalysisNotFound


async def _diff(uow: UnitOfWork, project_id: uuid.UUID, diff_id: uuid.UUID) -> ArchitectureDiff:
    found = await uow.architecture_diffs.get(project_id, diff_id)
    if found is None:
        raise ArchitectureDiffNotFound
    return found


def _side(state: ComparedState) -> dict[str, Any]:
    ref = state.ref
    facts: dict[str, Any] = {"kind": ref.kind.value}
    if ref.kind is StateKind.REVISION:
        facts |= {"architecture_id": str(ref.architecture_id), "revision": ref.revision_number}
    else:
        facts["run_id"] = str(ref.run_id)
    return facts


def _diff_facts(diff: ArchitectureDiff) -> dict[str, Any]:
    return {
        "base": _side(diff.base),
        "target": _side(diff.target),
        "changes": len(diff.semantic.changes),
        "groups": len(diff.semantic.groups),
        "requirements": len(diff.requirements),
        "decisions": len(diff.decisions),
        "engines": {e.engine: e.status.value for e in diff.engines},
        "explain": diff.request.explain,
    }


def _run_facts(run: ExplanationRun) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "explanation_run_id": str(run.id),
        "status": run.status.value,
        "model_calls": run.usage.model_calls,
        "retrieval_calls": run.usage.retrieval_calls,
        "evidence": len(run.evidence),
    }
    if run.prompt_version is not None:
        facts["prompt_version"] = run.prompt_version
    if run.failure is not None:
        facts["failure"] = run.failure.value
    return facts


async def _audit(
    uow: UnitOfWork,
    access: ProjectAccess,
    action: AuditAction,
    user_id: uuid.UUID,
    diff: ArchitectureDiff,
    facts: dict[str, Any],
) -> None:
    """Identifiers, statuses and counts only."""
    await uow.audit.record(
        AuditEvent(
            action,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="architecture_diff",
            resource_id=diff.id,
            metadata={"project_id": str(diff.project_id)} | facts,
        )
    )
