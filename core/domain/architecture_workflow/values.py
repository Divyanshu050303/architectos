"""The vocabulary of the architecture workflow: where a workflow stands, the stage it reached, the
actions the controller may take, what each action may change, where a candidate stands, and why a
workflow failed.

**The server owns the workflow.** The statuses, stages and actions are closed sets: a model may
propose content (a candidate, an explanation, a question), never a status, a stage or an action.
"""

from collections.abc import Iterable
from enum import StrEnum

from core.domain.discovery.values import FINGERPRINT, KEY, code, count, digest, items, key, text, texts

from .errors import InvalidWorkflowRecord

__all__ = [
    "FINGERPRINT", "KEY", "check", "code", "count", "digest", "items", "key", "text", "texts",
]  # fmt: skip


class WorkflowStatus(StrEnum):
    QUEUED = "queued"  # waiting for a worker
    RUNNING = "running"  # a worker holds it
    NEEDS_INPUT = "needs_input"  # a person must confirm requirements or answer questions
    REVIEW_READY = "review_ready"  # a reviewable package awaits a person's decision
    APPROVED = "approved"  # a person approved a candidate: it is now an architecture revision
    REJECTED = "rejected"  # a person rejected the package
    CANCELLED = "cancelled"
    FAILED = "failed"  # stopped, with why


TERMINAL = frozenset(
    {WorkflowStatus.APPROVED, WorkflowStatus.REJECTED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED}
)


class Stage(StrEnum):
    """Where the workflow is in the lifecycle; the controller, never a model, moves it."""

    INTAKE = "intake"
    REQUIREMENTS = "requirements"  # extraction; a person confirms and pins the requirement set
    KNOWLEDGE = "knowledge_retrieval"
    GENERATION = "generation"  # the first candidate
    VALIDATION = "validation"
    ANALYSIS = "analysis"
    ITERATION = "iteration"  # improvement candidates from findings
    COMPARISON = "comparison"  # deterministic diffs between candidates
    REVIEW = "review"  # the review package
    DECISION = "decision"  # a person approves or rejects


class Action(StrEnum):
    """The controller's closed allowlist. Nothing else can be planned or executed."""

    ANALYZE_REQUIREMENTS = "analyze_requirements"
    REQUEST_CLARIFICATION = "request_clarification"
    RETRIEVE_KNOWLEDGE = "retrieve_knowledge"
    GENERATE_ARCHITECTURE = "generate_architecture"
    VALIDATE_ARCHITECTURE = "validate_architecture"
    RUN_CAPACITY_ANALYSIS = "run_capacity_analysis"
    RUN_COST_ANALYSIS = "run_cost_analysis"
    RUN_RELIABILITY_ANALYSIS = "run_reliability_analysis"
    RUN_SECURITY_ANALYSIS = "run_security_analysis"
    RUN_OBSERVABILITY_ANALYSIS = "run_observability_analysis"
    RUN_SIMULATION = "run_simulation"
    GENERATE_ALTERNATIVE = "generate_alternative"
    COMPARE_CANDIDATES = "compare_candidates"
    PREPARE_REVIEW = "prepare_review"


class SideEffect(StrEnum):
    READ_ONLY = "read_only"  # reads project records
    ANALYSIS = "analysis"  # runs a deterministic engine on a candidate in memory
    CANDIDATE_MUTATION = "candidate_mutation"  # creates workflow records: candidates, steps, analyses
    CANONICAL_MUTATION = "canonical_mutation"  # changes the project's architecture or requirements
    EXTERNAL_SIDE_EFFECT = "external_side_effect"  # anything outside ArchitectOS


AUTOMATIC = frozenset({SideEffect.READ_ONLY, SideEffect.ANALYSIS, SideEffect.CANDIDATE_MUTATION})


class CandidateOrigin(StrEnum):
    AGENT = "agent"  # the architecture agent's proposal, built into canonical IR
    RULE = "rule"  # a deterministic evolution rule's configuration changes to a parent candidate
    AGENT_REVISION = "agent_revision"  # the agent again, given the parent's blocking findings


class CandidateStatus(StrEnum):
    GENERATED = "generated"  # built; not validated yet
    VALIDATED = "validated"  # validation evaluated it and nothing blocks it
    REJECTED = "rejected"  # validation blocks it (kept, never deleted)
    SUPERSEDED = "superseded"  # an improvement replaced it in the review package
    SELECTED_FOR_REVIEW = "selected_for_review"
    ACCEPTED = "accepted"  # a person approved it: it became a revision


class InputKind(StrEnum):
    CONFIRM_REQUIREMENTS = "confirm_requirements"  # promote extracted candidates, then pin a set
    CLARIFICATION = "clarification"  # answer blocking questions


class FailureClass(StrEnum):
    USER = "user"
    WORKFLOW = "workflow"
    AI = "ai"
    ENGINE = "engine"
    INFRASTRUCTURE = "infrastructure"


class FailureCode(StrEnum):
    REQUIREMENTS_UNAVAILABLE = "requirements_unavailable"  # nothing could be extracted or pinned
    BUDGET_EXHAUSTED = "budget_exhausted"  # a limit stopped it before any reviewable candidate
    NO_VALID_CANDIDATE = "no_valid_candidate"  # every candidate is blocked by validation
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_TIMEOUT = "llm_timeout"
    LLM_MALFORMED_OUTPUT = "llm_malformed_output"
    PROPOSAL_REJECTED = "proposal_rejected"  # the model's output was refused by the checks
    ENGINE_ERROR = "engine_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"  # a step failed for a reason outside the workflow
    TIMED_OUT = "timed_out"  # the workflow's duration limit, with nothing reviewable


FAILURE_CLASS: dict[FailureCode, FailureClass] = {
    FailureCode.REQUIREMENTS_UNAVAILABLE: FailureClass.USER,
    FailureCode.BUDGET_EXHAUSTED: FailureClass.WORKFLOW,
    FailureCode.NO_VALID_CANDIDATE: FailureClass.WORKFLOW,
    FailureCode.TIMED_OUT: FailureClass.WORKFLOW,
    FailureCode.LLM_UNAVAILABLE: FailureClass.AI,
    FailureCode.LLM_TIMEOUT: FailureClass.AI,
    FailureCode.LLM_MALFORMED_OUTPUT: FailureClass.AI,
    FailureCode.PROPOSAL_REJECTED: FailureClass.AI,
    FailureCode.ENGINE_ERROR: FailureClass.ENGINE,
    FailureCode.INFRASTRUCTURE_ERROR: FailureClass.INFRASTRUCTURE,
}


class StepStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"  # with a code; a retryable failure may be attempted again under the same key
    SKIPPED = "skipped"  # not applicable (an engine without its inputs), with why


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidWorkflowRecord(details={"fields": found})
