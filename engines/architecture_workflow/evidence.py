"""A candidate's engine results as evolution evidence, without storing an analysis.

The evolution rules read ``StoredAnalysis`` records: an engine's findings, verdicts and facts for one
exact architecture content. A workflow candidate's analyses are never stored as project analyses, so
each in-memory result is turned into the same record **through the engines' own report factories and
evolution's own builders** (``from_validation``, ``from_reliability``…) — nothing is re-derived here.

Each transient analysis is identified as the candidate (``analyzed_as`` its workflow, numbered by its
ordinal, with its content hash), so the rules' evidence is current for exactly that candidate and
stale for any other. Its id is derived from the candidate and the engine: the same results always give
the same evidence.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.domain.evolution.evidence import (
    StoredAnalysis,
    from_observability,
    from_reliability,
    from_security,
    from_validation,
)
from core.domain.evolution.values import EvidenceSource
from core.domain.observability.analyses import PENDING as OBSERVABILITY_PENDING
from core.domain.observability.analyses import ObservabilityAnalysis
from core.domain.observability.reports import ObservabilityReport
from core.domain.observability.results import ObservabilityResult
from core.domain.reliability.analyses import PENDING as RELIABILITY_PENDING
from core.domain.reliability.analyses import ReliabilityAnalysis
from core.domain.reliability.reports import ReliabilityReport
from core.domain.reliability.results import ReliabilityResult
from core.domain.security.analyses import PENDING as SECURITY_PENDING
from core.domain.security.analyses import SecurityAnalysis
from core.domain.security.reports import SecurityReport
from core.domain.security.results import SecurityResult
from core.domain.validation.results import ValidationResult
from core.domain.validation.runs import RunInputs, RunReport, RunStatus, ValidationRun

_NAMESPACE = uuid.UUID("6f2b0d4e-8c1a-4f6e-9b7d-3a5c2e1f0a9b")


@dataclass(frozen=True, slots=True)
class AnalyzedCandidate:
    """The identity a candidate is analyzed under: its workflow, its ordinal, its content."""

    candidate_id: uuid.UUID
    project_id: uuid.UUID
    analyzed_as: uuid.UUID  # the workflow
    number: int  # the candidate's ordinal
    content_hash: str


@dataclass(frozen=True, slots=True)
class CandidateResults:
    """The engines' in-memory results for one candidate; ``None`` when an engine did not evaluate it."""

    validation: ValidationResult | None = None
    reliability: ReliabilityResult | None = None
    security: SecurityResult | None = None
    observability: ObservabilityResult | None = None


def _identity(candidate: AnalyzedCandidate, engine: str, at: datetime) -> dict[str, Any]:
    return {
        "id": uuid.uuid5(_NAMESPACE, f"{candidate.candidate_id}:{engine}"),
        "project_id": candidate.project_id,
        "architecture_id": candidate.analyzed_as,
        "revision_number": candidate.number,
        "revision_content_hash": candidate.content_hash,
        "requested_by_user_id": None,
        "requested_at": at,
    }


def candidate_evidence(
    candidate: AnalyzedCandidate, results: CandidateResults, at: datetime
) -> dict[EvidenceSource, StoredAnalysis]:
    """Evolution evidence for each engine that evaluated the candidate (the others are absent: no
    trigger is drawn from an analysis that did not run)."""
    found: dict[EvidenceSource, StoredAnalysis] = {}
    if results.validation is not None:
        identity = _identity(candidate, "validation", at)
        run = ValidationRun(**identity, profile="default", status=RunStatus.PENDING)
        report = RunReport.of(run.start(at).complete(results.validation, at), RunInputs())
        found[EvidenceSource.VALIDATION] = from_validation(report, results.validation.findings)
    if results.reliability is not None:
        reliability = ReliabilityAnalysis(
            **_identity(candidate, "reliability", at), status=RELIABILITY_PENDING
        )
        finished = reliability.start(at).finish(results.reliability, at)
        found[EvidenceSource.RELIABILITY] = from_reliability(
            ReliabilityReport.of(finished, {}), results.reliability.findings
        )
    if results.security is not None:
        security = SecurityAnalysis(**_identity(candidate, "security", at), status=SECURITY_PENDING)
        done = security.start(at).finish(results.security, at)
        found[EvidenceSource.SECURITY] = from_security(SecurityReport.of(done, {}), results.security.findings)
    if results.observability is not None:
        identity = _identity(candidate, "observability", at)
        observability = ObservabilityAnalysis(**identity, status=OBSERVABILITY_PENDING)
        observed = observability.start(at).finish(results.observability, at)
        found[EvidenceSource.OBSERVABILITY] = from_observability(
            ObservabilityReport.of(observed, {}), results.observability.findings
        )
    return found
