"""Architecture decision records drafted from an evolution analysis, and rendered as ADR documents.

``draft_from_evolution`` turns an analysis into a **proposed** decision: the context (the baseline
revision, the goals, what could not be established), the candidates considered as options (all of
them, or those named), and the evidence and assumptions they rest on. It never chooses: the options
are alternatives, in canonical order, and the decision stays proposed until a person accepts one or
rejects them all.

``to_markdown`` renders a decision as an ADR (status, context, options with their trade-offs, the
decision, its consequences, evidence, assumptions, and the resulting revision once a person links
one). ``decision_ref`` is the Architecture IR's reference to the decision, for a person to attach
to the elements concerned when they make the architecture change.
"""

import uuid
from datetime import datetime

from core.architecture_ir.traceability import DecisionRef
from core.domain.evolution.results import EvolutionResult

from .entities import Decision, DecisionOption, DecisionSource, DecisionStatus
from .errors import InvalidDecision


def _context(analysis_id: uuid.UUID, result: EvolutionResult, options: int) -> str:
    baseline = result.baseline
    goals = "; ".join(g.key for g in result.goals)
    lines = [
        f"Evolution analysis {analysis_id} of revision {baseline.revision_number} "
        f"(content {baseline.content_hash[:12]}), for: {goals}.",
        f"{options} candidate(s) are listed as options. They are alternatives: the engine prefers none.",
    ]
    if result.unsupported_goals:
        lines.append(f"Goals that could not be evaluated: {'; '.join(result.unsupported_goals)}.")
    if result.missing:
        lines.append(f"Inputs that would let the engine decide more: {'; '.join(result.missing)}.")
    return "\n".join(lines)


def draft_from_evolution(  # noqa: PLR0913 -- the record's identity, its source and its author
    *,
    decision_id: uuid.UUID,
    project_id: uuid.UUID,
    number: int,
    analysis_id: uuid.UUID,
    result: EvolutionResult,
    created_by: uuid.UUID,
    at: datetime,
    candidate_ids: tuple[str, ...] | None = None,
    title: str | None = None,
) -> Decision:
    """A proposed decision with the analysis's candidates (or ``candidate_ids``) as its options."""
    by_id = {c.id: c for c in result.candidates}
    chosen = candidate_ids if candidate_ids is not None else tuple(by_id)
    unknown = sorted(set(chosen) - set(by_id))
    if unknown:
        raise InvalidDecision(details={"field": "candidate_ids", "reason": "not_a_candidate"})
    candidates = [by_id[i] for i in chosen]
    goals = sorted({g for c in candidates for g in c.goals}) or [g.key for g in result.goals]
    return Decision(
        id=decision_id,
        project_id=project_id,
        architecture_id=result.baseline.architecture_id,
        number=number,
        title=title or f"Evolution toward {', '.join(goals)}"[:200],
        status=DecisionStatus.PROPOSED,
        context=_context(analysis_id, result, len(candidates)),
        options=tuple(DecisionOption.of(c) for c in candidates),
        created_by_user_id=created_by,
        created_at=at,
        source=DecisionSource(analysis_id, result.baseline, result.model_set.version),
        goals=tuple(goals),
        evidence=tuple(e for c in candidates for e in c.evidence),
        assumptions=result.assumptions,
        related_element_ids=tuple(e for c in candidates for e in c.element_ids),
    )


def decision_ref(decision: Decision) -> DecisionRef:
    """The IR reference to this decision, for the elements it concerns."""
    return DecisionRef(decision.id, decision.related_element_ids)


def _option(option: DecisionOption, chosen: bool) -> list[str]:
    mark = " (chosen)" if chosen else ""
    lines = [
        f"### {option.title}{mark}",
        "",
        f"- Candidate: `{option.candidate_id}` ({option.category})",
        f"- Validation: {option.validation}",
        f"- Goals: {', '.join(option.goals)}",
        "- Changes: " + "; ".join(f"`{c}`" for c in option.changes),
    ]
    if option.consequences:
        lines += ["", "| Dimension | Direction |", "|---|---|"]
        lines += [f"| {d} | {v} |" for d, v in option.consequences]
    return [*lines, ""]


def to_markdown(decision: Decision) -> str:
    """The decision as an ADR document (deterministic for the same record)."""
    lines = [
        f"# {decision.reference}: {decision.title}",
        "",
        f"- Status: {decision.status.value}",
        f"- Date: {decision.created_at.date().isoformat()}",
    ]
    if decision.source is not None:
        baseline = decision.source.baseline
        lines.append(
            f"- Baseline: architecture {baseline.architecture_id}, revision {baseline.revision_number} "
            f"(content {baseline.content_hash[:12]}); evolution analysis {decision.source.analysis_id}"
        )
    lines += ["", "## Context", "", decision.context, "", "## Options considered", ""]
    for option in decision.options:
        lines += _option(option, option.candidate_id == decision.chosen_option)
    lines += ["## Decision", ""]
    if decision.status is DecisionStatus.PROPOSED:
        lines.append("Not decided. The options above are proposals for engineering review.")
    elif decision.chosen_option is not None:
        chosen = decision.option(decision.chosen_option)
        assert chosen is not None  # noqa: S101 -- checked by the record
        lines.append(f"Accepted: {chosen.title} (`{chosen.candidate_id}`). Rationale: {decision.rationale}")
    else:
        lines.append(f"Rejected: none of the options. Rationale: {decision.rationale}")
    if decision.superseded_by is not None:
        lines.append(f"Superseded by decision {decision.superseded_by}.")
    lines += ["", "## Consequences", ""]
    chosen_option = decision.option(decision.chosen_option) if decision.chosen_option else None
    if chosen_option is not None and chosen_option.consequences:
        lines += [f"- {d}: {v}" for d, v in chosen_option.consequences]
    else:
        lines.append("Recorded when an option is accepted.")
    lines += ["", "## Evidence", ""]
    lines += [
        f"- {e.source.value} `{e.reference}` {e.item or ''} ({e.state.value})".rstrip()
        for e in decision.evidence
    ] or ["- None recorded."]
    if decision.assumptions:
        lines += ["", "## Assumptions", ""]
        lines += [f"- {a.label}: {a.value}" for a in decision.assumptions]
    lines += ["", "## Resulting revision", ""]
    if decision.resulting_revision is not None:
        linked = decision.resulting_revision
        lines.append(
            f"Revision {linked.number} (content {linked.content_hash[:12]}), linked by a person on "
            f"{linked.linked_at.date().isoformat()}: their statement that it implements the decision."
        )
    else:
        lines.append("Not linked. Applying a decision is a separate, authorized change to the architecture.")
    return "\n".join(lines) + "\n"
