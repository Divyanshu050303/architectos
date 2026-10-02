"""The port the run's proposal stage calls: a bounded context in, a parsed proposal (or why not) out.

The context is a list of named sections (the objective, the requirements, the retrieved passages…)
that the proposer places in the prompt **as delimited, untrusted data**: nothing in a section can
change the instructions. The outcome never raises for a model failure — it says what happened, how
much was used, and (when there was output) the SHA-256 and size of that output; never the output.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.knowledge.retrieval import RetrievalQuery, RetrievalResult
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.requirements.planning import PlanningInputV2

from .proposals import Proposal
from .requests import AgentUsage, Budget
from .results import Rejection
from .runs import AgentRun, RawOutput
from .values import FailureCode, check, code, items, text, texts

MAX_SECTIONS = 20


@dataclass(frozen=True, slots=True)
class ContextSection:
    name: str  # e.g. "objective", "requirements", "passages"
    body: str

    def __post_init__(self) -> None:
        check([code(self.name, "section.name"), text(self.body, "section.body", 1_000_000)])


@dataclass(frozen=True, slots=True)
class ProposalContext:
    sections: tuple[ContextSection, ...]
    requirement_refs: tuple[str, ...] = ()  # the REQ-n labels the context lists
    passage_ids: tuple[str, ...] = ()  # the passage ids the context lists

    def __post_init__(self) -> None:
        names = [s.name for s in self.sections if isinstance(s, ContextSection)]
        check(
            [
                items(self.sections, ContextSection, "context.sections", MAX_SECTIONS),
                "context.sections" if not self.sections or len(set(names)) != len(names) else None,
                texts(self.requirement_refs, "context.requirement_refs", 32),
                texts(self.passage_ids, "context.passage_ids", 128),
            ]
        )

    @property
    def size(self) -> int:
        """Characters of data, as the budget counts them."""
        return sum(len(s.body) for s in self.sections)


@dataclass(frozen=True, slots=True)
class ProposerOutcome:
    model: str  # provider/model
    prompt_version: str
    usage: AgentUsage  # this stage's model calls only
    proposal: Proposal | None = None
    failure: FailureCode | None = None
    rejections: tuple[Rejection, ...] = ()  # why the output could not be read as a proposal
    raw: RawOutput | None = None  # the last output received, as a hash and a size
    attempts: tuple[str, ...] = ()  # the error code of each failed attempt, in order

    def __post_init__(self) -> None:
        check(
            [
                text(self.model, "outcome.model", 128),
                code(self.prompt_version, "outcome.prompt_version"),
                "outcome.failure" if (self.proposal is None) == (self.failure is None) else None,
                items(self.rejections, Rejection, "outcome.rejections", 100),
                texts(self.attempts, "outcome.attempts", 64),
            ]
        )


class ArchitectureProposer(Protocol):
    @property
    def model(self) -> str: ...

    async def propose(
        self, context: ProposalContext, budget: Budget, *, spent: AgentUsage, remaining_seconds: float
    ) -> ProposerOutcome:
        """At most ``budget.max_model_calls - spent.model_calls`` calls, within ``remaining_seconds``.
        Never raises for a model failure: the outcome says what failed."""
        ...


Retrieve = Callable[[RetrievalQuery], Awaitable[RetrievalResult]]


@dataclass(frozen=True, slots=True)
class PassInputs:
    """What the service loaded for one pass (all of the run's project, all authorized)."""

    planning_input: PlanningInputV2  # the requirement set, pinned
    requirements: tuple[Requirement, ...]  # the same pinned versions, for the engines
    policy: ArchitecturePolicy
    retrieve: Retrieve  # the knowledge retriever, bound to the project and the requesting person
    base: ArchitectureIR | None = None  # the revision an iteration starts from


class AgentPipeline(Protocol):
    @property
    def configured(self) -> bool:
        """Whether a language model is configured (without one, every run fails ``llm_unavailable``)."""
        ...

    async def advance(self, run: AgentRun, inputs: PassInputs) -> AgentRun:
        """One pass, from ``queued`` (or ``running`` after answers) to waiting, ready or failed."""
        ...
