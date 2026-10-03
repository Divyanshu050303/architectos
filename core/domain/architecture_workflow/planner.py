"""The workflow planner: what happens next, decided by deterministic policy — never by a model.

Given the workflow, its candidates and the steps already done, ``plan`` returns exactly one decision:

- ``Next``: an action from the registry, its stage, its subject and its operation key;
- ``Stop``: the workflow cannot go on (with a classified failure).

Policy, in order (a step already done under its key is never planned again):

1. **Requirements.** Without a pinned requirement set: analyze the goal, then ask a person to confirm
   the extracted requirements (the workflow waits; it never pins requirements itself).
2. **Knowledge.** Retrieve once, when the budget allows any retrieval.
3. **Generation.** Without a candidate: the agent's design (again after a person's answers).
4. **Each candidate of this round:** validate it; when validation does not block it, run the
   analyses whose inputs exist (capacity with a named workload, cost with named pricing, simulation
   with a scenario; reliability, security and observability always); compare it with its parent or
   base.
5. **Iteration.** While iterations remain: answer the newest candidate's blocking validation (the
   agent again), or its first unaddressed critical or high finding (a rule, or the agent when no rule
   applies). One improvement per round.
6. **Review.** The validated candidates (newest first, at most 8) go to review. None validated: stop.

Every action is checked against the budget before it is planned. When a limit is reached, the
workflow goes to review if a validated candidate exists, and stops if none does — it never continues
past a limit.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .budget import blocking_limit
from .candidates import FindingRef, WorkflowCandidate
from .steps import WorkflowStep, operation_key
from .values import Action, CandidateStatus, FailureCode, InputKind, Stage
from .workflows import MAX_SELECTED, ArchitectureWorkflow

A = Action
ACTIONABLE = frozenset({"critical", "high"})
IMPROVABLE_ENGINES = ("validation", "reliability", "security", "capacity", "observability")
ANALYSES = (
    A.RUN_RELIABILITY_ANALYSIS,
    A.RUN_SECURITY_ANALYSIS,
    A.RUN_OBSERVABILITY_ANALYSIS,
    A.RUN_CAPACITY_ANALYSIS,
    A.RUN_COST_ANALYSIS,
    A.RUN_SIMULATION,
)
REVIEWABLE = frozenset({CandidateStatus.VALIDATED, CandidateStatus.SELECTED_FOR_REVIEW})


@dataclass(frozen=True, slots=True)
class Inputs:
    """What the goal names for the engines that need more than an architecture."""

    workload: bool = False  # a capacity analysis was named
    pricing: bool = False  # a cost analysis was named
    scenario: bool = False  # a simulation scenario was given


@dataclass(frozen=True, slots=True)
class Next:
    action: Action
    stage: Stage
    key: str
    iteration: int
    subject: str = ""
    candidate_id: uuid.UUID | None = None
    trigger: FindingRef | None = None  # the finding an improvement answers
    input_kind: InputKind | None = None  # what a clarification asks for
    reason: str = ""  # for review: the limit that stopped the workflow early, if any


@dataclass(frozen=True, slots=True)
class Stop:
    code: FailureCode
    message: str


@dataclass(frozen=True, slots=True)
class PlanningState:
    workflow: ArchitectureWorkflow
    candidates: tuple[WorkflowCandidate, ...] = ()  # in ordinal order
    steps: tuple[WorkflowStep, ...] = ()
    inputs: Inputs = field(default_factory=Inputs)
    elapsed_seconds: float = 0.0


type Decision = Next | Stop


class _Plan:
    def __init__(self, state: PlanningState) -> None:
        self.state = state
        self.flow = state.workflow
        latest: dict[str, WorkflowStep] = {}
        for step in state.steps:
            latest[step.key] = step  # the last attempt decides
        self.done = {k for k, s in latest.items() if s.done}
        self.failed = {k: s for k, s in latest.items() if s.done and s.error is not None}

    def key(self, action: Action, subject: str = "") -> str:
        return operation_key(self.flow.id, action, subject)

    def next(
        self,
        action: Action,
        stage: Stage,
        subject: str = "",
        *,
        candidate_id: uuid.UUID | None = None,
        trigger: FindingRef | None = None,
        reason: str = "",
    ) -> Next | None:
        key = self.key(action, subject)
        if key in self.done:
            return None
        return Next(action, stage, key, self.flow.iteration, subject, candidate_id, trigger, None, reason)


def reviewable(candidates: Sequence[WorkflowCandidate]) -> tuple[WorkflowCandidate, ...]:
    """The candidates a review package holds: validated ones, newest first, at most ``MAX_SELECTED``."""
    validated = [c for c in candidates if c.status in REVIEWABLE]
    return tuple(sorted(validated, key=lambda c: -c.ordinal)[:MAX_SELECTED])


def _review(plan: _Plan, limit: str = "") -> Decision:
    if not reviewable(plan.state.candidates):
        if limit == "max_seconds":
            return Stop(
                FailureCode.TIMED_OUT,
                "The workflow reached its time limit before any candidate passed validation.",
            )
        if limit:
            return Stop(
                FailureCode.BUDGET_EXHAUSTED,
                f"The {limit} limit was reached before any candidate passed validation.",
            )
        return Stop(FailureCode.NO_VALID_CANDIDATE, "No candidate passed validation.")
    return Next(A.PREPARE_REVIEW, Stage.REVIEW, plan.key(A.PREPARE_REVIEW), plan.flow.iteration, reason=limit)


def _requirements(plan: _Plan) -> Decision | None:
    flow = plan.flow
    if flow.requirement_set_id is not None:
        return None
    if flow.requirement_analysis_id is None:
        if plan.key(A.ANALYZE_REQUIREMENTS) in plan.done:
            return Stop(
                FailureCode.REQUIREMENTS_UNAVAILABLE, "No requirements could be extracted from the goal."
            )
        return plan.next(A.ANALYZE_REQUIREMENTS, Stage.REQUIREMENTS)
    return Next(
        A.REQUEST_CLARIFICATION, Stage.REQUIREMENTS, plan.key(A.REQUEST_CLARIFICATION, "requirements"),
        flow.iteration, "requirements", input_kind=InputKind.CONFIRM_REQUIREMENTS,
    )  # fmt: skip


def _knowledge(plan: _Plan) -> Decision | None:
    if plan.flow.budget.max_retrievals == 0:
        return None
    retrieval = operation_key(plan.flow.id, A.RETRIEVE_KNOWLEDGE)
    if retrieval in plan.done:
        return None
    return Next(A.RETRIEVE_KNOWLEDGE, Stage.KNOWLEDGE, retrieval, plan.flow.iteration)


def _generation(plan: _Plan) -> Decision | None:
    if plan.state.candidates:
        return None
    subject = f"answers:{len(plan.flow.answers)}"  # after a person's answers, a new design
    generated = plan.key(A.GENERATE_ARCHITECTURE, subject)
    failed = plan.failed.get(generated)
    if failed is not None:
        error = failed.error or ""
        known = error in {c.value for c in FailureCode}
        code = FailureCode(error) if known else FailureCode.NO_VALID_CANDIDATE
        return Stop(code, failed.note or "The architecture could not be generated.")
    if generated in plan.done:
        return Stop(FailureCode.NO_VALID_CANDIDATE, "The architecture agent produced no candidate.")
    return plan.next(A.GENERATE_ARCHITECTURE, Stage.GENERATION, subject)


def _per_candidate(plan: _Plan) -> Decision | None:
    inputs = plan.state.inputs
    applicable = {
        A.RUN_CAPACITY_ANALYSIS: inputs.workload,
        A.RUN_COST_ANALYSIS: inputs.pricing,
        A.RUN_SIMULATION: inputs.scenario,
    }
    for candidate in plan.state.candidates:
        subject = str(candidate.id)
        if candidate.status is CandidateStatus.GENERATED:  # validation could not decide: left unreviewed
            validate = plan.next(
                A.VALIDATE_ARCHITECTURE, Stage.VALIDATION, subject, candidate_id=candidate.id
            )
            if validate is not None:
                return validate
            continue
        if candidate.status not in REVIEWABLE:
            continue
        for action in ANALYSES:
            if applicable.get(action, True):
                found = plan.next(action, Stage.ANALYSIS, subject, candidate_id=candidate.id)
                if found is not None:
                    return found
        compared = plan.next(A.COMPARE_CANDIDATES, Stage.COMPARISON, subject, candidate_id=candidate.id)
        if compared is not None:
            return compared
    return None


def _addressed(candidates: Sequence[WorkflowCandidate], parent: uuid.UUID) -> set[str]:
    return {c.trigger.finding_id for c in candidates if c.parent_id == parent and c.trigger is not None}


def _iteration(plan: _Plan) -> Decision | None:
    flow, candidates = plan.flow, plan.state.candidates
    if not candidates or flow.iteration >= flow.budget.max_iterations:
        return None
    leaf = max(candidates, key=lambda c: c.ordinal)
    if leaf.status is CandidateStatus.REJECTED:
        report = leaf.report("validation")
        rule = report.findings[0].rule if report and report.findings else "validation"
        return plan.next(
            A.GENERATE_ALTERNATIVE, Stage.ITERATION, f"{leaf.id}:validation", candidate_id=leaf.id,
            trigger=FindingRef("validation", rule, "blocking"),
            reason="Validation blocks the newest candidate.",
        )  # fmt: skip
    if leaf.status not in REVIEWABLE:
        return None
    addressed = _addressed(candidates, leaf.id)
    for engine in IMPROVABLE_ENGINES:
        report = leaf.report(engine)
        for finding in report.findings if report else ():
            if finding.severity not in ACTIONABLE or finding.rule in addressed:
                continue
            subject = f"{leaf.id}:{finding.rule}"[:64]
            found = plan.next(
                A.GENERATE_ALTERNATIVE, Stage.ITERATION, subject, candidate_id=leaf.id,
                trigger=FindingRef(engine, finding.rule, finding.severity, finding.elements),
                reason=f"A {finding.severity} {engine} finding of the newest candidate.",
            )  # fmt: skip
            if found is not None:
                return found
    return None


STAGES: tuple[Callable[[_Plan], Decision | None], ...] = (
    _requirements,
    _knowledge,
    _generation,
    _per_candidate,
    _iteration,
)


def plan(state: PlanningState) -> Decision:
    """The one next decision for a running workflow."""
    p = _Plan(state)
    for stage in STAGES:
        decision = stage(p)
        if decision is None:
            continue
        if isinstance(decision, Next) and decision.action is not A.REQUEST_CLARIFICATION:
            flow = state.workflow
            limit = blocking_limit(flow.budget, flow.usage, decision.action, state.elapsed_seconds)
            if limit is not None:
                return _review(p, limit)
        return decision
    return _review(p)
