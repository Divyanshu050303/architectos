"""What a run knows before the model is called: the requirement set's gaps, the questions they raise,
the knowledge to retrieve, and the bounded context the model is given.

**Gaps** come from the requirements engine's own analysis (``analyze``) of the pinned requirement
versions — never from the model:

- a conflict, or no requirement about **traffic** or **availability**, is *blocking*: the run asks a
  person and waits (a design cannot be sized or made redundant against nothing);
- a vague requirement, a metric without a lower bound, or another uncovered concern is asked too,
  but not blocking: the run proceeds and the gap is stated in the context as an open question.

Every question has a stable id (the same gap is the same question), and an answered one is not asked
again: the answer is a person's statement, given to the model as such.

**Retrieval** is the knowledge retriever's (authorized, cited): one query for the objective's terms,
one for the requirement references, merged in rank order without duplicates, up to the budget.

**The context** is a list of named sections, each later delimited as untrusted data. Requirements and
the person's own words are never cut: if they do not fit the budget, nothing is sent
(``budget_exhausted``). Passages, then the catalog listing, are what gives way — and what was left
out is said, never silent.
"""

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import to_dict
from core.domain.architecture_agent.ports import ContextSection, ProposalContext
from core.domain.architecture_agent.proposals import Answer, ClarificationQuestion
from core.domain.architecture_agent.requests import AgentRequest, Budget
from core.domain.architecture_agent.results import EvidenceRef
from core.domain.architecture_agent.values import KEY, QuestionKind
from core.domain.components.entities import SupportStatus
from core.domain.components.repository import ComponentCatalog
from core.domain.knowledge.retrieval import (
    MAX_QUERY,
    MAX_QUERY_IDENTIFIERS,
    MAX_RESULTS,
    Passage,
    RetrievalQuery,
    RetrievalResult,
)
from core.domain.requirements.analysis import Concern, analyze
from core.domain.requirements.entities import Requirement
from core.domain.requirements.planning import PlanningInputV2, PlanningRequirementV2

RULE = "agent-context@1"
BLOCKING_CONCERNS = frozenset({Concern.TRAFFIC, Concern.AVAILABILITY})
MAX_PASSAGE_CHARS = 2000
CONCERN_QUESTIONS = {
    Concern.TRAFFIC: (
        "No requirement states the expected load (requests per second, concurrent or daily users). "
        "What load must the architecture handle?"
    ),
    Concern.AVAILABILITY: (
        "No requirement states the availability required. What availability must the system meet "
        "(for example 99.9% monthly), and how quickly must it recover?"
    ),
    Concern.LATENCY: "No requirement states a response time. Is there a latency the design must meet?",
    Concern.DATA: "No requirement describes the data the system holds. What data, and how much?",
    Concern.SECURITY: "No requirement states a security need. What must be protected, and from whom?",
    Concern.RETENTION: "No requirement states how long data is kept. Is there a retention period?",
}
AMBIGUITY_QUESTIONS = {
    "missing_constraint": "{ref} has no measurable bound. What value must the design meet?",
    "missing_percentile": (
        "{ref} states a latency without a percentile. Which percentile applies (p50, p95, p99)?"
    ),
    "low_confidence": "{ref} was extracted automatically with low confidence. Is it stated correctly?",
}


# --- gaps and questions -------------------------------------------------------------------------


def gap_questions(requirements: Sequence[Requirement]) -> tuple[ClarificationQuestion, ...]:
    """The questions the requirement set raises, blocking first, in a stable order."""
    analysis = analyze(list(requirements))
    questions: list[ClarificationQuestion] = []
    for conflict in analysis.conflicts:
        first, second = conflict.requirements
        refs = (first.reference, second.reference)
        text = f"{refs[0]} and {refs[1]} cannot both hold ({conflict.metric}). Which one applies?"
        questions.append(ClarificationQuestion(QuestionKind.CONFLICT, text, refs, blocking=True))
    for concern in analysis.missing:
        question = CONCERN_QUESTIONS[concern]
        blocking = concern in BLOCKING_CONCERNS
        questions.append(ClarificationQuestion(QuestionKind.MISSING_CONCERN, question, (), blocking))
    for ambiguity in analysis.ambiguous:
        template = AMBIGUITY_QUESTIONS.get(ambiguity.reason)
        if template is not None:
            reference = ambiguity.requirement.reference
            text = template.format(ref=reference)
            questions.append(
                ClarificationQuestion(QuestionKind.AMBIGUITY, text, (reference,), blocking=False)
            )
    for unbounded in analysis.unbounded:
        sizing = tuple(r.reference for r in unbounded.requirements)
        text = f"{unbounded.metric} has no lower bound in {', '.join(sizing)}. What minimum must it support?"
        questions.append(ClarificationQuestion(QuestionKind.UNBOUNDED, text, sizing, blocking=False))
    unique = {q.id: q for q in questions}
    return tuple(sorted(unique.values(), key=lambda q: (not q.blocking, q.kind.value, q.question)))


def usable(planning_input: PlanningInputV2) -> bool:
    """A set without requirements cannot be designed against: ``requirements_unusable``."""
    return bool(planning_input["requirements"])


# --- retrieval ----------------------------------------------------------------------------------


def retrieval_queries(
    request: AgentRequest, planning_input: PlanningInputV2, budget: Budget
) -> tuple[RetrievalQuery, ...]:
    """At most two queries, both bounded: the objective's terms, and the requirement references."""
    if budget.max_passages == 0:
        return ()
    limit = min(budget.max_passages, MAX_RESULTS)
    words = " ".join(request.objective.split())
    terms = words[:MAX_QUERY].rsplit(" ", 1)[0] if len(words) > MAX_QUERY else words
    queries = [RetrievalQuery(text=terms, limit=limit)]
    references = [r["reference"] for r in planning_input["requirements"] if KEY.fullmatch(r["reference"])]
    if references:
        queries.append(RetrievalQuery(identifiers=tuple(references[:MAX_QUERY_IDENTIFIERS]), limit=limit))
    return tuple(queries)


def select_passages(results: Iterable[RetrievalResult], budget: Budget) -> tuple[Passage, ...]:
    """Merged in query, then rank order; each chunk once; at most ``max_passages``; citable ids only."""
    seen: dict[str, Passage] = {}
    for result in results:
        for passage in sorted(result.passages, key=lambda p: p.rank):
            chunk = passage.citation.chunk_id
            if chunk not in seen and KEY.fullmatch(chunk):
                seen[chunk] = passage
    return tuple(seen.values())[: budget.max_passages]


def evidence_ref(passage: Passage) -> EvidenceRef:
    citation = passage.citation
    return EvidenceRef(
        citation.chunk_id, str(citation.source_id), citation.source_version, citation.reference[:500]
    )


# --- the context --------------------------------------------------------------------------------


def _constraint(constraint: Mapping[str, Any] | None) -> str:
    if not constraint:
        return ""
    metric, operator, unit = constraint["metric"], constraint["operator"], constraint.get("unit", "")
    if "values" in constraint:
        bound = f"{operator} {{{', '.join(constraint['values'])}}}"
    elif "min" in constraint:
        bound = f"between {constraint['min']} and {constraint['max']} {unit}".rstrip()
    else:
        bound = f"{operator} {constraint.get('value', '')} {unit}".rstrip()
    percentile = f" at p{constraint['percentile']}" if constraint.get("percentile") else ""
    return f" [{metric} {bound}{percentile}]"


def _requirement(r: PlanningRequirementV2) -> str:
    facts = f"{r['type']}/{r['category']}, {r['priority']} priority, {r['status']}, scope {r['scope']}"
    stated = r.get("constraint")
    constraint = _constraint(dict(stated) if stated else None)
    return f"{r['reference']} ({facts}): {r['title']}. {r['statement']}{constraint}"


def _passage(passage: Passage) -> tuple[str, bool]:
    cut = len(passage.text) > MAX_PASSAGE_CHARS
    notes = [passage.verification.value]
    if passage.stale:
        notes.append("stale: the source changed since it was indexed")
    if passage.record_status:
        notes.append(f"status {passage.record_status}")
    body = passage.text[:MAX_PASSAGE_CHARS] + (" [cut]" if cut else "")
    return f"[{passage.citation.chunk_id}] {passage.citation.reference} ({'; '.join(notes)})\n{body}", cut


def _catalog(catalog: ComponentCatalog) -> str:
    return "\n".join(
        f"{s.id}: {s.technology}; kinds {', '.join(k.value for k in s.node_kinds)}; {s.support_status.value}"
        for s in catalog.list()
        if s.support_status is not SupportStatus.DEPRECATED
    )


def _lines(values: Iterable[str]) -> str:
    return "\n".join(f"- {v}" for v in values)


@dataclass(frozen=True, slots=True)
class AssembledContext:
    context: ProposalContext | None  # None: the essentials alone do not fit (or there is nothing)
    labels: Mapping[str, uuid.UUID] = field(default_factory=dict)  # REQ-n → requirement id
    passages: Mapping[str, EvidenceRef] = field(default_factory=dict)  # the passages given, by id
    limitations: tuple[str, ...] = ()  # what was left out or cut, said


def _essential(
    request: AgentRequest,
    planning_input: PlanningInputV2,
    questions: Sequence[ClarificationQuestion],
    answers: Sequence[Answer],
    base: ArchitectureIR | None,
) -> list[ContextSection]:
    asked = {q.id: q for q in questions}
    answered = [a for a in answers if a.question_id in asked]
    open_questions = [q.question for q in questions if q.id not in {a.question_id for a in answered}]
    lists = (("constraints", request.constraints), ("preferences", request.preferences))
    sections = [ContextSection("objective", request.objective)]
    sections += [ContextSection(name, _lines(values)) for name, values in lists if values]
    if request.exclusions:
        sections.append(ContextSection("exclusions", _lines(request.exclusions)))
    if request.context:
        sections.append(ContextSection("person_context", request.context))
    requirements = "\n".join(_requirement(r) for r in planning_input["requirements"])
    sections.append(ContextSection("requirements", requirements))
    if answered:
        pairs = (f"Q: {asked[a.question_id].question}\nA: {a.answer}" for a in answered)
        sections.append(ContextSection("answers", "\n".join(pairs)))
    if open_questions:
        sections.append(ContextSection("open_questions", _lines(open_questions)))
    if base is not None:
        document = json.dumps(to_dict(base), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        sections.append(ContextSection("base_architecture", document))
    return sections


def assemble(
    *,
    request: AgentRequest,
    planning_input: PlanningInputV2,
    questions: Sequence[ClarificationQuestion],
    answers: Sequence[Answer],
    passages: Sequence[Passage],
    catalog: ComponentCatalog,
    budget: Budget,
    base: ArchitectureIR | None = None,
) -> AssembledContext:
    """The bounded context: the same inputs always give the same context."""
    labels = {r["reference"]: uuid.UUID(r["id"]) for r in planning_input["requirements"]}
    if not usable(planning_input):
        return AssembledContext(None, labels, limitations=("The requirement set has no requirements.",))
    essential = _essential(request, planning_input, questions, answers, base)
    room = budget.max_context_chars - sum(len(s.body) for s in essential)
    if room < 0:
        reason = "The requirements and the request alone exceed the context budget."
        return AssembledContext(None, labels, limitations=(reason,))
    limitations: list[str] = []
    catalog_text = _catalog(catalog)
    reserved = len(catalog_text) if len(catalog_text) <= room else 0  # the listing, if it can fit at all
    given: list[Passage] = []
    blocks: list[str] = []
    for passage in passages:
        block, cut = _passage(passage)
        if len(block) + 2 > room - reserved:
            break
        blocks.append(block)
        given.append(passage)
        room -= len(block) + 2
        if cut:
            limitations.append(
                f"Passage {passage.citation.chunk_id} was cut to {MAX_PASSAGE_CHARS} characters."
            )
    if len(given) < len(passages):
        left_out = len(passages) - len(given)
        limitations.append(f"{left_out} retrieved passage(s) left out to fit the context budget.")
    sections = list(essential)
    if blocks:
        sections.append(ContextSection("passages", "\n\n".join(blocks)))
    if catalog_text and len(catalog_text) <= room:
        sections.append(ContextSection("component_catalog", catalog_text))
    elif catalog_text:
        limitations.append("The component catalog listing was left out to fit the context budget.")
    context = ProposalContext(
        tuple(sections), requirement_refs=tuple(labels), passage_ids=tuple(p.citation.chunk_id for p in given)
    )
    evidence = {p.citation.chunk_id: evidence_ref(p) for p in given}
    return AssembledContext(context, labels, evidence, tuple(limitations))
