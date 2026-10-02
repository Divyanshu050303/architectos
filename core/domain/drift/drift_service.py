"""Drift use cases: analyze an exact baseline revision against a stored discovery run, list and read
analyses and their findings, follow drift items and record a person's review of them, confirm
identity mappings.

Analyzing is synchronous, in three steps like the other analyses: read and authorize every input in
a short transaction — the architecture and its exact revision, the discovery run (of the same
project, with a result), the run the baseline was accepted from (if any), the confirmed identity
mappings and the other engines' stored analyses of the baseline — then compare (on a worker thread,
no transaction: every input is immutable), then store the analysis, fold its findings into the
architecture's drift items under the architecture's lock, and audit. An engine refusal is stored as a
failed analysis. **Nothing here changes an architecture, a revision or a discovery run.**

Access: analyzing, reviewing and confirming identities need ``architecture.drift``; reading needs
``architecture.read``. Every lookup is scoped by project — an analysis, item, run, revision or linked
record of another project or tenant is not found. Audit entries carry identifiers and counts only.
"""

import asyncio
import uuid
from collections.abc import Mapping
from datetime import datetime

from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain import pagination
from core.domain.architecture.entities import Architecture
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.discovery.errors import DiscoveryRunNotFound
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.runs import DiscoveryRun
from core.domain.migrations.loading import load_evidence
from core.domain.organizations.permissions import Permission
from core.domain.projects.entities import ProjectAccess
from core.domain.projects.repository import ProjectLock
from core.domain.requirements.access import project_access
from core.domain.unit_of_work import UnitOfWork

from .analyses import AnalysisError, BaselineRef, DriftAnalysis, DriftRequest, ObservedRef
from .errors import (
    DriftAnalysisNotFound,
    DriftItemNotFound,
    InvalidDriftRequest,
    InvalidDriftResult,
    InvalidReviewAction,
)
from .findings import DriftFinding
from .identity import IdentityMapping, current
from .items import DriftItem, Link
from .ports import DriftEngine, DriftInputs
from .review import correlate, resolution_problem
from .values import AnalysisStatus, Classification, FindingType, LinkKind, ReviewAction, ReviewStatus

MAX_PAGE = 100
_ANALYSES = "drift_analyses"
_ITEMS = "drift_items"


def encode_analysis_cursor(requested_at: datetime, analysis_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_ANALYSES, requested_at.isoformat(), str(analysis_id)])


def decode_analysis_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    sort, requested_at, analysis_id = pagination.decode_cursor(raw, length=3)
    if sort != _ANALYSES:
        raise pagination.InvalidCursor
    try:
        return datetime.fromisoformat(requested_at), uuid.UUID(analysis_id)
    except ValueError:
        raise pagination.InvalidCursor from None


def encode_item_cursor(item_id: uuid.UUID) -> str:
    return pagination.encode_cursor([_ITEMS, str(item_id)])


def decode_item_cursor(raw: str) -> uuid.UUID:
    sort, item_id = pagination.decode_cursor(raw, length=2)
    if sort != _ITEMS:
        raise pagination.InvalidCursor
    try:
        return uuid.UUID(item_id)
    except ValueError:
        raise pagination.InvalidCursor from None


class DriftService:
    def __init__(self, uow: UnitOfWork, engine: DriftEngine, *, clock: Clock = utc_now) -> None:
        self._uow = uow
        self._engine = engine
        self._clock = clock

    # --- analyzing -------------------------------------------------------------------------------

    async def analyze(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, request: DriftRequest
    ) -> DriftAnalysis:
        """The baseline revision compared with the discovery run, stored with its findings, which are
        folded into the architecture's drift items. Neither side is changed."""
        inputs = await self._inputs(project_id, user_id, request)
        now = self._clock()
        analysis = DriftAnalysis(uuid.uuid7(), project_id, request, AnalysisStatus.PENDING, user_id, now)
        analysis = analysis.start(now)
        try:
            result = await asyncio.to_thread(self._engine.run, inputs)
            analysis = analysis.finish(result, self._clock())
        except (InvalidDriftResult, InvalidDriftRequest) as error:
            analysis = analysis.fail(AnalysisError(error.code, error.message), self._clock())
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_DRIFT, lock=ProjectLock.SHARE
            )
            await _architecture(uow, project_id, request.architecture_id, for_update=True)  # serializes items
            await uow.drift.add_analysis(analysis)
            items = await uow.drift.items_of(project_id, request.architecture_id, for_update=True)
            correlation = correlate(items, analysis, self._clock())
            if correlation.created:
                await uow.drift.add_items(correlation.created)
            for item in correlation.updated:
                await uow.drift.save_item(item)
            facts = {"items_opened": len(correlation.created), "items_detected": len(correlation.updated)}
            await _audit_analysis(uow, access, user_id, analysis, facts)
        return analysis

    async def _inputs(self, project_id: uuid.UUID, user_id: uuid.UUID, request: DriftRequest) -> DriftInputs:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_DRIFT)
            architecture = await _architecture(uow, project_id, request.architecture_id)
            revision = await uow.architectures.get_revision(architecture.id, request.baseline_revision)
            if revision is None:
                raise ArchitectureRevisionNotFound
            run = await uow.discoveries.get(project_id, request.discovery_run_id)
            if run is None:
                raise DiscoveryRunNotFound
            if run.result is None:
                raise InvalidDriftRequest(details={"field": "discovery_run_id", "reason": "no_result"})
            accepted = await uow.discoveries.accepted_for(project_id, architecture.id)
            source = _source_of(accepted, architecture.id, revision.number)
            mappings = current(tuple(await uow.drift.mappings(project_id, architecture.id)))
            evidence = await load_evidence(uow, project_id, architecture.id, (revision.number,))
        stored = revision.snapshot.get("schema_version") if revision.snapshot else None
        schema = stored if isinstance(stored, int) else IR_SCHEMA_VERSION
        result = run.result
        observed = ObservedRef(
            run.id, result.fingerprint, result.sources_fingerprint, result.version, result.extractors
        )
        return DriftInputs(
            request=request,
            baseline=BaselineRef(architecture.id, revision.number, revision.content_hash, schema),
            baseline_ir=revision.ir,
            baseline_created_at=revision.created_at,
            latest_revision=architecture.current_revision,
            baseline_source=source,
            observed=observed,
            observed_result=result,
            observed_at=run.requested_at,
            mappings=mappings,
            evidence=evidence,
        )

    # --- reading ---------------------------------------------------------------------------------

    async def get(
        self, *, project_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID
    ) -> DriftAnalysis:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _analysis(uow, project_id, analysis_id)

    async def list_analyses(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        architecture_id: uuid.UUID | None = None,
        status: AnalysisStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[DriftAnalysis]:
        """Newest first — an architecture's drift history when ``architecture_id`` is given."""
        size = max(1, min(limit, MAX_PAGE))
        after = decode_analysis_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.drift.list_analyses(
                project_id, architecture_id=architecture_id, status=status, after=after, limit=size + 1
            )
        page, more = rows[:size], len(rows) > size
        last = page[-1] if more and page else None
        return pagination.Page(page, encode_analysis_cursor(last.requested_at, last.id) if last else None)

    async def findings(
        self,
        *,
        project_id: uuid.UUID,
        analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        classification: Classification | None = None,
        finding_type: FindingType | None = None,
    ) -> tuple[DriftFinding, ...]:
        found = await self.get(project_id=project_id, analysis_id=analysis_id, user_id=user_id)
        findings = found.result.findings if found.result else ()
        return tuple(
            f
            for f in findings
            if (classification is None or f.classification is classification)
            and (finding_type is None or f.type is finding_type)
        )

    async def finding(
        self, *, project_id: uuid.UUID, analysis_id: uuid.UUID, finding_id: str, user_id: uuid.UUID
    ) -> DriftFinding:
        for found in await self.findings(project_id=project_id, analysis_id=analysis_id, user_id=user_id):
            if found.id == finding_id:
                return found
        raise DriftAnalysisNotFound  # a finding of another analysis is not found either

    # --- items and review ------------------------------------------------------------------------

    async def list_items(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        architecture_id: uuid.UUID | None = None,
        status: ReviewStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> pagination.Page[DriftItem]:
        size = max(1, min(limit, MAX_PAGE))
        after = decode_item_cursor(cursor) if cursor else None
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            rows = await uow.drift.list_items(
                project_id, architecture_id=architecture_id, status=status, after=after, limit=size + 1
            )
        page, more = rows[:size], len(rows) > size
        return pagination.Page(page, encode_item_cursor(page[-1].id) if more and page else None)

    async def get_item(self, *, project_id: uuid.UUID, item_id: uuid.UUID, user_id: uuid.UUID) -> DriftItem:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            return await _item(uow, project_id, item_id)

    async def review(
        self,
        *,
        project_id: uuid.UUID,
        item_id: uuid.UUID,
        user_id: uuid.UUID,
        action: ReviewAction,
        note: str | None = None,
        link: Link | None = None,
        evidence_analysis_id: uuid.UUID | None = None,
    ) -> DriftItem:
        """A person's review action on an item: recorded, audited — never an architecture change."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_DRIFT, lock=ProjectLock.SHARE
            )
            item = await _item(uow, project_id, item_id, for_update=True)
            if link is not None:
                await _check_link(uow, project_id, item, link)
            if evidence_analysis_id is not None:
                problem = resolution_problem(item, await _analysis(uow, project_id, evidence_analysis_id))
                if problem is not None:
                    details = {"action": action.value, "status": item.status.value, "reason": problem}
                    raise InvalidReviewAction(details=details)
            at = self._clock()
            reviewed = item.act(
                action, user_id, at, note=note, link=link, evidence_analysis_id=evidence_analysis_id
            )
            await uow.drift.save_item(reviewed)
            await _audit_review(uow, access, user_id, reviewed, link, evidence_analysis_id)
        return reviewed

    # --- identity mappings -----------------------------------------------------------------------

    async def confirm_identity(
        self,
        *,
        project_id: uuid.UUID,
        architecture_id: uuid.UUID,
        user_id: uuid.UUID,
        baseline_id: str,
        discovered_key: str | None,
        note: str | None = None,
    ) -> IdentityMapping:
        """A person's statement that a node of the architecture and a discovered entity are the same
        (``discovered_key`` None retracts it). Used for matching only."""
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.ARCHITECTURE_DRIFT, lock=ProjectLock.SHARE
            )
            architecture = await _architecture(uow, project_id, architecture_id)
            revision = await uow.architectures.get_revision(architecture.id, architecture.current_revision)
            if revision is None or revision.ir.node(baseline_id) is None:
                raise InvalidDriftRequest(details={"field": "baseline_id", "reason": "not_a_node"})
            mapping = IdentityMapping(
                architecture.id, baseline_id, discovered_key, user_id, self._clock(), note
            )
            await uow.drift.add_mapping(project_id, mapping)
            await uow.audit.record(
                AuditEvent(
                    AuditAction.DRIFT_IDENTITY_CONFIRMED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="architecture",
                    resource_id=architecture.id,
                    metadata={"project_id": str(project_id), "retracted": discovered_key is None},
                )
            )
        return mapping

    async def mappings(
        self, *, project_id: uuid.UUID, architecture_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[IdentityMapping, ...]:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.ARCHITECTURE_READ)
            architecture = await _architecture(uow, project_id, architecture_id)
            return tuple(await uow.drift.mappings(project_id, architecture.id))


def _source_of(
    runs: tuple[DiscoveryRun, ...], architecture_id: uuid.UUID, revision: int
) -> DiscoveryResult | None:
    """The run whose acceptance produced the latest revision at or before the baseline."""
    accepted = [
        (a.revision_number, run.result)
        for run in runs
        for a in run.acceptances
        if a.architecture_id == architecture_id and a.revision_number <= revision and run.result is not None
    ]
    return max(accepted, key=lambda pair: pair[0])[1] if accepted else None


async def _architecture(
    uow: UnitOfWork, project_id: uuid.UUID, architecture_id: uuid.UUID, *, for_update: bool = False
) -> Architecture:
    architecture = await uow.architectures.get(project_id, architecture_id, for_update=for_update)
    if architecture is None:
        raise ArchitectureNotFound
    return architecture


async def _analysis(uow: UnitOfWork, project_id: uuid.UUID, analysis_id: uuid.UUID) -> DriftAnalysis:
    found = await uow.drift.get_analysis(project_id, analysis_id)
    if found is None:
        raise DriftAnalysisNotFound
    return found


async def _item(
    uow: UnitOfWork, project_id: uuid.UUID, item_id: uuid.UUID, *, for_update: bool = False
) -> DriftItem:
    found = await uow.drift.get_item(project_id, item_id, for_update=for_update)
    if found is None:
        raise DriftItemNotFound
    return found


def _uuid(target: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(target)
    except ValueError:
        return None


async def _check_link(uow: UnitOfWork, project_id: uuid.UUID, item: DriftItem, link: Link) -> None:
    """The linked record exists in this project — never another tenant's."""
    target = _uuid(link.target)
    exists: object = None
    if link.kind is LinkKind.DECISION and target:
        exists = await uow.decisions.get(project_id, target)
    elif link.kind is LinkKind.MIGRATION_PLAN and target:
        exists = await uow.migrations.get(project_id, target)
    elif link.kind is LinkKind.EVOLUTION_ANALYSIS and target:
        exists = await uow.evolution.get(project_id, item.architecture_id, target)
    elif link.kind is LinkKind.REVISION and link.architecture_id and link.target.isdigit():
        architecture = await uow.architectures.get(project_id, link.architecture_id)
        if architecture is not None:
            exists = await uow.architectures.get_revision(architecture.id, int(link.target))
    if not exists:
        details = {
            "action": ReviewAction.LINK.value,
            "status": item.status.value,
            "reason": "link_target_not_found",
        }
        raise InvalidReviewAction(details=details)


async def _audit_review(
    uow: UnitOfWork,
    access: ProjectAccess,
    user_id: uuid.UUID,
    item: DriftItem,
    link: Link | None,
    evidence_analysis_id: uuid.UUID | None,
) -> None:
    event = item.history[-1]
    await uow.audit.record(
        AuditEvent(
            AuditAction.DRIFT_ITEM_REVIEWED,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="drift_item",
            resource_id=item.id,
            metadata={
                "project_id": str(item.project_id),
                "architecture_id": str(item.architecture_id),
                "action": event.action.value,
                "previous_status": event.previous.value,
                "status": event.status.value,
                "link_kind": link.kind.value if link else None,
                "evidence_analysis_id": str(evidence_analysis_id) if evidence_analysis_id else None,
            },
        )
    )


async def _audit_analysis(
    uow: UnitOfWork,
    access: ProjectAccess,
    user_id: uuid.UUID,
    analysis: DriftAnalysis,
    facts: Mapping[str, object],
) -> None:
    result = analysis.result
    metadata: dict[str, object] = {
        "project_id": str(analysis.project_id),
        "architecture_id": str(analysis.request.architecture_id),
        "baseline_revision": analysis.request.baseline_revision,
        "discovery_run_id": str(analysis.request.discovery_run_id),
        "status": analysis.status.value,
        "compatibility": result.status.value if result else None,
        "findings": len(result.findings) if result else 0,
    }
    await uow.audit.record(
        AuditEvent(
            AuditAction.DRIFT_ANALYSIS_CREATED,
            actor_user_id=user_id,
            organization_id=access.project.organization_id,
            resource_type="drift_analysis",
            resource_id=analysis.id,
            metadata=metadata | dict(facts),
        )
    )
