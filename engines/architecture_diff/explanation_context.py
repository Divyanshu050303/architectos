"""What the model is given to explain a diff (rule ``diff-explanation-context@1``): the deterministic diff,
its groups, the requirement and decision impacts, the engines' comparison and cited passages — never
either architecture as a document, never anything else of the project.

Each part is a named section of data (delimited as untrusted when sent), and each statement the model
may make can cite only what a section lists, by its id: a change (``ch_…``), a group (``cg_…``), a
finding (``engine:finding id``), a requirement (``REQ-n``), a decision (``ADR-n``), a passage
(``kch_…``), or the person's own context (``context``).

**Bounds.** Everything is listed when it fits the budget; otherwise the field details of the changes
are left out (each change and its id stays), then the passages give way — and what was left out is
said. If even the changes' headers do not fit, nothing is sent.

**Retrieval** (through the knowledge retriever, by the caller): one query for the changed components'
names and technologies, one for the requirement and decision references.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core.domain.architecture_diff.changes import Change, FieldDelta
from core.domain.architecture_diff.diffs import ArchitectureDiff
from core.domain.architecture_diff.ports import ExplanationBudget, ExplanationContext
from core.domain.architecture_diff.values import KEY, Basis, ImpactStatus, Sensitivity
from core.domain.knowledge.retrieval import MAX_QUERY, MAX_QUERY_IDENTIFIERS, Passage, RetrievalQuery

RULE = "diff-explanation-context@1"
MAX_PASSAGE_CHARS = 2000
CONTEXT_REF = "context"


@dataclass(frozen=True, slots=True)
class Assembled:
    context: ExplanationContext | None  # None: even the changes' headers do not fit
    evidence: tuple[Passage, ...] = ()  # the passages given
    limitations: tuple[str, ...] = ()


def _value(value: object) -> str:
    return "absent" if value is None else str(value)


def _field(delta: FieldDelta) -> str:
    if delta.sensitivity is Sensitivity.SECRET:
        return f"  {delta.path}: (a secret value changed; its values are not shown)"
    unit = f" {delta.unit}" if delta.unit else ""
    classes = ", ".join(c.value for c in delta.classes)
    return f"  {delta.path}: {_value(delta.before)} -> {_value(delta.after)}{unit} [{classes}]"


def _change(change: Change, detailed: bool) -> str:
    label = f" ({change.label})" if change.label else ""
    kind = f" {change.kind}" if change.kind else ""
    renamed = ", renamed" if change.renamed else ""
    ends = f" from {change.endpoints[0]} to {change.endpoints[1]}" if change.endpoints else ""
    classes = ", ".join(c.value for c in change.classes)
    what = f"{change.element.value}{kind} {change.element_id}{label}{ends}"
    head = f"[{change.id}] {what}: {change.change.value}{renamed} [{classes}]"
    return "\n".join([head, *(_field(f) for f in change.fields)]) if detailed else head


def _requirements(diff: ArchitectureDiff) -> str:
    lines = []
    for r in diff.requirements:
        known = r.base_verdict or r.target_verdict
        verdicts = f"; validation verdict {r.base_verdict} -> {r.target_verdict}" if known else ""
        elements = f"; elements {', '.join(r.element_ids)}" if r.element_ids else ""
        lines.append(f"[{r.reference}] {r.relation.value}: {r.title}. {r.statement}{elements}{verdicts}")
    return "\n".join(lines)


def _decisions(diff: ArchitectureDiff) -> str:
    return "\n".join(
        f"[{d.reference}] {d.title} ({d.status}); concerns {', '.join(d.element_ids)}; may require review"
        for d in diff.decisions
    )


def _engines(diff: ArchitectureDiff) -> str:
    lines = []
    for impact in diff.engines:
        lines.append(f"{impact.engine}: {impact.status.value}; {impact.unchanged} finding(s) unchanged")
        for f in impact.findings:
            elements = f" ({', '.join(f.elements)})" if f.elements else ""
            lines.append(
                f"  [{impact.engine}:{f.finding_id}] {f.state.value} {f.severity}: {f.title}{elements}"
            )
        lines += [f"  {m.name}: {m.before} -> {m.after} {m.unit}" for m in impact.measures]
        lines += [f"  limitation: {limit}" for limit in impact.limitations]
    return "\n".join(lines)


def _citable(
    diff: ArchitectureDiff, passages: Sequence[Passage], context: bool
) -> dict[Basis, frozenset[str]]:
    evaluated = [i for i in diff.engines if i.status is ImpactStatus.EVALUATED]
    return {
        Basis.CHANGE: frozenset(c.id for c in diff.semantic.changes),
        Basis.GROUP: frozenset(g.id for g in diff.semantic.groups),
        Basis.FINDING: frozenset(f"{i.engine}:{f.finding_id}" for i in evaluated for f in i.findings),
        Basis.REQUIREMENT: frozenset(r.reference for r in diff.requirements),
        Basis.DECISION: frozenset(d.reference for d in diff.decisions),
        Basis.EVIDENCE: frozenset(p.citation.chunk_id for p in passages),
        Basis.USER_INPUT: frozenset({CONTEXT_REF}) if context else frozenset(),
    }


def _sections(diff: ArchitectureDiff, detailed: bool) -> list[tuple[str, str]]:
    base, target = diff.base, diff.target
    states = f"Base: {base.label} ({base.ref.kind.value})\nTarget: {target.label} ({target.ref.kind.value})"
    groups = "\n".join(
        f"[{g.id}] {g.title} ({g.rule}): {g.reason}\n  changes: {', '.join(g.change_ids)}"
        for g in diff.semantic.groups
    )
    parts = [
        ("states", states),
        ("comparison_context", diff.request.context or ""),
        ("changes", "\n".join(_change(c, detailed) for c in diff.semantic.changes)),
        ("groups", groups),
        ("requirements", _requirements(diff)),
        ("decisions", _decisions(diff)),
        ("engines", _engines(diff)),
        ("unknowns", "\n".join((*diff.warnings, *diff.unknowns))),
    ]
    return [(name, body) for name, body in parts if body]


def assemble(diff: ArchitectureDiff, passages: Sequence[Passage], budget: ExplanationBudget) -> Assembled:
    """The bounded context: the same diff and passages always give the same context."""
    limitations: list[str] = []
    sections = _sections(diff, detailed=True)
    if sum(len(b) for _, b in sections) > budget.max_context_chars:
        sections = _sections(diff, detailed=False)
        limitations.append("The changes' field details were left out to fit the context budget.")
    room = budget.max_context_chars - sum(len(b) for _, b in sections)
    if room < 0:
        return Assembled(None, limitations=("The diff is too large to explain within the context budget.",))
    given: list[Passage] = []
    blocks: list[str] = []
    for passage in list(passages)[: budget.max_passages]:
        block = (
            f"[{passage.citation.chunk_id}] {passage.citation.reference}\n{passage.text[:MAX_PASSAGE_CHARS]}"
        )
        if len(block) + 2 > room:
            break
        blocks.append(block)
        given.append(passage)
        room -= len(block) + 2
    if len(given) < len(passages):
        limitations.append(
            f"{len(passages) - len(given)} retrieved passage(s) left out to fit the context budget."
        )
    if blocks:
        sections.append(("passages", "\n\n".join(blocks)))
    context = ExplanationContext(
        tuple(sections),
        _citable(diff, given, bool(diff.request.context)),
        tuple(g.id for g in diff.semantic.groups),
        tuple(r.reference for r in diff.requirements),
    )
    return Assembled(context, tuple(given), tuple(limitations))


def retrieval_queries(diff: ArchitectureDiff, budget: ExplanationBudget) -> tuple[RetrievalQuery, ...]:
    """At most two bounded queries: the changed components' names and technologies, and the references."""
    if budget.max_passages == 0 or diff.identical:
        return ()
    limit = min(budget.max_passages, 20)
    words: list[str] = []
    for change in diff.semantic.changes:
        named = (
            str(f.after) for f in change.fields if f.path in {"technology.name", "component"} and f.after
        )
        words += [change.label or "", *named]
    terms = " ".join(dict.fromkeys(w for w in " ".join(words).split() if w))[:MAX_QUERY].strip()
    queries = [RetrievalQuery(text=terms, limit=limit)] if terms else []
    references = [r.reference for r in diff.requirements] + [d.reference for d in diff.decisions]
    identifiers = tuple(dict.fromkeys(r for r in references if KEY.fullmatch(r)))[:MAX_QUERY_IDENTIFIERS]
    if identifiers:
        queries.append(RetrievalQuery(identifiers=identifiers, limit=limit))
    return tuple(queries)


def merged(results: Iterable[Sequence[Passage]], budget: ExplanationBudget) -> tuple[Passage, ...]:
    """Merged in query, then rank order; each passage once; at most ``max_passages``."""
    seen: dict[str, Passage] = {}
    for passages in results:
        for passage in sorted(passages, key=lambda p: p.rank):
            seen.setdefault(passage.citation.chunk_id, passage)
    return tuple(seen.values())[: budget.max_passages]
