"""The vocabulary of the architecture agent: where a run stands, which stage it reached, why it failed,
and — for every statement it makes — what the statement rests on. Checking helpers are discovery's
(the same validators), raising the agent's own error.

**The model proposes; nothing it says is verified by saying it.** Every claim in a proposal carries a
``Basis``, kept apart and never collapsed into one confidence score:

- ``user_provided``: stated by a person (the objective, a requirement, an answer to a question);
- ``retrieved``: stated by a cited passage of the project's knowledge (what the source says, not
  verified);
- ``proposed``: the model's design choice — a proposal for a person to review;
- ``assumption``: taken as true to proceed, and said to be;
- ``estimate``: calculated by a deterministic engine from stated inputs;
- ``unknown``: the evidence is insufficient;
- ``unsupported``: asked for, but nothing in ArchitectOS can represent or check it.
"""

from collections.abc import Iterable
from enum import StrEnum

from core.domain.discovery.values import (
    FINGERPRINT,
    KEY,
    code,
    count,
    digest,
    fingerprint,
    items,
    key,
    text,
    texts,
)

from .errors import InvalidAgentRecord

__all__ = [
    "FINGERPRINT", "KEY", "check", "code", "count", "digest", "fingerprint", "items", "key", "text", "texts",
]  # fmt: skip


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CLARIFICATION = "awaiting_clarification"  # blocking gaps: questions for a person
    CANDIDATE_READY = "candidate_ready"  # a validated candidate awaits a person's decision
    FAILED = "failed"  # stopped, with why; no candidate
    CANCELLED = "cancelled"
    ACCEPTED = "accepted"  # a person accepted the candidate: it is now an architecture revision
    REJECTED = "rejected"  # a person rejected the candidate


TERMINAL = frozenset({RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.ACCEPTED, RunStatus.REJECTED})


class Stage(StrEnum):
    """The workflow's fixed stages, in order. The model never chooses the next one."""

    INTAKE = "intake"
    INTERPRETATION = "interpretation"  # the requirement set and its gaps
    RETRIEVAL = "retrieval"  # project knowledge, through the knowledge retriever
    CONTEXT = "context"  # the bounded context the model is given
    PROPOSAL = "proposal"  # one structured model call (and at most one retry)
    CONSTRUCTION = "construction"  # the proposal turned into a canonical IR candidate, or refused
    VALIDATION = "validation"  # the validation engine
    ANALYSIS = "analysis"  # reliability, security, observability
    REVIEW = "review"  # the review package
    DECISION = "decision"  # a person accepts or rejects


STAGES = tuple(Stage)


class Basis(StrEnum):
    USER_PROVIDED = "user_provided"
    RETRIEVED = "retrieved"
    PROPOSED = "proposed"
    ASSUMPTION = "assumption"
    ESTIMATE = "estimate"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class FailureCode(StrEnum):
    LLM_UNAVAILABLE = "llm_unavailable"  # not configured, unreachable, rate-limited or refused
    LLM_TIMEOUT = "llm_timeout"
    LLM_MALFORMED_OUTPUT = "llm_malformed_output"  # not JSON, not the schema, or cut off
    PROPOSAL_REJECTED = "proposal_rejected"  # valid JSON whose content cannot become an architecture
    BUDGET_EXHAUSTED = "budget_exhausted"  # model calls, tokens or time ran out
    REQUIREMENTS_UNUSABLE = "requirements_unusable"  # the requirement set cannot be designed against
    ENGINE_ERROR = "engine_error"  # a deterministic engine could not run


class EngineStatus(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"  # its inputs are not available (e.g. no workload for capacity)
    FAILED = "failed"


class QuestionKind(StrEnum):
    CONFLICT = "conflict"  # requirements that cannot all hold
    MISSING_CONCERN = "missing_concern"  # a concern no requirement covers (e.g. availability)
    AMBIGUITY = "ambiguity"  # a requirement too vague to design against
    UNBOUNDED = "unbounded"  # a metric without a bound
    PROPOSER = "proposer"  # a question the model could not answer from the context


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidAgentRecord(details={"fields": found})
