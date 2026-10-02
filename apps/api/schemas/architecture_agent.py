"""The architecture agent over HTTP, typed field by field.

A run proposes an architecture for a person to review: the proposal is the model's (every claim with
what it rests on), the candidate is canonical IR built and checked deterministically, and the
reports are the engines' own. Nothing here is an architecture until a person accepts it. What is not
known is ``null`` — token counts a provider did not report, cost (no pricing is configured) — never 0.
Prompts, retrieved text and the model's raw output are never returned (they are not stored).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from core.architecture_ir.serialization import to_dict
from core.domain.architecture_agent.agent_service import AcceptedCandidate, validation_blocks
from core.domain.architecture_agent.proposals import (
    MAX_STATEMENT,
    Claim,
    ClarificationQuestion,
    DesignDecision,
    Proposal,
    ProposedConnection,
    ProposedNode,
)
from core.domain.architecture_agent.repository import RunListing
from core.domain.architecture_agent.requests import (
    MAX_CONTEXT,
    MAX_ITEM,
    MAX_ITEMS,
    MAX_OBJECTIVE,
    AgentRequest,
    BaseRevision,
    Budget,
)
from core.domain.architecture_agent.results import Candidate, EngineReport
from core.domain.architecture_agent.runs import AgentRun
from core.domain.architecture_agent.values import (
    Basis,
    EngineStatus,
    FailureCode,
    QuestionKind,
    RunStatus,
    Stage,
)

from .common import ApiModel, RequestModel

type Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type Item = Annotated[str, Field(min_length=1, max_length=MAX_ITEM)]
DEFAULTS = Budget()


def _confidence(value: object) -> str | None:
    return None if value is None else str(value)


# --- requests ------------------------------------------------------------------------------------


class BaseRevisionModel(RequestModel):
    architecture_id: uuid.UUID
    revision_number: int = Field(ge=1, description="The exact revision the iteration starts from.")


class BudgetModel(RequestModel):
    """Lowers the run's limits; never raises them above the defaults."""

    max_model_calls: int = Field(default=DEFAULTS.max_model_calls, ge=1, le=DEFAULTS.max_model_calls)
    max_input_tokens: int = Field(default=DEFAULTS.max_input_tokens, ge=1000, le=DEFAULTS.max_input_tokens)
    max_output_tokens: int = Field(default=DEFAULTS.max_output_tokens, ge=256, le=DEFAULTS.max_output_tokens)
    max_seconds: float = Field(default=DEFAULTS.max_seconds, gt=5, le=DEFAULTS.max_seconds)
    max_passages: int = Field(default=DEFAULTS.max_passages, ge=0, le=DEFAULTS.max_passages)
    max_context_chars: int = Field(default=DEFAULTS.max_context_chars, ge=1000, le=DEFAULTS.max_context_chars)

    def to_domain(self) -> Budget:
        return Budget(
            self.max_model_calls,
            self.max_input_tokens,
            self.max_output_tokens,
            self.max_seconds,
            self.max_passages,
            self.max_context_chars,
        )


class AgentRunRequest(RequestModel):
    requirement_set_id: uuid.UUID = Field(description="The requirement set the design is made against.")
    objective: Annotated[str, Field(min_length=1, max_length=MAX_OBJECTIVE)] = Field(
        description="What the system should do, in your own words."
    )
    constraints: Annotated[list[Item], Field(max_length=MAX_ITEMS)] = Field(
        default_factory=list, description="Hard constraints, kept as written, e.g. 'must run on AWS'."
    )
    preferences: Annotated[list[Item], Field(max_length=MAX_ITEMS)] = Field(
        default_factory=list, description="Soft preferences: never turned into constraints."
    )
    exclusions: Annotated[list[Item], Field(max_length=MAX_ITEMS)] = Field(
        default_factory=list, description="What is out of scope."
    )
    context: Annotated[str | None, Field(max_length=MAX_CONTEXT)] = None
    base: BaseRevisionModel | None = Field(default=None, description="Iterate on exactly this revision.")
    budget: BudgetModel | None = None

    def to_domain(self) -> tuple[AgentRequest, Budget | None]:
        base = self.base
        request = AgentRequest(
            self.requirement_set_id,
            self.objective,
            tuple(self.constraints),
            tuple(self.preferences),
            tuple(self.exclusions),
            self.context,
            BaseRevision(base.architecture_id, base.revision_number) if base else None,
        )
        return request, self.budget.to_domain() if self.budget else None


class AnswerModel(RequestModel):
    question_id: Annotated[str, Field(min_length=1, max_length=64)]
    answer: Annotated[str, Field(min_length=1, max_length=MAX_STATEMENT)]


class AnswersRequest(RequestModel):
    answers: Annotated[list[AnswerModel], Field(min_length=1, max_length=50)]


class RejectRequest(RequestModel):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class AcceptRequest(RequestModel):
    candidate_content_hash: Fingerprint = Field(description="The candidate as reviewed: its contentHash.")
    name: Annotated[str | None, Field(min_length=1, max_length=100)] = Field(
        default=None, description="A new architecture's name (default: the candidate's)."
    )


# --- responses -----------------------------------------------------------------------------------


class UsageResponse(ApiModel):
    model_calls: int
    input_tokens: int | None = Field(description="As the provider reported them; null: not reported.")
    output_tokens: int | None
    model_latency_ms: int
    retrieval_calls: int
    engine_runs: int
    cost: None = Field(default=None, description="Always null: provider pricing is not configured.")


class QuestionResponse(ApiModel):
    id: str
    kind: QuestionKind
    question: str
    requirement_refs: list[str]
    blocking: bool
    answered: bool

    @classmethod
    def of(cls, question: ClarificationQuestion, answered: set[str]) -> QuestionResponse:
        return cls(
            id=question.id,
            kind=question.kind,
            question=question.question,
            requirement_refs=list(question.requirement_refs),
            blocking=question.blocking,
            answered=question.id in answered,
        )


class AnswerResponse(ApiModel):
    question_id: str
    answer: str
    answered_by_user_id: uuid.UUID
    answered_at: datetime


class ClaimResponse(ApiModel):
    statement: str
    basis: Basis
    requirement_refs: list[str]
    evidence: list[str]
    confidence: str | None = Field(description="The model's own statement (0 to 1); never verification.")

    @classmethod
    def of(cls, claim: Claim) -> ClaimResponse:
        return cls(
            statement=claim.statement,
            basis=claim.basis,
            requirement_refs=list(claim.requirement_refs),
            evidence=list(claim.evidence),
            confidence=_confidence(claim.confidence),
        )


class ProposedNodeResponse(ApiModel):
    id: str
    kind: str
    name: str
    rationale: str
    component: str | None
    technology: str | None
    configuration: dict[str, Any] | None
    requirement_refs: list[str]
    evidence: list[str]
    confidence: str | None

    @classmethod
    def of(cls, node: ProposedNode) -> ProposedNodeResponse:
        return cls(
            id=node.id,
            kind=node.kind,
            name=node.name,
            rationale=node.rationale,
            component=node.component,
            technology=node.technology,
            configuration=dict(node.configuration) if node.configuration else None,
            requirement_refs=list(node.requirement_refs),
            evidence=list(node.evidence),
            confidence=_confidence(node.confidence),
        )


class ProposedConnectionResponse(ApiModel):
    id: str
    source: str
    target: str
    kind: str
    rationale: str
    protocol: str | None
    requirement_refs: list[str]
    evidence: list[str]
    confidence: str | None

    @classmethod
    def of(cls, connection: ProposedConnection) -> ProposedConnectionResponse:
        return cls(
            id=connection.id,
            source=connection.source,
            target=connection.target,
            kind=connection.kind,
            rationale=connection.rationale,
            protocol=connection.protocol,
            requirement_refs=list(connection.requirement_refs),
            evidence=list(connection.evidence),
            confidence=_confidence(connection.confidence),
        )


class DesignDecisionResponse(ApiModel):
    title: str
    choice: str
    rationale: str
    alternatives: list[str]
    trade_offs: list[str]
    requirement_refs: list[str]
    evidence: list[str]

    @classmethod
    def of(cls, decision: DesignDecision) -> DesignDecisionResponse:
        return cls(
            title=decision.title,
            choice=decision.choice,
            rationale=decision.rationale,
            alternatives=list(decision.alternatives),
            trade_offs=list(decision.trade_offs),
            requirement_refs=list(decision.requirement_refs),
            evidence=list(decision.evidence),
        )


class ProposalResponse(ApiModel):
    """The model's proposal as validated: a design for review, never an architecture."""

    name: str
    summary: str
    confidence: str | None
    nodes: list[ProposedNodeResponse]
    connections: list[ProposedConnectionResponse]
    decisions: list[DesignDecisionResponse]
    claims: list[ClaimResponse]
    risks: list[str]
    questions: list[str]

    @classmethod
    def of(cls, proposal: Proposal) -> ProposalResponse:
        return cls(
            name=proposal.name,
            summary=proposal.summary,
            confidence=_confidence(proposal.confidence),
            nodes=[ProposedNodeResponse.of(n) for n in proposal.nodes],
            connections=[ProposedConnectionResponse.of(c) for c in proposal.connections],
            decisions=[DesignDecisionResponse.of(d) for d in proposal.decisions],
            claims=[ClaimResponse.of(c) for c in proposal.claims],
            risks=list(proposal.risks),
            questions=list(proposal.questions),
        )


class EvidenceResponse(ApiModel):
    chunk_id: str
    source_id: str
    source_version: int
    reference: str


class CandidateResponse(ApiModel):
    content_hash: str = Field(description="Name it when accepting: the candidate as reviewed.")
    architecture: dict[str, Any] = Field(description="Canonical Architecture IR (llm_proposal provenance).")
    normalizations: list[str]
    evidence: list[EvidenceResponse]
    uncovered_requirements: list[str] = Field(description="Requirements of the set no element traces to.")

    @classmethod
    def of(cls, candidate: Candidate) -> CandidateResponse:
        return cls(
            content_hash=candidate.content_hash,
            architecture=to_dict(candidate.ir),
            normalizations=list(candidate.normalizations),
            evidence=[EvidenceResponse(**e.to_dict()) for e in candidate.evidence],
            uncovered_requirements=list(candidate.uncovered_requirements),
        )


class FindingResponse(ApiModel):
    engine: str
    rule: str
    severity: str
    message: str
    elements: list[str]


class EngineReportResponse(ApiModel):
    engine: str
    status: EngineStatus
    versions: dict[str, Any]
    findings: list[FindingResponse]
    summary: dict[str, Any] = Field(description="The engine's own counts; never a score.")
    limitations: list[str]
    error: str | None

    @classmethod
    def of(cls, report: EngineReport) -> EngineReportResponse:
        return cls(
            engine=report.engine,
            status=report.status,
            versions=dict(report.versions),
            findings=[FindingResponse(**f.to_dict()) for f in report.findings],
            summary=dict(report.summary),
            limitations=list(report.limitations),
            error=report.error,
        )


class RejectionResponse(ApiModel):
    code: str
    path: str
    detail: str


class FailureResponse(ApiModel):
    code: FailureCode
    message: str
    stage: Stage


class StatusEventResponse(ApiModel):
    status: RunStatus
    stage: Stage
    at: datetime
    user_id: uuid.UUID | None


class AcceptabilityResponse(ApiModel):
    acceptable: bool
    reason: str | None = Field(
        description="validation_blocking, validation_unknown or validation_missing; or the run's status "
        "when it is not candidate_ready."
    )


class RequestResponse(ApiModel):
    requirement_set_id: uuid.UUID
    objective: str
    constraints: list[str]
    preferences: list[str]
    exclusions: list[str]
    context: str | None
    base: dict[str, Any] | None


class AcceptedRevisionResponse(ApiModel):
    architecture_id: uuid.UUID
    revision_number: int


class AgentRunResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: RunStatus
    stage: Stage
    request: RequestResponse
    budget: dict[str, Any]
    usage: UsageResponse
    model: str | None
    prompt_version: str | None
    questions: list[QuestionResponse]
    answers: list[AnswerResponse]
    proposal: ProposalResponse | None
    rejections: list[RejectionResponse]
    candidate: CandidateResponse | None
    reports: list[EngineReportResponse]
    acceptability: AcceptabilityResponse
    raw_output: dict[str, Any] | None = Field(description="Only the SHA-256 and size of the model's output.")
    failure: FailureResponse | None
    accepted: AcceptedRevisionResponse | None
    decision_reason: str | None
    history: list[StatusEventResponse]
    limitations: list[str]
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, run: AgentRun) -> AgentRunResponse:
        answered = {a.question_id for a in run.answers}
        blocked = validation_blocks(run) if run.status is RunStatus.CANDIDATE_READY else run.status.value
        raw, accepted, failure, usage = run.raw_output, run.accepted, run.failure, run.usage
        return cls(
            id=run.id,
            project_id=run.project_id,
            status=run.status,
            stage=run.stage,
            request=RequestResponse(**run.request.to_dict()),
            budget=run.budget.to_dict(),
            usage=UsageResponse(
                model_calls=usage.model_calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                model_latency_ms=usage.model_latency_ms,
                retrieval_calls=usage.retrieval_calls,
                engine_runs=usage.engine_runs,
            ),
            model=run.model,
            prompt_version=run.prompt_version,
            questions=[QuestionResponse.of(q, answered) for q in run.questions],
            answers=[AnswerResponse(**a.to_dict()) for a in run.answers],
            proposal=ProposalResponse.of(run.proposal) if run.proposal else None,
            rejections=[RejectionResponse(**r.to_dict()) for r in run.rejections],
            candidate=CandidateResponse.of(run.candidate) if run.candidate else None,
            reports=[EngineReportResponse.of(r) for r in run.reports],
            acceptability=AcceptabilityResponse(acceptable=blocked is None, reason=blocked),
            raw_output={"sha256": raw.sha256, "bytes": raw.bytes} if raw else None,
            failure=FailureResponse(code=failure.code, message=failure.message, stage=failure.stage)
            if failure
            else None,
            accepted=AcceptedRevisionResponse(
                architecture_id=accepted.architecture_id, revision_number=accepted.number
            )
            if accepted
            else None,
            decision_reason=run.decision_reason,
            history=[StatusEventResponse(**e.to_dict()) for e in run.history],
            limitations=list(run.limitations),
            requested_by_user_id=run.requested_by_user_id,
            requested_at=run.requested_at,
            completed_at=run.completed_at,
        )


class AgentRunSummary(ApiModel):
    id: uuid.UUID
    requirement_set_id: uuid.UUID
    base_architecture_id: uuid.UUID | None
    base_revision_number: int | None
    status: RunStatus
    stage: Stage
    model: str | None
    failure: FailureCode | None
    candidate_content_hash: str | None
    accepted_architecture_id: uuid.UUID | None
    accepted_revision_number: int | None
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, listing: RunListing) -> AgentRunSummary:
        return cls(**{name: getattr(listing, name) for name in cls.model_fields})


class AgentRunPage(ApiModel):
    runs: list[AgentRunSummary]
    next_cursor: str | None


class AcceptedResponse(ApiModel):
    run: AgentRunResponse
    architecture_id: uuid.UUID
    revision_number: int
    created_architecture: bool

    @classmethod
    def of(cls, accepted: AcceptedCandidate) -> AcceptedResponse:
        revision = accepted.run.accepted
        if revision is None:  # an accepted run always names its revision
            raise ValueError("accepted run without a revision")
        return cls(
            run=AgentRunResponse.of(accepted.run),
            architecture_id=revision.architecture_id,
            revision_number=revision.number,
            created_architecture=accepted.created_architecture,
        )
