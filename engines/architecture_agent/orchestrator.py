"""The architecture agent's fixed pipeline: one pass, the same stages in the same order, bounded.

    interpretation → retrieval → context → proposal → construction → validation → analysis → review

**The model never chooses what happens next.** It is called at one stage (``proposal``, at most twice:
the call and one retry of a retryable failure, within the run's budget) and returns data. Every other
stage is deterministic code, and the run stops — safely, with a stated reason and no candidate — at
the first stage that cannot complete:

- ``interpretation``: the requirement set's gaps. Blocking gaps → the run waits for a person
  (``awaiting_clarification``); answering resumes the same run, which passes here again.
- ``retrieval``: project knowledge through the knowledge retriever. A retrieval that fails is a
  stated limitation, not a failure: the design is then made without those passages, and says so.
- ``context``: the bounded context; essentials that do not fit → ``budget_exhausted``.
- ``proposal`` / ``construction``: the proposer port, then the candidate builder (all or nothing).
- ``validation``: the validation engine on the candidate — required: if it cannot run, the run
  fails (``engine_error``): a candidate is never presented unvalidated.
- ``analysis``: reliability, security and observability on the candidate; one that fails is
  reported as failed (never as "no findings"). Capacity, cost and simulation are reported as not
  evaluated, with why.

**Budget.** Model calls and tokens are counted across the whole run (a resumed run continues the
count); time is counted per pass, so the hours a run waits for a person do not count. A pass that
runs out of time stops with ``budget_exhausted`` at the stage it reached.

**The candidate's revision.** Engines analyze a revision; a candidate has none yet, so it is
analyzed as the revision it would become: the run's id as the architecture, revision 1 (or the base
revision's successor for an iteration), and the candidate's content hash.

Nothing here reads or writes storage: the service gives the inputs and stores the run it returns.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.architecture_agent.errors import InvalidAgentTransition
from core.domain.architecture_agent.ports import (
    ArchitectureProposer,
    PassInputs,
    ProposalContext,
    ProposerOutcome,
)
from core.domain.architecture_agent.proposals import ClarificationQuestion, Proposal
from core.domain.architecture_agent.requests import AgentUsage
from core.domain.architecture_agent.results import Candidate, EngineReport, Rejection
from core.domain.architecture_agent.runs import AgentRun, RunFailure
from core.domain.architecture_agent.values import FailureCode, QuestionKind, RunStatus, Stage
from core.domain.clock import utc_now
from core.domain.components.repository import ComponentCatalog
from core.domain.knowledge.retrieval import Passage, RetrievalResult
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.ports import ObservabilityEngine
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.ports import ReliabilityEngine
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.ports import SecurityEngine
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.ports import ValidationEngine

from .candidate import CandidateInput, build_candidate
from .context import AssembledContext, assemble, gap_questions, retrieval_queries, select_passages, usable
from .reports import analysis_report, failed_report, not_evaluated_reports, validation_report

log = logging.getLogger(__name__)

RULE = "agent-pipeline@1"
MAX_QUESTIONS = 50  # kept on a run; blocking ones first
FAILURE_MESSAGES = {
    FailureCode.LLM_UNAVAILABLE: "The language model is not available. Nothing was proposed.",
    FailureCode.LLM_TIMEOUT: "The language model did not answer in time. Nothing was proposed.",
    FailureCode.LLM_MALFORMED_OUTPUT: "The language model's answer was not a well-formed proposal.",
    FailureCode.PROPOSAL_REJECTED: "The proposal cannot become an architecture: see the rejection reasons.",
    FailureCode.BUDGET_EXHAUSTED: "The run reached its budget before a candidate was ready.",
    FailureCode.REQUIREMENTS_UNUSABLE: "The requirement set has nothing to design against.",
    FailureCode.ENGINE_ERROR: "The validation engine could not check the candidate, so it is not presented.",
}


@dataclass(frozen=True, slots=True)
class AgentEngines:
    validation: ValidationEngine
    reliability: ReliabilityEngine
    security: SecurityEngine
    observability: ObservabilityEngine


def _counted(run: AgentRun, *, retrievals: int = 0, engines: int = 0) -> AgentRun:
    return replace(run, usage=run.usage.plus(AgentUsage(retrieval_calls=retrievals, engine_runs=engines)))


class ArchitectureAgentPipeline:
    def __init__(
        self,
        proposer: ArchitectureProposer,
        engines: AgentEngines,
        catalog: ComponentCatalog,
        *,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._proposer = proposer
        self._engines = engines
        self._catalog = catalog
        self._clock = clock
        self._monotonic = monotonic

    @property
    def configured(self) -> bool:
        return getattr(self._proposer, "configured", True)

    def _fail(
        self, run: AgentRun, code: FailureCode, stage: Stage, rejections: tuple[Rejection, ...] = ()
    ) -> AgentRun:
        failure = RunFailure(code, FAILURE_MESSAGES[code], stage)
        return run.at_stage(stage).fail(failure, self._clock(), proposal=run.proposal, rejections=rejections)

    async def advance(self, run: AgentRun, inputs: PassInputs) -> AgentRun:
        """One pass: from ``queued`` (or ``running`` after answers) to waiting, ready or failed."""
        if run.status is RunStatus.QUEUED:
            run = run.start(self._clock())
        elif run.status is not RunStatus.RUNNING:
            raise InvalidAgentTransition(details={"from": run.status.value, "to": RunStatus.RUNNING.value})
        started = self._monotonic()
        run = self._interpret(run, inputs)
        if run.status is not RunStatus.RUNNING:
            return run
        run, passages = await self._retrieve(run, inputs)
        if self._monotonic() - started > run.budget.max_seconds:
            return self._fail(run, FailureCode.BUDGET_EXHAUSTED, Stage.RETRIEVAL)
        run = run.at_stage(Stage.CONTEXT)
        assembled = assemble(
            request=run.request,
            planning_input=inputs.planning_input,
            questions=run.questions,
            answers=run.answers,
            passages=passages,
            catalog=self._catalog,
            budget=run.budget,
            base=inputs.base,
        )
        run = run.noting(*assembled.limitations)
        if assembled.context is None:
            return self._fail(run, FailureCode.BUDGET_EXHAUSTED, Stage.CONTEXT)
        remaining = run.budget.max_seconds - (self._monotonic() - started)
        run, outcome = await self._propose(run, assembled.context, remaining)
        if outcome.proposal is None:
            return self._fail(
                run, outcome.failure or FailureCode.LLM_MALFORMED_OUTPUT, Stage.PROPOSAL, outcome.rejections
            )
        return self._construct(run, outcome.proposal, outcome, assembled, inputs, started)

    def _construct(
        self,
        run: AgentRun,
        proposal: Proposal,
        outcome: ProposerOutcome,
        assembled: AssembledContext,
        inputs: PassInputs,
        started: float,
    ) -> AgentRun:
        """Canonical IR, or every reason not; then the review."""
        run = replace(run.at_stage(Stage.CONSTRUCTION), proposal=proposal)
        source = CandidateInput(
            proposal,
            inputs.planning_input,
            assembled.labels,
            assembled.passages,
            outcome.model,
            outcome.prompt_version,
            self._clock(),
            run.request.requirement_set_id,
        )
        construction = build_candidate(source, self._catalog)
        if construction.candidate is None:
            return self._fail(run, FailureCode.PROPOSAL_REJECTED, Stage.CONSTRUCTION, construction.rejections)
        if self._monotonic() - started > run.budget.max_seconds:
            return self._fail(run, FailureCode.BUDGET_EXHAUSTED, Stage.CONSTRUCTION)
        return self._review(run, proposal, construction.candidate, inputs)

    def _interpret(self, run: AgentRun, inputs: PassInputs) -> AgentRun:
        """The requirement set and its gaps: blocking ones make the run wait for a person."""
        run = run.at_stage(Stage.INTERPRETATION)
        if not usable(inputs.planning_input):
            return self._fail(run, FailureCode.REQUIREMENTS_UNUSABLE, Stage.INTERPRETATION)
        questions = gap_questions(inputs.requirements)[:MAX_QUESTIONS]
        answered = {a.question_id for a in run.answers}
        if any(q.blocking and q.id not in answered for q in questions):
            return run.ask(questions, self._clock())
        return run.with_questions(tuple(q for q in questions if not q.blocking))

    async def _retrieve(self, run: AgentRun, inputs: PassInputs) -> tuple[AgentRun, tuple[Passage, ...]]:
        """Project knowledge, through the retriever; a failure is said, and the design proceeds."""
        run = run.at_stage(Stage.RETRIEVAL)
        results: list[RetrievalResult] = []
        queries = retrieval_queries(run.request, inputs.planning_input, run.budget)
        for query in queries:
            try:
                results.append(await inputs.retrieve(query))
            except Exception as error:
                code = getattr(error, "code", type(error).__name__)
                run = run.noting(f"Project knowledge could not be retrieved ({code}); no passage was used.")
                results = []
                break
        run = _counted(run, retrievals=len(queries))
        for result in results:
            run = run.noting(*result.limitations)
        return run, select_passages(results, run.budget)

    async def _propose(
        self, run: AgentRun, context: ProposalContext, remaining_seconds: float
    ) -> tuple[AgentRun, ProposerOutcome]:
        """The one model stage. The proposer's own questions are kept, never blocking."""
        run = run.at_stage(Stage.PROPOSAL)
        outcome = await self._proposer.propose(
            context, run.budget, spent=run.usage, remaining_seconds=remaining_seconds
        )
        usage = run.usage.plus(outcome.usage)
        run = run.with_model(outcome.model, outcome.prompt_version, usage, outcome.raw)
        if outcome.proposal is not None and outcome.proposal.questions:
            asked = tuple(
                ClarificationQuestion(QuestionKind.PROPOSER, q, (), blocking=False)
                for q in outcome.proposal.questions
            )
            run = run.with_questions(asked[: max(MAX_QUESTIONS - len(run.questions), 0)])
        return run, outcome

    def _review(
        self, run: AgentRun, proposal: Proposal, candidate: Candidate, inputs: PassInputs
    ) -> AgentRun:
        """Validation (required), then analysis; then the candidate awaits a person."""
        revision = self._revision(run, candidate, inputs)
        run = run.at_stage(Stage.VALIDATION)
        validation = self._validate(candidate, revision, inputs)
        run = _counted(run, engines=1)
        if validation is None:
            return self._fail(run, FailureCode.ENGINE_ERROR, Stage.VALIDATION)
        run = run.at_stage(Stage.ANALYSIS)
        analyses = self._analyze(candidate, revision, inputs)
        run = _counted(run, engines=len(analyses))
        return run.ready(
            proposal, candidate, (validation, *analyses, *not_evaluated_reports()), self._clock()
        )

    # --- engines (each isolated: one failing never hides another's result) ----------------------

    @staticmethod
    def _revision(run: AgentRun, candidate: Candidate, inputs: PassInputs) -> RevisionInfo:
        base = run.request.base
        number = base.number + 1 if base is not None and inputs.base is not None else 1
        return RevisionInfo(str(run.id), number, candidate.content_hash, IR_SCHEMA_VERSION)

    def _validate(
        self, candidate: Candidate, revision: RevisionInfo, inputs: PassInputs
    ) -> EngineReport | None:
        policy = None if inputs.policy.is_empty else inputs.policy
        try:
            result = self._engines.validation.validate(
                candidate.ir,
                revision,
                requirements=inputs.requirements,
                policy=policy,
                config=ValidationConfig(),
            )
        except Exception:  # an engine bug or a refused input: the candidate is not presented
            log.exception("architecture agent: validation could not run", extra={"rule": RULE})
            return None
        return validation_report(result)

    def _analyze(
        self, candidate: Candidate, revision: RevisionInfo, inputs: PassInputs
    ) -> tuple[EngineReport, ...]:
        architecture_id, number = uuid.UUID(revision.architecture_id), revision.number
        ir, requirements, policy, engines = candidate.ir, inputs.requirements, inputs.policy, self._engines
        reports: list[EngineReport] = []
        try:
            reliability = engines.reliability.analyze(
                ir, revision, ReliabilityAnalysisRequest(architecture_id, number), requirements
            )
            reports.append(analysis_report("reliability", reliability, reliability.model_set))
        except Exception:
            log.exception("architecture agent: reliability could not run", extra={"rule": RULE})
            reports.append(failed_report("reliability"))
        try:
            security = engines.security.analyze(
                ir, revision, SecurityAnalysisRequest(architecture_id, number), policy, requirements
            )
            reports.append(analysis_report("security", security, security.analyzer_set))
        except Exception:
            log.exception("architecture agent: security could not run", extra={"rule": RULE})
            reports.append(failed_report("security"))
        try:
            observability = engines.observability.analyze(
                ir, revision, ObservabilityAnalysisRequest(architecture_id, number), policy, requirements
            )
            reports.append(analysis_report("observability", observability, observability.analyzer_set))
        except Exception:
            log.exception("architecture agent: observability could not run", extra={"rule": RULE})
            reports.append(failed_report("observability"))
        return tuple(reports)
