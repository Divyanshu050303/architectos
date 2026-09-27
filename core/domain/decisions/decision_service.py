"""Decision use cases: draft a decision record from an evolution analysis, read and list decisions,
render one as an ADR, and record a person's decision — accept an option, reject them all, supersede
an accepted decision, or link the revision a person says implements it.

None of these changes the architecture. Applying an option is a separate, authorized change made in
the architecture workflow; linking its resulting revision afterwards is a person's statement.

Access: drafting and deciding need ``architecture.evolve`` and a modifiable project and architecture;
reading needs ``architecture.read``. Every lookup is scoped by project. Drafting allocates the ADR
number under the project's exclusive lock; every change is audited with identifiers only.
"""

import uuid

from core.domain import pagination
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.evolution.errors import EvolutionAnalysisNotFound
from core.domain.evolution.queries import decode_decision_cursor, encode_decision_cursor
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.unit_of_work import UnitOfWork

from .adr import draft_from_evolution, to_markdown
from .entities import Decision, DecisionStatus
from .errors import DecisionNotFound, InvalidDecision

MAX_PAGE = 100


class DecisionService:
    def __init__(self, uow: UnitOfWork, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._clock = clock

    async def draft(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        candidate_ids: tuple[str, ...] | None = None,
        title: str | None = None,
    ) -> Decision:
        """A proposed decision with the analysis's candidates (or those named) as options."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_EVOLVE, lock=ProjectLock.EXCLUSIVE
            )
            architecture = await uow.architectures.get(project_id, architecture_id)
            if architecture is None:
                raise ArchitectureNotFound
            architecture.ensure_modifiable()
            report = await uow.evolution.get(project_id, architecture.id, analysis_id)
            if report is None:
                raise EvolutionAnalysisNotFound
            if report.model_set is None:
                raise InvalidDecision(details={"field": "analysis_id", "reason": "no_result"})
            result = report.result(await uow.evolution.candidates(project_id, report.analysis.id))
            decision = draft_from_evolution(
                decision_id=uuid.uuid7(),
                project_id=project_id,
                number=await uow.decisions.next_number(project_id),
                analysis_id=report.analysis.id,
                result=result,
                created_by=user_id,
                at=self._clock(),
                candidate_ids=candidate_ids,
                title=title,
            )
            await uow.decisions.add(decision)
            await self._audit(uow, access, AuditAction.DECISION_PROPOSED, user_id, decision)
        return decision

    async def get(self, *, project_id: uuid.UUID, decision_id: uuid.UUID, user_id: uuid.UUID) -> Decision:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _decision(uow, project_id, decision_id)

    async def document(self, *, project_id: uuid.UUID, decision_id: uuid.UUID, user_id: uuid.UUID) -> str:
        return to_markdown(await self.get(project_id=project_id, decision_id=decision_id, user_id=user_id))

    async def list_decisions(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        architecture_id: uuid.UUID | None = None,
        status: DecisionStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[Decision]:
        size = max(1, min(limit, MAX_PAGE))
        after = decode_decision_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.decisions.list_for_project(
                project_id, architecture_id=architecture_id, status=status, after=after, limit=size + 1
            )
        items, more = rows[:size], len(rows) > size
        return pagination.Page(
            items=items, next_cursor=encode_decision_cursor(items[-1].number) if more else None
        )

    async def accept(
        self,
        *,
        project_id: uuid.UUID,
        decision_id: uuid.UUID,
        user_id: uuid.UUID,
        candidate_id: str,
        rationale: str,
    ) -> Decision:
        """A person chose ``candidate_id``. Nothing in the architecture changes."""
        async with self._uow as uow:
            access, decision = await self._writable(uow, project_id, decision_id, user_id)
            decided = decision.accept(candidate_id, rationale, user_id, self._clock())
            await uow.decisions.update(decided)
            await self._audit(uow, access, AuditAction.DECISION_ACCEPTED, user_id, decided)
        return decided

    async def reject(
        self, *, project_id: uuid.UUID, decision_id: uuid.UUID, user_id: uuid.UUID, rationale: str
    ) -> Decision:
        async with self._uow as uow:
            access, decision = await self._writable(uow, project_id, decision_id, user_id)
            decided = decision.reject(rationale, user_id, self._clock())
            await uow.decisions.update(decided)
            await self._audit(uow, access, AuditAction.DECISION_REJECTED, user_id, decided)
        return decided

    async def supersede(
        self, *, project_id: uuid.UUID, decision_id: uuid.UUID, user_id: uuid.UUID, by_decision_id: uuid.UUID
    ) -> Decision:
        """Replaced by ``by_decision_id``: an accepted decision of the same project."""
        async with self._uow as uow:
            access, decision = await self._writable(uow, project_id, decision_id, user_id)
            successor = await uow.decisions.get(project_id, by_decision_id)
            if successor is None:
                raise DecisionNotFound
            if successor.status is not DecisionStatus.ACCEPTED:
                raise InvalidDecision(details={"field": "by_decision_id", "reason": "not_accepted"})
            superseded = decision.supersede(successor.id)
            await uow.decisions.update(superseded)
            await self._audit(uow, access, AuditAction.DECISION_SUPERSEDED, user_id, superseded)
        return superseded

    async def link_revision(
        self, *, project_id: uuid.UUID, decision_id: uuid.UUID, user_id: uuid.UUID, revision_number: int
    ) -> Decision:
        """A person states that ``revision_number`` of the decision's architecture implements it."""
        async with self._uow as uow:
            access, decision = await self._writable(uow, project_id, decision_id, user_id)
            revision = await uow.architectures.get_revision(decision.architecture_id, revision_number)
            if revision is None:
                raise ArchitectureRevisionNotFound
            linked = decision.link_revision(revision.number, revision.content_hash, user_id, self._clock())
            await uow.decisions.update(linked)
            await self._audit(uow, access, AuditAction.DECISION_REVISION_LINKED, user_id, linked)
        return linked

    async def _writable(
        self, uow: UnitOfWork, project_id: uuid.UUID, decision_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[ProjectAccess, Decision]:
        access = await project_access(
            uow, project_id, user_id, Permission.ARCHITECTURE_EVOLVE, lock=ProjectLock.SHARE
        )
        decision = await _decision(uow, project_id, decision_id)
        architecture = await uow.architectures.get(project_id, decision.architecture_id)
        if architecture is None:
            raise DecisionNotFound  # its architecture was deleted: the decision is no longer reachable
        architecture.ensure_modifiable()
        return access, decision

    @staticmethod
    async def _audit(
        uow: UnitOfWork, access: ProjectAccess, action: AuditAction, user_id: uuid.UUID, decision: Decision
    ) -> None:
        metadata: dict[str, object] = {
            "project_id": str(decision.project_id),
            "architecture_id": str(decision.architecture_id),
            "decision": decision.reference,
            "status": decision.status.value,
            "options": len(decision.options),
        }
        if decision.source is not None:
            metadata["analysis_id"] = str(decision.source.analysis_id)
        if decision.chosen_option is not None:
            metadata["chosen_option"] = decision.chosen_option
        if decision.resulting_revision is not None:
            metadata["resulting_revision"] = decision.resulting_revision.number
        await uow.audit.record(
            AuditEvent(
                action,
                actor_user_id=user_id,
                organization_id=access.project.organization_id,
                resource_type="decision",
                resource_id=decision.id,
                metadata=metadata,
            )
        )


async def _decision(uow: UnitOfWork, project_id: uuid.UUID, decision_id: uuid.UUID) -> Decision:
    decision = await uow.decisions.get(project_id, decision_id)
    if decision is None:
        raise DecisionNotFound
    return decision
