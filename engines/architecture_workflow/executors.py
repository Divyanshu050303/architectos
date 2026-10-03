"""The workflow's executors: one per registry action, each doing exactly that action with an existing
ArchitectOS capability, and reporting what happened as a step outcome. None of them changes a project
record: a candidate, its reports and the questions for a person are returned to the controller, which
commits them with the step.

| Action | Capability |
|---|---|
| analyze_requirements | the requirements engine, through ``RequirementAnalyzer`` (a stored analysis) |
| request_clarification | — (a person confirms the extracted requirements) |
| retrieve_knowledge | the knowledge retriever, authorized for the person |
| generate_architecture | the architecture agent's pipeline (proposal → canonical IR candidate) |
| validate_architecture, run_* | the deterministic engines, on the candidate in memory |
| generate_alternative | evolution rules on the candidate's findings; else the agent again |
| compare_candidates | the deterministic architecture diff (parent, or base revision) |
| prepare_review | — (the validated candidates, newest first) |

Engines run on a worker thread. An engine that fails is reported ``failed`` on the candidate —
never as "no findings".
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.architecture_agent.ports import AgentPipeline, PassInputs
from core.domain.architecture_agent.requests import AgentRequest, AgentUsage
from core.domain.architecture_agent.results import EngineReport
from core.domain.architecture_agent.runs import AgentRun
from core.domain.architecture_agent.values import Basis, RunStatus
from core.domain.architecture_diff.ports import DiffComputer, DiffInputs, ImpactInputs, ResolvedState
from core.domain.architecture_diff.references import ComparedState, StateRef
from core.domain.architecture_diff.values import FindingState
from core.domain.architecture_workflow.budget import WorkflowUsage
from core.domain.architecture_workflow.candidates import FindingRef, WorkflowCandidate
from core.domain.architecture_workflow.planner import reviewable
from core.domain.architecture_workflow.ports import (
    InputsLoader,
    KnowledgeAccess,
    RequirementAnalyzer,
    StepContext,
    StepExecutor,
    StepOutcome,
    WorkflowInputs,
)
from core.domain.architecture_workflow.values import (
    Action,
    CandidateOrigin,
    CandidateStatus,
    InputKind,
    StepStatus,
)
from core.domain.architecture_workflow.workflows import ArchitectureWorkflow, InputRequest
from core.domain.capacity.analyses import AnalysisRequest
from core.domain.capacity.ports import CapacityEngine
from core.domain.cost.analyses import CostAnalysisRequest
from core.domain.cost.ports import CostEngine
from core.domain.evolution.entities import EvolutionRequest
from core.domain.evolution.goals import EvolutionGoal
from core.domain.evolution.goals import FindingRef as EvolutionFinding
from core.domain.evolution.overlays import apply_candidate
from core.domain.evolution.ports import EvolutionEngine
from core.domain.evolution.ports import ImpactInputs as EvolutionInputs
from core.domain.evolution.values import FINDING_SOURCES, EvidenceSource, GoalType, ValidationState
from core.domain.knowledge.retrieval import MAX_QUERY, RetrievalQuery, RetrievalResult
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.ports import ObservabilityEngine
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.ports import ReliabilityEngine
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.ports import SecurityEngine
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.ports import SimulationEngine
from core.domain.validation.options import RevisionInfo, ValidationConfig
from core.domain.validation.ports import ValidationEngine
from engines.architecture_agent.reports import analysis_report, failed_report, validation_report

from .evidence import AnalyzedCandidate, CandidateResults, candidate_evidence
from .reports import capacity_report, cost_report, simulation_report

log = logging.getLogger("architectos.workflow")

DONE, FAILED, SKIPPED = StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED
USABLE = frozenset({ValidationState.VALID, ValidationState.NOT_EVALUABLE})
MAX_PASSAGES = 10
ENGINE_OF = {
    Action.RUN_RELIABILITY_ANALYSIS: "reliability",
    Action.RUN_SECURITY_ANALYSIS: "security",
    Action.RUN_OBSERVABILITY_ANALYSIS: "observability",
    Action.RUN_CAPACITY_ANALYSIS: "capacity",
    Action.RUN_COST_ANALYSIS: "cost",
    Action.RUN_SIMULATION: "simulation",
}


@dataclass(frozen=True, slots=True)
class WorkflowEngines:
    validation: ValidationEngine
    reliability: ReliabilityEngine
    security: SecurityEngine
    observability: ObservabilityEngine
    capacity: CapacityEngine
    cost: CostEngine
    simulation: SimulationEngine
    evolution: EvolutionEngine
    diff: DiffComputer


@dataclass(frozen=True, slots=True)
class _Subject:
    """A candidate as the engines see it, with what the project gives every engine."""

    candidate: WorkflowCandidate
    revision: RevisionInfo
    inputs: WorkflowInputs

    @property
    def analyzed_as(self) -> uuid.UUID:
        return uuid.UUID(self.revision.architecture_id)

    @property
    def policy_or_none(self) -> Any:
        return None if self.inputs.policy.is_empty else self.inputs.policy


def _usage(spent: AgentUsage) -> WorkflowUsage:
    return WorkflowUsage(
        llm_calls=spent.model_calls,
        input_tokens=spent.input_tokens,
        output_tokens=spent.output_tokens,
        retrievals=spent.retrieval_calls,
    )


def _revision(flow: ArchitectureWorkflow, candidate: WorkflowCandidate) -> RevisionInfo:
    """A candidate is analyzed as its workflow, numbered by its ordinal."""
    return RevisionInfo(str(flow.id), candidate.ordinal, candidate.content_hash, IR_SCHEMA_VERSION)


def _target(context: StepContext) -> WorkflowCandidate:
    found = context.candidate(context.decision.candidate_id)
    if found is None:  # the planner names an existing candidate: never expected
        raise LookupError(context.decision.candidate_id)
    return found


async def _subject(loader: InputsLoader, context: StepContext, candidate: WorkflowCandidate) -> _Subject:
    return _Subject(candidate, _revision(context.workflow, candidate), await loader.load(context.workflow))


# --- requirements and knowledge ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnalyzeRequirements:
    analyzer: RequirementAnalyzer

    async def execute(self, context: StepContext) -> StepOutcome:
        analysis = await self.analyzer.analyze(context.workflow)
        outputs: dict[str, Any] = {
            "analysis_id": str(analysis.analysis_id),
            "candidates": analysis.candidates,
        }
        if analysis.candidates == 0:
            note = "The requirements engine found no requirement in the goal."
            return StepOutcome(FAILED, outputs, analysis.usage, "requirements_unavailable", note=note)
        return StepOutcome(DONE, outputs, analysis.usage, requirement_analysis_id=analysis.analysis_id)


class ConfirmRequirements:
    async def execute(self, context: StepContext) -> StepOutcome:
        analysis = context.workflow.requirement_analysis_id
        ask = InputRequest(InputKind.CONFIRM_REQUIREMENTS, analysis)
        return StepOutcome(DONE, {"analysis_id": str(analysis)}, ask=ask)


@dataclass(frozen=True, slots=True)
class RetrieveKnowledge:
    knowledge: KnowledgeAccess

    async def execute(self, context: StepContext) -> StepOutcome:
        query = RetrievalQuery(text=context.workflow.goal.objective[:MAX_QUERY], limit=MAX_PASSAGES)
        spent = WorkflowUsage(retrievals=1)
        try:
            result = await self.knowledge.retrieve(context.workflow, query)
        except Exception as error:  # no knowledge, or not allowed to read it: said, and the design proceeds
            code = str(getattr(error, "code", "retrieval_failed"))[:64]
            note = "Project knowledge could not be retrieved; the design proceeds without passages."
            return StepOutcome(SKIPPED, usage=spent, error=code, note=note)
        outputs = {"passages": len(result.passages), "sources": result.searched_sources}
        return StepOutcome(DONE, outputs, spent, limitations=tuple(result.limitations))


# --- generation -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentStep:
    """The architecture agent, for the workflow's person, against its pinned requirement set."""

    pipeline: AgentPipeline
    loader: InputsLoader
    knowledge: KnowledgeAccess

    async def run(self, context: StepContext, request: AgentRequest) -> AgentRun:
        flow = context.workflow
        inputs = await self.loader.load(flow)

        async def retrieve(query: RetrievalQuery) -> RetrievalResult:
            return await self.knowledge.retrieve(flow, query)

        passes = PassInputs(inputs.planning_input, inputs.requirements, inputs.policy, retrieve, inputs.base)
        run = AgentRun(uuid.uuid4(), flow.project_id, flow.requested_by_user_id, context.now, request)
        run = await self.pipeline.advance(run, passes)
        if run.status is RunStatus.AWAITING_CLARIFICATION:  # the person may have answered these already
            asked = {q.id for q in run.unanswered}
            given = tuple(a for a in flow.answers if a.question_id in {q.id for q in run.questions})
            if asked <= {a.question_id for a in given}:
                answered = run.answer(given, flow.requested_by_user_id, context.now)
                run = await self.pipeline.advance(answered, passes)
        return run


def _limited(values: tuple[str, ...], limit: int) -> tuple[str, ...]:
    return tuple(v[:limit] for v in values if v.strip())[:50]


def _from_run(context: StepContext, run: AgentRun, **lineage: Any) -> WorkflowCandidate | StepOutcome:
    """The agent run's candidate, or the outcome that says why there is none."""
    usage = _usage(run.usage)
    outputs: dict[str, Any] = {
        "agent_status": run.status.value,
        "model": run.model,
        "prompt": run.prompt_version,
    }
    if run.status is RunStatus.AWAITING_CLARIFICATION:
        return StepOutcome(
            DONE, outputs, usage, ask=InputRequest(InputKind.CLARIFICATION, questions=run.unanswered)
        )
    if run.candidate is None or run.proposal is None:
        code = run.failure.code.value if run.failure else "no_candidate"
        note = run.failure.message if run.failure else "The architecture agent produced no candidate."
        return StepOutcome(FAILED, outputs, usage, code, note=note)
    proposal, built = run.proposal, run.candidate
    rationale = (proposal.summary, *(f"{d.title}: {d.choice}. {d.rationale}" for d in proposal.decisions))
    assumptions = tuple(c.statement for c in proposal.claims if c.basis is Basis.ASSUMPTION)
    limitations = tuple(run.limitations)
    if built.uncovered_requirements:
        limitations += (f"Requirements no element traces to: {', '.join(built.uncovered_requirements)}.",)
    return WorkflowCandidate(
        id=uuid.uuid4(), workflow_id=context.workflow.id, ordinal=context.next_ordinal,
        ir=built.ir, content_hash=built.content_hash, created_at=context.now,
        assumptions=_limited(assumptions, 500), rationale=_limited(rationale, 1000),
        evidence=built.evidence, limitations=_limited(limitations, 500), **lineage,
    )  # fmt: skip


def _created(candidate: WorkflowCandidate, run: AgentRun) -> StepOutcome:
    usage = _usage(run.usage).plus(WorkflowUsage(candidates=1))
    return StepOutcome(DONE, {"candidate_id": str(candidate.id)}, usage, candidates=(candidate,))


def _repeats(context: StepContext, content: str, usage: WorkflowUsage) -> StepOutcome | None:
    """An improvement identical to a design the workflow already has is not a new candidate."""
    same = next((c for c in context.candidates if c.content_hash == content), None)
    if same is None:
        return None
    note = f"The improvement is the same design as candidate {same.ordinal}; nothing new to review."
    return StepOutcome(SKIPPED, {"same_as": str(same.id)}, usage, "no_new_design", note=note)


@dataclass(frozen=True, slots=True)
class GenerateArchitecture:
    agent: AgentStep

    async def execute(self, context: StepContext) -> StepOutcome:
        flow = context.workflow
        if flow.requirement_set_id is None:  # the planner generates only with a pinned set
            raise LookupError("requirement_set_id")
        run = await self.agent.run(context, flow.goal.agent_request(flow.requirement_set_id))
        reason = "The architecture agent's design for the goal."
        made = _from_run(context, run, origin=CandidateOrigin.AGENT, reason=reason)
        return made if isinstance(made, StepOutcome) else _created(made, run)


# --- validation and analysis ------------------------------------------------------------------------------


def _report(engines: WorkflowEngines, action: Action, s: _Subject) -> EngineReport:
    """The engine's report for the candidate — the engine's own result, read into the report."""
    ir, rev, number = s.candidate.ir, s.revision, s.revision.number
    policy, requirements, inputs = s.inputs.policy, s.inputs.requirements, s.inputs
    match action:
        case Action.RUN_RELIABILITY_ANALYSIS:
            r = engines.reliability.analyze(
                ir, rev, ReliabilityAnalysisRequest(s.analyzed_as, number), requirements
            )
            return analysis_report("reliability", r, r.model_set)
        case Action.RUN_SECURITY_ANALYSIS:
            request_s = SecurityAnalysisRequest(s.analyzed_as, number)
            sec = engines.security.analyze(ir, rev, request_s, policy, requirements)
            return analysis_report("security", sec, sec.analyzer_set)
        case Action.RUN_OBSERVABILITY_ANALYSIS:
            request_o = ObservabilityAnalysisRequest(s.analyzed_as, number)
            obs = engines.observability.analyze(ir, rev, request_o, policy, requirements)
            return analysis_report("observability", obs, obs.analyzer_set)
        case Action.RUN_CAPACITY_ANALYSIS if inputs.capacity is not None:
            given = inputs.capacity
            request_c = AnalysisRequest(s.analyzed_as, number, given.workload, entries=given.entries)
            return capacity_report(engines.capacity.analyze(ir, rev, request_c, ()).result, given.analysis_id)
        case Action.RUN_COST_ANALYSIS if inputs.cost is not None:
            cost = inputs.cost
            request_p = CostAnalysisRequest(
                s.analyzed_as, number, cost.snapshot.id, cost.currency, cost.pricing_date,
                cost.operating_hours_per_month,
            )  # fmt: skip
            output = engines.cost.analyze(ir, rev, request_p, cost.snapshot, cost.provider, None, ())
            return cost_report(output, cost.analysis_id)
        case Action.RUN_SIMULATION if inputs.scenario is not None:
            workload = inputs.capacity.workload if inputs.capacity else None
            request_m = SimulationRequest(s.analyzed_as, number, inputs.scenario, workload=workload)
            priced = inputs.cost
            simulated = engines.simulation.simulate(
                ir, rev, request_m, requirements, priced.snapshot if priced else None,
                priced.provider if priced else None, priced.currency if priced else None,
            )  # fmt: skip
            return simulation_report(simulated)
    raise LookupError(action)  # planned only with its inputs: never expected


@dataclass(frozen=True, slots=True)
class Validate:
    engines: WorkflowEngines
    loader: InputsLoader

    async def execute(self, context: StepContext) -> StepOutcome:
        s = await _subject(self.loader, context, _target(context))

        def validate() -> EngineReport:
            result = self.engines.validation.validate(
                s.candidate.ir, s.revision, requirements=s.inputs.requirements, policy=s.policy_or_none,
                config=ValidationConfig(),
            )  # fmt: skip
            return validation_report(result)

        try:
            report = await asyncio.to_thread(validate)
        except Exception:
            log.exception(
                "workflow validation could not run", extra={"workflow_id": str(context.workflow.id)}
            )
            failed = s.candidate.with_report(failed_report("validation"))
            return StepOutcome(
                FAILED, error="engine_error", note="Validation could not run.", candidates=(failed,)
            )
        validated = s.candidate.with_report(report).validated()
        outputs = {"blocking": validated.blocking, "status": validated.status.value}
        return StepOutcome(DONE, outputs, candidates=(validated,))


@dataclass(frozen=True, slots=True)
class Analyze:
    engines: WorkflowEngines
    loader: InputsLoader
    action: Action

    async def execute(self, context: StepContext) -> StepOutcome:
        s = await _subject(self.loader, context, _target(context))
        engine = ENGINE_OF[self.action]
        usage = WorkflowUsage(simulations=1 if self.action is Action.RUN_SIMULATION else 0)
        try:
            report = await asyncio.to_thread(_report, self.engines, self.action, s)
        except Exception:
            log.exception("workflow analysis could not run", extra={"engine": engine})
            failed = s.candidate.with_report(failed_report(engine))
            note = f"The {engine} engine could not analyze this candidate."
            ordinal = s.candidate.ordinal
            said = f"The {engine} engine could not analyze candidate {ordinal}; it is reported as failed."
            return StepOutcome(
                FAILED,
                usage=usage,
                error="engine_error",
                note=note,
                candidates=(failed,),
                limitations=(said,),
            )
        outputs = {"findings": len(report.findings), "engine": engine}
        return StepOutcome(DONE, outputs, usage, candidates=(s.candidate.with_report(report),))


# --- iteration ---------------------------------------------------------------------------------------------


def _results(engines: WorkflowEngines, s: _Subject) -> CandidateResults:
    """The parent's results again (deterministic), for the evolution rules' evidence."""
    ir, rev, number = s.candidate.ir, s.revision, s.revision.number
    policy, requirements = s.inputs.policy, s.inputs.requirements
    return CandidateResults(
        engines.validation.validate(
            ir, rev, requirements=requirements, policy=s.policy_or_none, config=ValidationConfig()
        ),
        engines.reliability.analyze(ir, rev, ReliabilityAnalysisRequest(s.analyzed_as, number), requirements),
        engines.security.analyze(
            ir, rev, SecurityAnalysisRequest(s.analyzed_as, number), policy, requirements
        ),
        engines.observability.analyze(
            ir, rev, ObservabilityAnalysisRequest(s.analyzed_as, number), policy, requirements
        ),
    )


def by_rule(
    engines: WorkflowEngines, s: _Subject, trigger: FindingRef, context: StepContext
) -> tuple[ArchitectureIR, str, str] | None:
    """The evolution rules' answer to ``trigger`` on the parent: the changed architecture, the rule and
    its rationale — or None when no rule applies (or none it proposes validates)."""
    sources = {v.value: v for v in EvidenceSource}
    source = sources.get(trigger.engine)
    if source is None or source not in FINDING_SOURCES:
        return None
    flow, parent = context.workflow, s.candidate
    analyzed = AnalyzedCandidate(parent.id, flow.project_id, flow.id, parent.ordinal, parent.content_hash)
    evidence = candidate_evidence(analyzed, _results(engines, s), context.now)
    goal = EvolutionGoal(GoalType.ADDRESS_FINDING, finding=EvolutionFinding(source, trigger.finding_id))
    request = EvolutionRequest(flow.id, parent.ordinal, (goal,))
    inputs = EvolutionInputs(s.inputs.requirements, s.inputs.policy)
    found = engines.evolution.analyze(parent.ir, s.revision, request, evidence, inputs)
    for proposal in found.candidates:
        if goal.key in proposal.goals and proposal.validation in USABLE:
            return apply_candidate(parent.ir, proposal).architecture, proposal.rule.id, proposal.rationale
    return None


@dataclass(frozen=True, slots=True)
class GenerateAlternative:
    engines: WorkflowEngines
    agent: AgentStep
    loader: InputsLoader

    async def execute(self, context: StepContext) -> StepOutcome:
        parent, trigger, flow = _target(context), context.decision.trigger, context.workflow
        if (
            trigger is None or flow.requirement_set_id is None
        ):  # the planner names the finding: never expected
            raise LookupError("trigger")
        lineage: dict[str, Any] = {
            "parent_id": parent.id,
            "trigger": trigger,
            "reason": context.decision.reason or "An improvement of its parent.",
        }
        if parent.status is not CandidateStatus.REJECTED:
            s = await _subject(self.loader, context, parent)
            ruled = await asyncio.to_thread(by_rule, self.engines, s, trigger, context)
            if ruled is not None:
                architecture, rule, rationale = ruled
                repeated = _repeats(context, content_hash(architecture), WorkflowUsage())
                if repeated is not None:
                    return repeated
                candidate = WorkflowCandidate(
                    id=uuid.uuid4(), workflow_id=flow.id, ordinal=context.next_ordinal,
                    origin=CandidateOrigin.RULE, ir=architecture, content_hash=content_hash(architecture),
                    created_at=context.now, rule=rule, rationale=_limited((rationale,), 1000), **lineage,
                )  # fmt: skip
                outputs = {"candidate_id": str(candidate.id), "rule": rule}
                return StepOutcome(DONE, outputs, WorkflowUsage(candidates=1), candidates=(candidate,))
        finding = f"{trigger.engine} finding {trigger.finding_id} ({trigger.severity})"
        note = f"Revise the design to address the {finding} of the previous candidate."
        base = flow.goal.agent_request(flow.requirement_set_id)
        revised = f"{base.context}\n{note}" if base.context else note
        request = AgentRequest(
            base.requirement_set_id, base.objective, base.constraints, base.preferences, base.exclusions,
            revised[:4000], base.base,
        )  # fmt: skip
        run = await self.agent.run(context, request)
        made = _from_run(context, run, origin=CandidateOrigin.AGENT_REVISION, **lineage)
        if isinstance(made, WorkflowCandidate):
            return _repeats(context, made.content_hash, _usage(run.usage)) or _created(made, run)
        if made.ask is not None:  # a revision never waits for a person: no improvement this round
            return StepOutcome(SKIPPED, made.outputs, made.usage, "needs_clarification")
        return made


# --- comparison and review ----------------------------------------------------------------------------------


def _state(flow: ArchitectureWorkflow, candidate: WorkflowCandidate) -> ResolvedState:
    label = f"Candidate {candidate.ordinal}"
    compared = ComparedState(StateRef.candidate(candidate.id), candidate.content_hash, label)
    return ResolvedState(compared, candidate.ir, flow.id, candidate.ordinal)


@dataclass(frozen=True, slots=True)
class Compare:
    engines: WorkflowEngines
    loader: InputsLoader

    async def execute(self, context: StepContext) -> StepOutcome:
        target, flow = _target(context), context.workflow
        inputs = await self.loader.load(flow)
        parent = context.candidate(target.parent_id)
        goal_base = flow.goal.base
        if parent is not None:
            base, against = _state(flow, parent), str(parent.id)
        elif inputs.base is not None and inputs.base_content_hash is not None and goal_base is not None:
            ref = StateRef.revision(goal_base.architecture_id, goal_base.number)
            compared = ComparedState(ref, inputs.base_content_hash, f"Revision {goal_base.number}")
            base = ResolvedState(compared, inputs.base, goal_base.architecture_id, goal_base.number)
            against = f"revision:{goal_base.number}"
        else:
            note = "A first design without a base revision has nothing to compare with."
            return StepOutcome(SKIPPED, error="nothing_to_compare", note=note)
        known = {r.id: r for r in inputs.requirements}
        diff_inputs = DiffInputs(ImpactInputs(inputs.requirements, inputs.policy), known)
        outcome = await asyncio.to_thread(self.engines.diff.compare, base, _state(flow, target), diff_inputs)
        states = [f.state for e in outcome.engines for f in e.findings]
        outputs = {
            "against": against,
            "changes": len(outcome.semantic.changes),
            "groups": len(outcome.semantic.groups),
            "requirements": len(outcome.requirements),
            "decisions": len(outcome.decisions),
            "introduced": states.count(FindingState.INTRODUCED),
            "resolved": states.count(FindingState.RESOLVED),
        }
        return StepOutcome(DONE, outputs)


NOT_RUN = (
    ("capacity_analysis_id", "Capacity was not analyzed: no capacity analysis (workload) was named."),
    ("cost_analysis_id", "Cost was not analyzed: no cost analysis (pricing) was named."),
    ("scenario", "Nothing was simulated: no scenario was named."),
)


class PrepareReview:
    async def execute(self, context: StepContext) -> StepOutcome:
        chosen = reviewable(context.candidates)
        selected = CandidateStatus.SELECTED_FOR_REVIEW
        moved = tuple(c if c.status is selected else c.moved(selected) for c in chosen)
        goal = context.workflow.goal
        not_run = tuple(said for name, said in NOT_RUN if getattr(goal, name) is None)  # said, never zero
        return StepOutcome(
            DONE, {"selected": len(moved)}, candidates=moved, selected=tuple(c.id for c in moved),
            limitations=not_run,
        )  # fmt: skip


def build_executors(
    *,
    engines: WorkflowEngines,
    pipeline: AgentPipeline,
    loader: InputsLoader,
    analyzer: RequirementAnalyzer,
    knowledge: KnowledgeAccess,
) -> dict[Action, StepExecutor]:
    """One executor per registry action — and no other."""
    agent = AgentStep(pipeline, loader, knowledge)
    executors: dict[Action, StepExecutor] = {
        Action.ANALYZE_REQUIREMENTS: AnalyzeRequirements(analyzer),
        Action.REQUEST_CLARIFICATION: ConfirmRequirements(),
        Action.RETRIEVE_KNOWLEDGE: RetrieveKnowledge(knowledge),
        Action.GENERATE_ARCHITECTURE: GenerateArchitecture(agent),
        Action.VALIDATE_ARCHITECTURE: Validate(engines, loader),
        Action.GENERATE_ALTERNATIVE: GenerateAlternative(engines, agent, loader),
        Action.COMPARE_CANDIDATES: Compare(engines, loader),
        Action.PREPARE_REVIEW: PrepareReview(),
    }
    for action in ENGINE_OF:
        executors[action] = Analyze(engines, loader, action)
    return executors
