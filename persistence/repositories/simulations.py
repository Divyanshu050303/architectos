"""Simulations with their component outcomes and deltas (append-only). Every read is scoped by project
(and, for simulations, architecture); rows are read only for a simulation already found."""

import uuid

from sqlalchemy import insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.engine_results import Limitation, ModelSet, Unsupported, read_evidence
from core.domain.simulations.entities import Simulation, SimulationError
from core.domain.simulations.queries import SimulationComponentQuery, SimulationDeltaQuery, SimulationQuery
from core.domain.simulations.reports import SimulationReport
from core.domain.simulations.results import AnalysisRun, ComponentOutcome, Delta, EntryImpact
from persistence.models import SimulationComponentRecord, SimulationDeltaRecord, SimulationRecord

_BATCH = 500
S, C, D = SimulationRecord, SimulationComponentRecord, SimulationDeltaRecord


def to_report(record: SimulationRecord) -> SimulationReport:
    error = SimulationError(record.error_code, record.error_message or "") if record.error_code else None
    simulation = Simulation(
        id=record.id,
        project_id=record.project_id,
        architecture_id=record.architecture_id,
        revision_number=record.revision_number,
        revision_content_hash=record.revision_content_hash,
        status=record.status,
        requested_by_user_id=record.requested_by_user_id,
        requested_at=record.requested_at,
        label=record.label,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error=error,
    )
    return SimulationReport(
        simulation=simulation,
        inputs=record.inputs,
        overlay=record.overlay,
        engine_set=ModelSet.from_dict(record.engine_set) if record.engine_set else None,
        context_fingerprint=record.context_fingerprint,
        scenario_fingerprint=record.scenario_fingerprint,
        result_fingerprint=record.result_fingerprint,
        summary=record.summary,
        runs=tuple(AnalysisRun.from_dict(r) for r in record.runs),
        entries=tuple(EntryImpact.from_dict(e) for e in record.entries),
        assumptions=read_evidence(record.assumptions),
        trace=read_evidence(record.trace),
        unsupported=tuple(Unsupported.from_dict(u) for u in record.unsupported),
        limitations=tuple(Limitation.from_dict(x) for x in record.limitations),
    )


class SqlAlchemySimulationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        report: SimulationReport,
        components: tuple[ComponentOutcome, ...],
        deltas: tuple[Delta, ...],
    ) -> SimulationReport:
        simulation = report.simulation
        self._session.add(
            SimulationRecord(
                id=simulation.id,
                project_id=simulation.project_id,
                architecture_id=simulation.architecture_id,
                revision_number=simulation.revision_number,
                revision_content_hash=simulation.revision_content_hash,
                status=simulation.status,
                label=simulation.label,
                requested_by_user_id=simulation.requested_by_user_id,
                requested_at=simulation.requested_at,
                started_at=simulation.started_at,
                completed_at=simulation.completed_at,
                inputs=dict(report.inputs),
                overlay=dict(report.overlay) if report.overlay is not None else None,
                engine_set=report.engine_set.to_dict() if report.engine_set else None,
                context_fingerprint=report.context_fingerprint,
                scenario_fingerprint=report.scenario_fingerprint,
                result_fingerprint=report.result_fingerprint,
                summary=dict(report.summary) if report.summary is not None else None,
                runs=[r.to_dict() for r in report.runs],
                entries=[e.to_dict() for e in report.entries],
                assumptions=[e.to_dict() for e in report.assumptions],
                trace=[e.to_dict() for e in report.trace],
                unsupported=[u.to_dict() for u in report.unsupported],
                limitations=[x.to_dict() for x in report.limitations],
                error_code=simulation.error.code if simulation.error else None,
                error_message=simulation.error.message if simulation.error else None,
            )
        )
        await self._session.flush()
        common = {"simulation_id": simulation.id, "project_id": simulation.project_id}
        rows = (
            (
                C,
                [
                    {
                        "id": uuid.uuid7(),
                        **common,
                        "node_id": c.node_id,
                        "unavailable": c.unavailable,
                        "impact": c.impact.value if c.impact is not None else None,
                        "data": c.to_dict(),
                    }
                    for c in components
                ],
            ),
            (
                D,
                [
                    {
                        "id": uuid.uuid7(),
                        **common,
                        "position": position,
                        "analysis": d.analysis.value,
                        "element_id": d.element_id,
                        "metric": d.metric,
                        "comparable": d.comparable,
                        "data": d.to_dict(),
                    }
                    for position, d in enumerate(deltas)
                ],
            ),
        )
        for model, batch in rows:
            for start in range(0, len(batch), _BATCH):
                await self._session.execute(insert(model), batch[start : start + _BATCH])
        return report

    async def get(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, simulation_id: uuid.UUID
    ) -> SimulationReport | None:
        record = await self._session.scalar(
            select(S).where(
                S.id == simulation_id, S.project_id == project_id, S.architecture_id == architecture_id
            )
        )
        return to_report(record) if record else None

    async def list_for_architecture(
        self, project_id: uuid.UUID, architecture_id: uuid.UUID, query: SimulationQuery
    ) -> list[SimulationReport]:
        statement = select(S).where(S.project_id == project_id, S.architecture_id == architecture_id)
        if query.revision is not None:
            statement = statement.where(S.revision_number == query.revision)
        if query.after is not None:
            requested_at, simulation_id = query.after
            statement = statement.where(
                tuple_(S.requested_at, S.id) < tuple_(literal(requested_at), literal(simulation_id))
            )
        ordered = statement.order_by(S.requested_at.desc(), S.id.desc())
        return [to_report(r) for r in await self._session.scalars(ordered.limit(query.limit))]

    async def list_components(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID, query: SimulationComponentQuery
    ) -> list[ComponentOutcome]:
        statement = select(C.data).where(C.simulation_id == simulation_id, C.project_id == project_id)
        if query.unavailable is not None:
            statement = statement.where(C.unavailable.is_(query.unavailable))
        if query.impact is not None:
            statement = statement.where(C.impact == query.impact.value)
        if query.after is not None:
            statement = statement.where(C.node_id > query.after)
        rows = await self._session.scalars(statement.order_by(C.node_id).limit(query.limit))
        return [ComponentOutcome.from_dict(data) for data in rows]

    async def list_deltas(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID, query: SimulationDeltaQuery
    ) -> list[tuple[int, Delta]]:
        statement = select(D.position, D.data).where(
            D.simulation_id == simulation_id, D.project_id == project_id
        )
        if query.analysis is not None:
            statement = statement.where(D.analysis == query.analysis.value)
        if query.element_id is not None:
            statement = statement.where(D.element_id == query.element_id)
        if query.comparable is not None:
            statement = statement.where(D.comparable.is_(query.comparable))
        if query.after is not None:
            statement = statement.where(D.position > query.after)
        rows = await self._session.execute(statement.order_by(D.position).limit(query.limit))
        return [(position, Delta.from_dict(data)) for position, data in rows]

    async def rows(
        self, project_id: uuid.UUID, simulation_id: uuid.UUID
    ) -> tuple[tuple[ComponentOutcome, ...], tuple[Delta, ...]]:
        components = await self._session.scalars(
            select(C.data)
            .where(C.simulation_id == simulation_id, C.project_id == project_id)
            .order_by(C.node_id)
        )
        deltas = await self._session.scalars(
            select(D.data)
            .where(D.simulation_id == simulation_id, D.project_id == project_id)
            .order_by(D.position)
        )
        return (
            tuple(ComponentOutcome.from_dict(c) for c in components),
            tuple(Delta.from_dict(d) for d in deltas),
        )
