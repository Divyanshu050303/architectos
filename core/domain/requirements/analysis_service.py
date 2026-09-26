"""Requirement analysis use cases: analyze a person's text, read an analysis, promote candidates.

Analyzing never creates requirements. It stores an append-only record (the raw input exactly as
written, the engine version and the engine's result) and nothing else. Only **promotion**, a
person's explicit choice of candidates, creates requirements: always drafts, always validated like
any other creation, always with their origin, and never twice (promoting an already promoted
candidate returns the requirement it became).

The engine is reached through the ``RequirementsAnalyzer`` port and runs **outside** any database
transaction: it may be slow (a language model, later) and must never hold a project lock. Access is
checked before it runs and again, with the project locked, when the result is stored.
"""

import time
import uuid
from dataclasses import dataclass

from core.domain.audit.entities import AuditAction, AuditEvent
from core.domain.clock import Clock, utc_now
from core.domain.metrics import Metrics, NullMetrics
from core.domain.organizations.permissions import Permission
from core.domain.projects.repository import ProjectLock
from core.domain.unit_of_work import UnitOfWork

from .access import project_access
from .analyses import (
    AnalyzerOutput,
    NewRequirementAnalysis,
    RequirementAnalysis,
    RequirementsAnalyzer,
    validate_raw_input,
)
from .analysis import ANALYZED_STATUSES
from .candidates import RequirementCandidate
from .entities import Requirement
from .errors import CandidateAlreadyPromoted, InvalidPromotion, RequirementAnalysisNotFound
from .requirement_service import MAX_ANALYZED_REQUIREMENTS

MAX_PROMOTIONS = 100


@dataclass(frozen=True, slots=True)
class Promotion:
    candidate_key: str
    requirement: Requirement
    created: bool  # False: it had already been promoted (a retry)


class RequirementAnalysisService:
    def __init__(
        self,
        uow: UnitOfWork,
        analyzer: RequirementsAnalyzer,
        *,
        clock: Clock = utc_now,
        metrics: Metrics | None = None,
    ) -> None:
        self._uow = uow
        self._analyzer = analyzer
        self._clock = clock
        self._metrics: Metrics = metrics or NullMetrics()

    async def analyze(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, raw_input: str
    ) -> RequirementAnalysis:
        raw = validate_raw_input(raw_input)
        async with self._uow as uow:
            access = await project_access(uow, project_id, user_id, Permission.REQUIREMENT_CREATE)
            access.project.ensure_modifiable()
            existing = await uow.requirements.list_by_status(
                project_id, ANALYZED_STATUSES, limit=MAX_ANALYZED_REQUIREMENTS
            )
        started = time.monotonic()
        output = await self._analyzer.analyze(raw, existing)  # no transaction, no lock held
        self._record(output, time.monotonic() - started)
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.REQUIREMENT_CREATE, lock=ProjectLock.SHARE
            )
            stored = await uow.requirement_analyses.add(
                NewRequirementAnalysis(project_id, raw, output.engine_version, output.result, user_id)
            )
            await uow.audit.record(
                AuditEvent(
                    AuditAction.REQUIREMENT_ANALYSIS_CREATED,
                    actor_user_id=user_id,
                    organization_id=access.project.organization_id,
                    resource_type="requirement_analysis",
                    resource_id=stored.id,
                    metadata={
                        "project_id": str(project_id),
                        "engine_version": output.engine_version,
                        "candidates": output.candidate_count,
                        "blocking": output.blocking_count,
                        "ready_for_architecture": output.ready_for_architecture,
                        "input_characters": len(raw),
                    },
                )
            )
        return stored

    def _record(self, output: AnalyzerOutput, seconds: float) -> None:
        """Counts and identifiers only: the requirement text never reaches a metric."""
        m = self._metrics
        m.increment("requirements.analyze")
        m.observe("requirements.analyze.duration_ms", round(seconds * 1000, 1))
        m.increment("requirements.extracted", output.candidate_count)
        if output.ambiguity_count:
            m.increment("requirements.ambiguous")
        if output.conflict_count:
            m.increment("requirements.conflicting")
        if output.completeness_status != "complete":
            m.increment("requirements.incomplete", status=output.completeness_status)
        if output.ready_for_architecture:
            m.increment("requirements.ready")
        if output.semantic_status is not None and output.semantic_source is not None:
            source = output.semantic_source
            if output.semantic_status == "ok":
                m.increment("requirements.llm.calls", source=source)
            else:
                m.increment("requirements.llm_failure", source=source, reason=output.semantic_status)
            m.observe("requirements.llm.input_tokens", output.input_tokens, source=source)
            m.observe("requirements.llm.output_tokens", output.output_tokens, source=source)
            m.observe("requirements.llm.latency_ms", output.llm_latency_ms, source=source)

    async def get(
        self, *, project_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID
    ) -> RequirementAnalysis:
        async with self._uow as uow:
            await project_access(uow, project_id, user_id, Permission.REQUIREMENT_READ)
            found = await uow.requirement_analyses.get(project_id, analysis_id)
        if found is None:
            raise RequirementAnalysisNotFound
        return found

    async def promote(
        self, *, project_id: uuid.UUID, analysis_id: uuid.UUID, user_id: uuid.UUID, candidate_keys: list[str]
    ) -> list[Promotion]:
        """Each chosen candidate becomes a draft requirement with its origin; idempotent."""
        _check_selection(candidate_keys)
        async with self._uow as uow:
            access = await project_access(
                uow, project_id, user_id, Permission.REQUIREMENT_CREATE, lock=ProjectLock.EXCLUSIVE
            )
            analysis = await uow.requirement_analyses.get(project_id, analysis_id)
            if analysis is None:
                raise RequirementAnalysisNotFound
            stored = {c["key"]: c for c in analysis.result.get("candidates", [])}
            promotions: list[Promotion] = []
            for key in candidate_keys:
                if key not in stored:
                    raise InvalidPromotion(details={"reason": "unknown_candidate", "candidate_key": key})
                candidate = RequirementCandidate.from_dict(stored[key])
                new = candidate.to_new_requirement(
                    project_id=project_id, created_by_user_id=user_id, analysis_id=analysis_id
                )
                try:
                    requirement = await uow.requirements.add(new)
                except CandidateAlreadyPromoted as already:
                    existing = await uow.requirements.get(
                        project_id, uuid.UUID(already.details["requirement_id"])
                    )
                    assert existing is not None  # noqa: S101 - found live by the unique index
                    promotions.append(Promotion(key, existing, created=False))
                    continue
                promotions.append(Promotion(key, requirement, created=True))
                self._metrics.increment("requirements.promoted")
                await uow.audit.record(
                    AuditEvent(
                        AuditAction.REQUIREMENT_PROMOTED,
                        actor_user_id=user_id,
                        organization_id=access.project.organization_id,
                        resource_type="requirement",
                        resource_id=requirement.id,
                        metadata={
                            "project_id": str(project_id),
                            "reference": requirement.reference,
                            "version": requirement.version,
                            "analysis_id": str(analysis_id),
                            "candidate_key": key,
                            "source": requirement.source.value,
                        },
                    )
                )
        return promotions


def _check_selection(candidate_keys: list[str]) -> None:
    if not candidate_keys:
        raise InvalidPromotion(details={"reason": "empty"})
    if len(candidate_keys) > MAX_PROMOTIONS:
        raise InvalidPromotion(details={"reason": "too_many"})
    seen: set[str] = set()
    for key in candidate_keys:
        if key in seen:
            raise InvalidPromotion(details={"reason": "duplicate", "candidate_key": key})
        seen.add(key)
