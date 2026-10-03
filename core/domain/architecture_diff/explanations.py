"""The AI interpretation of a diff — what the changes may mean, never what they are.

The model is given the deterministic diff, the groups, the impacts and cited evidence; it returns typed
statements, each **grounded** — citing the changes, groups, findings, requirements, decisions or
passages it rests on, all of which it was given — or **inferred**: an explicitly labelled hypothesis
for a person to check (it may cite what it was inferred from). A statement that is neither is refused,
as is a citation of anything the model was not given. Review questions ask, they never assume
("Was Redis added to reduce database load?", not "Redis improves performance").

An ``ExplanationRun`` records one request for an explanation: the model, prompt version and usage,
the SHA-256 and size of the model's output (never the output, the prompt or the retrieved text), and
the explanation or why there is none. Runs are appended, never replaced: asking again adds a run.
Identical states need no explanation (``not_needed``): the model is not called.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.domain.architecture_agent.requests import AgentUsage
from core.domain.architecture_agent.results import EvidenceRef, Rejection
from core.domain.architecture_agent.runs import RawOutput

from .values import Basis, ExplanationFailure, ExplanationStatus, check, code, items, text, texts

MAX_STATEMENT = 1500
MAX_REFS = 30
MAX_LIST = 30


@dataclass(frozen=True, slots=True)
class Grounding:
    basis: Basis
    ref: str  # a change id, group id, finding id, REQ-n, ADR-n, passage id, or "context"

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.basis, Basis) else "grounding.basis",
                text(self.ref, "grounding.ref", 256),
            ]
        )

    def to_dict(self) -> dict[str, str]:
        return {"basis": self.basis.value, "ref": self.ref}


@dataclass(frozen=True, slots=True)
class Statement:
    text: str
    groundings: tuple[Grounding, ...] = ()
    inferred: bool = False  # an AI hypothesis for a person to review: never presented as fact

    def __post_init__(self) -> None:
        check(
            [
                text(self.text, "statement.text", MAX_STATEMENT),
                items(self.groundings, Grounding, "statement.groundings", MAX_REFS),
                None if isinstance(self.inferred, bool) else "statement.inferred",
                # grounded or labelled an inference: never an unsupported claim presented as fact
                "statement.groundings" if not self.inferred and not self.groundings else None,
            ]
        )

    def refs(self, basis: Basis) -> tuple[str, ...]:
        return tuple(g.ref for g in self.groundings if g.basis is basis)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "groundings": [g.to_dict() for g in self.groundings],
            "inferred": self.inferred,
        }


@dataclass(frozen=True, slots=True)
class GroupExplanation:
    group_id: str
    title: str  # the AI's title for the group (the group's own title is kept, unchanged)
    explanation: Statement
    consequences: tuple[Statement, ...] = ()  # known (grounded) or possible (inferred)
    unknowns: tuple[str, ...] = ()  # what cannot be established from what was given

    def __post_init__(self) -> None:
        check(
            [
                text(self.group_id, "group.group_id", 64),
                text(self.title, "group.title", 200),
                None if isinstance(self.explanation, Statement) else "group.explanation",
                items(self.consequences, Statement, "group.consequences", MAX_LIST),
                texts(self.unknowns, "group.unknowns", MAX_STATEMENT),
                "group.unknowns" if len(self.unknowns) > MAX_LIST else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "title": self.title,
            "explanation": self.explanation.to_dict(),
            "consequences": [c.to_dict() for c in self.consequences],
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True, slots=True)
class RequirementExplanation:
    reference: str  # REQ-n, one of the diff's requirement impacts
    explanation: Statement

    def __post_init__(self) -> None:
        check(
            [
                text(self.reference, "requirement.reference", 32),
                None if isinstance(self.explanation, Statement) else "requirement.explanation",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {"reference": self.reference, "explanation": self.explanation.to_dict()}


@dataclass(frozen=True, slots=True)
class DiffExplanation:
    summary: Statement  # the executive summary
    groups: tuple[GroupExplanation, ...] = ()
    tradeoffs: tuple[Statement, ...] = ()
    requirements: tuple[RequirementExplanation, ...] = ()
    risks: tuple[Statement, ...] = ()  # areas a person should review
    questions: tuple[Statement, ...] = ()  # review questions: they ask, never assume
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        group_ids = [g.group_id for g in self.groups if isinstance(g, GroupExplanation)]
        check(
            [
                None if isinstance(self.summary, Statement) else "explanation.summary",
                items(self.groups, GroupExplanation, "explanation.groups", 500),
                "explanation.groups" if len(set(group_ids)) != len(group_ids) else None,
                items(self.tradeoffs, Statement, "explanation.tradeoffs", MAX_LIST),
                items(self.requirements, RequirementExplanation, "explanation.requirements", 200),
                items(self.risks, Statement, "explanation.risks", MAX_LIST),
                items(self.questions, Statement, "explanation.questions", MAX_LIST),
                texts(self.unknowns, "explanation.unknowns", MAX_STATEMENT),
                "explanation.unknowns" if len(self.unknowns) > MAX_LIST else None,
            ]
        )

    def statements(self) -> tuple[Statement, ...]:
        """Every statement, wherever it is: what grounding checks walk."""
        return (
            self.summary,
            *(s for g in self.groups for s in (g.explanation, *g.consequences)),
            *self.tradeoffs,
            *(r.explanation for r in self.requirements),
            *self.risks,
            *self.questions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "groups": [g.to_dict() for g in self.groups],
            "tradeoffs": [t.to_dict() for t in self.tradeoffs],
            "requirements": [r.to_dict() for r in self.requirements],
            "risks": [r.to_dict() for r in self.risks],
            "questions": [q.to_dict() for q in self.questions],
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True, slots=True)
class ExplanationRun:
    id: uuid.UUID
    diff_id: uuid.UUID
    status: ExplanationStatus
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    model: str | None = None
    prompt_version: str | None = None
    usage: AgentUsage = field(default_factory=AgentUsage)
    raw_output: RawOutput | None = None
    explanation: DiffExplanation | None = None
    evidence: tuple[EvidenceRef, ...] = ()  # the passages it cites, with their citations
    failure: ExplanationFailure | None = None
    rejections: tuple[Rejection, ...] = ()  # why the output could not be used
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        completed = self.status is ExplanationStatus.COMPLETED
        failed = self.status is ExplanationStatus.FAILED
        not_needed = self.status is ExplanationStatus.NOT_NEEDED
        called = bool(self.model) or (isinstance(self.usage, AgentUsage) and self.usage.model_calls > 0)
        check(
            [
                None if isinstance(self.status, ExplanationStatus) else "run.status",
                "run.explanation" if completed != (self.explanation is not None) else None,
                "run.failure" if failed != (self.failure is not None) else None,
                "run.model" if not_needed and called else None,  # identical states: no model call
                text(self.model, "run.model", 128, required=False),
                code(self.prompt_version, "run.prompt_version", required=False),
                None if isinstance(self.usage, AgentUsage) else "run.usage",
                items(self.evidence, EvidenceRef, "run.evidence", 200),
                items(self.rejections, Rejection, "run.rejections", 100),
                texts(self.limitations, "run.limitations", 500),
            ]
        )
