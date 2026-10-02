"""What the model proposes, once its output has been parsed — and the questions a run asks a person.

A ``Proposal`` is **untrusted input in a typed shape**: its nodes, connections, decisions and claims
are bounded and well-formed here, but nothing about them is accepted yet. Whether a kind exists, a
component is in the catalog, an endpoint is a node, a requirement is in the set or a citation was
retrieved is decided when the candidate is built — and a proposal that fails is refused, never
repaired.

Every statement keeps its ``Basis``: what a person said, what a cited passage says, what the model
proposes, what is assumed, estimated, unknown or unsupported.

``ClarificationQuestion``: a gap that matters to the design — a conflict, an uncovered concern, a
vague requirement, an unbounded metric — asked once, focused, with the requirements it concerns. An
``Answer`` is a person's statement: from then on, a user-provided fact.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.domain.discovery.values import json_value

from .values import KEY, Basis, QuestionKind, check, digest, items, key, text, texts

MAX_NODES = 60
MAX_CONNECTIONS = 150
MAX_DECISIONS = 30
MAX_CLAIMS = 100
MAX_LIST = 30
MAX_REFS = 30
MAX_STATEMENT = 1000
MAX_RATIONALE = 2000
MAX_QUESTIONS = 20
REQUIREMENT_REF = re.compile(r"^REQ-[1-9][0-9]{0,8}$")


def _refs(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_REFS:
        return name
    return None if all(isinstance(v, str) and REQUIREMENT_REF.fullmatch(v) for v in values) else name


def _evidence(values: object, name: str) -> str | None:
    """Passage ids (``kch_…``) the statement cites — whether they were retrieved is checked later."""
    if not isinstance(values, tuple) or len(values) > MAX_REFS:
        return name
    return None if all(isinstance(v, str) and KEY.fullmatch(v) for v in values) else name


@dataclass(frozen=True, slots=True)
class Claim:
    statement: str
    basis: Basis
    requirement_refs: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                text(self.statement, "claim.statement", MAX_STATEMENT),
                None if isinstance(self.basis, Basis) else "claim.basis",
                _refs(self.requirement_refs, "claim.requirement_refs"),
                _evidence(self.evidence, "claim.evidence"),
                # a "retrieved" claim must cite what it was retrieved from
                "claim.evidence" if self.basis is Basis.RETRIEVED and not self.evidence else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "basis": self.basis.value,
            "requirement_refs": list(self.requirement_refs),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ProposedNode:
    id: str
    kind: str  # checked against the IR's node kinds when the candidate is built
    name: str
    rationale: str
    component: str | None = None  # a catalog id, checked against the catalog
    technology: str | None = None
    configuration: dict[str, Any] | None = None  # checked against the IR's configuration schema
    requirement_refs: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                key(self.id, "node.id"),
                text(self.kind, "node.kind", 64),
                text(self.name, "node.name", 200),
                text(self.rationale, "node.rationale", MAX_RATIONALE),
                text(self.component, "node.component", 128, required=False),
                text(self.technology, "node.technology", 128, required=False),
                None if self.configuration is None else json_value(self.configuration, "node.configuration"),
                _refs(self.requirement_refs, "node.requirement_refs"),
                _evidence(self.evidence, "node.evidence"),
            ]
        )


@dataclass(frozen=True, slots=True)
class ProposedConnection:
    id: str
    source: str
    target: str
    kind: str  # checked against the IR's connection kinds
    rationale: str
    protocol: str | None = None
    requirement_refs: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                key(self.id, "connection.id"),
                key(self.source, "connection.source"),
                key(self.target, "connection.target"),
                text(self.kind, "connection.kind", 64),
                text(self.rationale, "connection.rationale", MAX_RATIONALE),
                text(self.protocol, "connection.protocol", 64, required=False),
                _refs(self.requirement_refs, "connection.requirement_refs"),
                _evidence(self.evidence, "connection.evidence"),
            ]
        )


@dataclass(frozen=True, slots=True)
class DesignDecision:
    title: str
    choice: str
    rationale: str
    alternatives: tuple[str, ...] = ()
    trade_offs: tuple[str, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check(
            [
                text(self.title, "decision.title", 200),
                text(self.choice, "decision.choice", MAX_STATEMENT),
                text(self.rationale, "decision.rationale", MAX_RATIONALE),
                texts(self.alternatives, "decision.alternatives", MAX_STATEMENT),
                texts(self.trade_offs, "decision.trade_offs", MAX_STATEMENT),
                "decision.alternatives" if len(self.alternatives) > MAX_LIST else None,
                "decision.trade_offs" if len(self.trade_offs) > MAX_LIST else None,
                _refs(self.requirement_refs, "decision.requirement_refs"),
                _evidence(self.evidence, "decision.evidence"),
            ]
        )


@dataclass(frozen=True, slots=True)
class Proposal:
    """The model's proposal, parsed: a design for a person to review, never an architecture yet."""

    name: str
    summary: str
    nodes: tuple[ProposedNode, ...]
    connections: tuple[ProposedConnection, ...] = ()
    decisions: tuple[DesignDecision, ...] = ()
    claims: tuple[Claim, ...] = ()  # facts it relies on, assumptions, unknowns, unsupported asks
    risks: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()  # what it could not resolve from the context

    def __post_init__(self) -> None:
        check(
            [
                text(self.name, "proposal.name", 200),
                text(self.summary, "proposal.summary", MAX_RATIONALE),
                items(self.nodes, ProposedNode, "proposal.nodes", MAX_NODES),
                "proposal.nodes" if not self.nodes else None,
                items(self.connections, ProposedConnection, "proposal.connections", MAX_CONNECTIONS),
                items(self.decisions, DesignDecision, "proposal.decisions", MAX_DECISIONS),
                items(self.claims, Claim, "proposal.claims", MAX_CLAIMS),
                texts(self.risks, "proposal.risks", MAX_STATEMENT),
                "proposal.risks" if len(self.risks) > MAX_LIST else None,
                texts(self.questions, "proposal.questions", MAX_STATEMENT),
                "proposal.questions" if len(self.questions) > MAX_QUESTIONS else None,
            ]
        )

    def claims_of(self, basis: Basis) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.basis is basis)


@dataclass(frozen=True, slots=True)
class ClarificationQuestion:
    kind: QuestionKind
    question: str
    requirement_refs: tuple[str, ...] = ()
    blocking: bool = True  # the design depends on the answer: the run waits

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.kind, QuestionKind) else "question.kind",
                text(self.question, "question.question", MAX_STATEMENT),
                _refs(self.requirement_refs, "question.requirement_refs"),
            ]
        )
        object.__setattr__(self, "requirement_refs", tuple(sorted(set(self.requirement_refs))))

    @property
    def id(self) -> str:
        """Stable: the same gap asked twice is the same question."""
        return digest("aq", self.kind.value, self.question, list(self.requirement_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "question": self.question,
            "requirement_refs": list(self.requirement_refs),
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class Answer:
    """A person's answer: a user-provided fact from now on."""

    question_id: str
    answer: str
    answered_by_user_id: uuid.UUID
    answered_at: datetime

    def __post_init__(self) -> None:
        check(
            [
                text(self.question_id, "answer.question_id", 64),
                text(self.answer, "answer.answer", MAX_STATEMENT),
            ]
        )

    def as_claim(self, question: ClarificationQuestion) -> Claim:
        statement = f"{question.question} — {self.answer}"[:MAX_STATEMENT]
        return Claim(statement, Basis.USER_PROVIDED, question.requirement_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "answered_by_user_id": str(self.answered_by_user_id),
            "answered_at": self.answered_at.isoformat(),
        }
