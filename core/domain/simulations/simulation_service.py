"""Simulation use cases: run a scenario against an architecture revision, read simulations, page
through component outcomes and deltas, compare two simulations, and describe what is supported.

A simulation is synchronous, in three steps, like the other analyses: read and authorize (a short
transaction: the revision, the project's in-force requirements, its provider and currency, and —
when cost is asked — the organization's pricing snapshot), calculate (on a worker thread, no
transaction, no lock: every input is immutable), then store, finished, in a transaction that
re-checks access and modifiability under the project lock and records the audit entry. The scenario
snapshot, the overlay and the requirements read (id, version, status) are stored with the
simulation, so it stays reproducible and explainable. No telemetry is read, nothing is deployed and
the architecture is never changed.

Access: running needs ``architecture.analyze`` and a modifiable project and architecture; reading
and comparing need ``architecture.read``. Every lookup goes project -> architecture -> simulation;
the pricing snapshot is read within the project's organization. Audit entries carry identifiers and
counts only; an engine failure is logged by the error's type, never its message.
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
from core.domain.capacity.workload import WorkloadProfile
from core.domain.clock import Clock, utc_now
from core.domain.cost.errors import PricingSnapshotNotFound
from core.domain.cost.pricing import PricingSnapshot
from core.domain.errors import DomainError
from core.domain.organizations.permissions import Permission
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.requirements.requirements import IN_FORCE
from core.domain.unit_of_work import UnitOfWork
from core.domain.validation.options import RevisionInfo

from .comparison import SimulationComparison, compare
from .entities import (
    PENDING,
    PricingInputs,
    Simulation,
    SimulationAssumption,
    SimulationError,
    SimulationRequest,
)
from .errors import InvalidSimulationRequest, SimulationNotFound
from .ports import SimulationEngine, SimulationOutput
from .queries import (
    SimulationComponentQuery,
    SimulationDeltaQuery,
    SimulationQuery,
    decode_component_cursor,
    decode_delta_cursor,
    decode_simulation_cursor,
    encode_component_cursor,
    encode_delta_cursor,
    encode_simulation_cursor,
)
from .reports import SimulationReport
from .results import ComponentOutcome, Delta
from .scenarios import Scenario
from .values import AnalysisKind

log = logging.getLogger("architectos.simulation")

MAX_REQUIREMENTS = 5000
MAX_PAGE = 500
ENGINE_ERROR = SimulationError("engine_error", "The simulation engine could not complete this simulation.")
TOO_MANY_REQUIREMENTS = SimulationError(
    "too_many_requirements",
    f"The project has more than {MAX_REQUIREMENTS} requirements in force; too many to simulate against.",
)


class SimulationService:
    def __init__(self, uow: UnitOfWork, engine: SimulationEngine, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock

    def catalog(self) -> Mapping[str, Any]:
        return self._engine.catalog()

    async def simulate(  # noqa: PLR0913 -- keyword-only, one argument per field of the request
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        scenario: Scenario,
        revision_number: int | None = None,
        analyses: tuple[AnalysisKind, ...] | None = None,
        workload: WorkloadProfile | None = None,
        entries: tuple[str, ...] | None = None,
        pricing: PricingInputs | None = None,
        assumptions: tuple[SimulationAssumption, ...] = (),
        label: str | None = None,
    ) -> SimulationReport:
        """Simulates ``scenario`` against ``revision_number`` (default: the current revision) and stores
        the simulation. Invalid scenarios, unknown snapshots and over-limit requests are refused (4xx)
        and nothing is stored."""
        # 1. Read and authorize (a short transaction).
        async with self._uow as uow:
            access = await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE)
            architecture = await _architecture(uow, project_id, architecture_id)
            architecture.ensure_modifiable()
            number = revision_number if revision_number is not None else architecture.current_revision
            revision = await uow.architectures.get_revision(architecture.id, number)
            if revision is None:
                raise ArchitectureRevisionNotFound
            snapshot: PricingSnapshot | None = None
            if pricing is not None:
                snapshot = await uow.pricing.get(access.project.organization_id, pricing.snapshot_id)
                if snapshot is None:
                    raise PricingSnapshotNotFound
            requirements = await uow.requirements.list_by_status(
                project_id, IN_FORCE, limit=MAX_REQUIREMENTS + 1
            )
            settings = access.project.settings
        request = SimulationRequest(
            architecture.id,
            revision.number,
            scenario,
            analyses,
            workload,
            entries,
            pricing,
            assumptions,
            label,
        )
        info = RevisionInfo(
            str(architecture.id), revision.number, revision.content_hash, revision.ir_schema_version
        )
        provider = settings.cloud_provider.value if settings.cloud_provider else None
        ordered = tuple(sorted(requirements, key=lambda r: r.number)[:MAX_REQUIREMENTS])
        inputs = request.inputs() | {
            "requirements": [[str(r.id), r.version, r.content.status.value] for r in ordered],
            "provider": provider,
            "currency": settings.currency,
        }
        now = self._clock()
        simulation = Simulation(
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
        output: SimulationOutput | None = None
        error: SimulationError | None = None
        if len(requirements) > MAX_REQUIREMENTS:
            error = TOO_MANY_REQUIREMENTS
        else:
            output = await self._run(
                simulation.id,
                lambda: self._engine.simulate(
                    revision.ir, info, request, ordered, snapshot, provider, settings.currency
                ),
            )
            error = ENGINE_ERROR if output is None else None
        # 3. Store, re-authorized under the project lock.
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_ANALYZE, lock=ProjectLock.SHARE
            )
            (await _architecture(uow, project_id, architecture_id)).ensure_modifiable()
            components: tuple[ComponentOutcome, ...] = ()
            deltas: tuple[Delta, ...] = ()
            if output is None:
                assert error is not None  # noqa: S101 -- set with no output
                report = SimulationReport.of(simulation.fail(error, self._clock()), inputs, None)
            else:
                finished = simulation.finish(output.result, self._clock())
                report = SimulationReport.of(finished, inputs, output.overlay)
                components, deltas = output.result.components, output.result.deltas
            report = await uow.simulations.add(report, components, deltas)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.ARCHITECTURE_SIMULATED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="architecture",
                    resource_id=architecture.id,
                    metadata=_facts(report, len(components), len(deltas)),
                )
            )
        return report

    async def _run(
        self, simulation_id: uuid.UUID, run: Callable[[], SimulationOutput]
    ) -> SimulationOutput | None:
        try:
            return await asyncio.to_thread(run)
        except DomainError:
            raise  # a request the engine refuses: 4xx, nothing stored
        except Exception as error:  # an engine bug: recorded as a failed simulation, never a partial one
            log.error(  # no traceback: its message could carry a configuration value
                "simulation engine failed",
                extra={"simulation_id": str(simulation_id), "error_type": type(error).__name__},
            )
            return None

    # --- reading ---------------------------------------------------------------------------------

    async def list_simulations(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        revision: int | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[SimulationReport]:
        size = pagination.page_size(limit)
        query = SimulationQuery(revision, decode_simulation_cursor(cursor) if cursor else None, size + 1)
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            rows = await uow.simulations.list_for_architecture(project_id, architecture.id, query)
        items, more = rows[:size], len(rows) > size
        last = items[-1].simulation if more and items else None
        return pagination.Page(
            items=items, next_cursor=encode_simulation_cursor(last.requested_at, last.id) if last else None
        )

    async def get(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        simulation_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SimulationReport:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _simulation(uow, project_id, architecture_id, simulation_id)

    async def list_components(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        simulation_id: uuid.UUID,
        user_id: uuid.UUID,
        query: SimulationComponentQuery,
        cursor: str | None = None,
    ) -> pagination.Page[ComponentOutcome]:
        size = max(1, min(query.limit, MAX_PAGE))
        after = decode_component_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _simulation(uow, project_id, architecture_id, simulation_id)
            rows = await uow.simulations.list_components(
                project_id,
                report.simulation.id,
                SimulationComponentQuery(query.unavailable, query.impact, after, size + 1),
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(
            items=items, next_cursor=encode_component_cursor(items[-1].node_id) if more else None
        )

    async def list_deltas(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        simulation_id: uuid.UUID,
        user_id: uuid.UUID,
        query: SimulationDeltaQuery,
        cursor: str | None = None,
    ) -> pagination.Page[Delta]:
        size = max(1, min(query.limit, MAX_PAGE))
        after = decode_delta_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            report = await _simulation(uow, project_id, architecture_id, simulation_id)
            rows = await uow.simulations.list_deltas(
                project_id,
                report.simulation.id,
                SimulationDeltaQuery(query.analysis, query.element_id, query.comparable, after, size + 1),
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(
            items=[d for _, d in items], next_cursor=encode_delta_cursor(items[-1][0]) if more else None
        )

    async def compare(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        first_id: uuid.UUID,
        second_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SimulationComparison:
        """The second simulation's scenario against the first's, where directly comparable. Both must be
        finished simulations of this architecture."""
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            results = []
            for simulation_id in (first_id, second_id):
                report = await _simulation(uow, project_id, architecture_id, simulation_id)
                if report.engine_set is None:
                    raise InvalidSimulationRequest(details={"field": "simulation_id", "reason": "no_result"})
                components, deltas = await uow.simulations.rows(project_id, report.simulation.id)
                results.append(report.result(components, deltas))
        return compare(results[0], results[1])


async def _architecture(uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _simulation(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, simulation_id: uuid.UUID
) -> SimulationReport:
    await _architecture(uow, project_id, architecture_id)  # a deleted architecture hides its simulations
    report = await uow.simulations.get(project_id, architecture_id, simulation_id)
    if report is None:
        raise SimulationNotFound
    return report


def _facts(report: SimulationReport, components: int, deltas: int) -> dict[str, object]:
    simulation = report.simulation
    facts: dict[str, object] = {
        "project_id": str(simulation.project_id),
        "simulation_id": str(simulation.id),
        "revision": simulation.revision_number,
        "status": simulation.status,
        "components": components,
        "deltas": deltas,
        "runs": len(report.runs),
    }
    if simulation.error is not None:
        facts["error"] = simulation.error.code
    return facts
