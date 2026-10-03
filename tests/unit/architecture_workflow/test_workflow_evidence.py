"""A candidate's in-memory engine results as evolution evidence (Autonomous Architecture Workflow,
phase 2): through the engines' own report factories and evolution's own builders, identified as the
candidate (its content hash), deterministic, and absent for an engine that did not run."""

import uuid
from datetime import UTC, datetime

from core.architecture_ir.serialization import content_hash
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.evolution.values import EvidenceSource
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.validation.options import RevisionInfo, ValidationConfig
from engines.architecture_workflow.evidence import AnalyzedCandidate, CandidateResults, candidate_evidence
from engines.observability.service import DeterministicObservabilityEngine
from engines.reliability.service import DeterministicReliabilityEngine
from engines.security.service import DeterministicSecurityEngine
from engines.validation.service import DeterministicValidationEngine
from persistence.component_catalog import default_catalog
from tests.unit.architecture_diff.test_diff_impact import shop

AT = datetime(2026, 10, 3, tzinfo=UTC)
WORKFLOW = uuid.uuid4()


def analyzed() -> tuple[AnalyzedCandidate, CandidateResults]:
    ir = shop(db={"exposure": "public"})
    candidate = AnalyzedCandidate(uuid.UUID(int=7), uuid.uuid4(), WORKFLOW, 1, content_hash(ir))
    revision = RevisionInfo(str(WORKFLOW), 1, candidate.content_hash, IR_SCHEMA_VERSION)
    policy = ArchitecturePolicy()
    results = CandidateResults(
        DeterministicValidationEngine(catalog=default_catalog()).validate(
            ir, revision, requirements=(), policy=None, config=ValidationConfig()
        ),
        DeterministicReliabilityEngine().analyze(ir, revision, ReliabilityAnalysisRequest(WORKFLOW, 1), ()),
        DeterministicSecurityEngine().analyze(ir, revision, SecurityAnalysisRequest(WORKFLOW, 1), policy, ()),
        DeterministicObservabilityEngine().analyze(
            ir, revision, ObservabilityAnalysisRequest(WORKFLOW, 1), policy, ()
        ),
    )
    return candidate, results


def test_each_engines_results_become_current_evidence_for_the_candidate() -> None:
    candidate, results = analyzed()
    evidence = candidate_evidence(candidate, results, AT)
    assert set(evidence) == {
        EvidenceSource.VALIDATION,
        EvidenceSource.RELIABILITY,
        EvidenceSource.SECURITY,
        EvidenceSource.OBSERVABILITY,
    }
    for analysis in evidence.values():
        assert (analysis.revision_number, analysis.content_hash) == (1, candidate.content_hash)
    security = evidence[EvidenceSource.SECURITY]
    assert results.security is not None
    assert {i.item for i in security.items} == {f.id for f in results.security.findings}
    assert security.items, "a public database is something the security engine reports"


def test_the_same_results_give_the_same_evidence() -> None:
    candidate, results = analyzed()
    assert candidate_evidence(candidate, results, AT) == candidate_evidence(candidate, results, AT)


def test_an_engine_that_did_not_run_gives_no_evidence() -> None:
    candidate, results = analyzed()
    only = CandidateResults(validation=results.validation)
    assert set(candidate_evidence(candidate, only, AT)) == {EvidenceSource.VALIDATION}
