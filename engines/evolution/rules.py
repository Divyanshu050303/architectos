"""Evolution rules and deterministic candidate generation.

A **rule** turns one trigger (current evidence relevant to a goal) into candidate proposals: the
configuration changes its documented reasoning supports, or a finding saying why it cannot propose
one (a precondition that fails, an input the architecture does not declare). Each rule declares its
id and version, the triggers and goal types it applies to, the evidence it requires, the category
and the properties it may change, its preconditions, expected benefits, known trade-offs,
unsupported conditions, the validation it needs and the analyses that should evaluate its
candidates. Rules never modify the architecture: they return proposals.

**Generation** walks the triggers in canonical order and asks every applicable rule. A trigger no
rule applies to is reported (``no_applicable_rule``), with the engine's own recommendation; a limit
no model can scale past becomes a ``structural_consideration`` for human review (a cache, a queue or
a split is never proposed: no model evaluates it). Candidates a request constraint excludes are
reported (``excluded_by_constraint``), never silently dropped. The same proposal reached from two
triggers is one candidate addressing both goals.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Protocol

from core.architecture_ir.model import ArchitectureIR
from core.domain.engine_results import ModelSet
from core.domain.evolution.candidates import BaselineRef, Candidate
from core.domain.evolution.entities import EvolutionConstraints
from core.domain.evolution.errors import InvalidEvolutionResult
from core.domain.evolution.goals import EvolutionGoal
from core.domain.evolution.results import EvolutionFinding, FindingType
from core.domain.evolution.triggers import Trigger, TriggerKind
from core.domain.evolution.values import CandidateCategory, EvidenceSource, GoalType
from core.domain.simulations.scenarios import specs_of

ENGINE = ("evolution", 1)  # the generation's own version


class DuplicateRule(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RuleMeta:
    id: str
    version: int
    name: str
    description: str
    category: CandidateCategory
    triggers: tuple[tuple[EvidenceSource, str], ...]  # (source, trigger code) it reads
    goals: tuple[GoalType, ...]  # the goal types it serves
    evidence: tuple[EvidenceSource, ...]  # the evidence it requires
    properties: tuple[str, ...]  # the IR properties it may change
    conditions: tuple[str, ...]  # the architecture conditions it applies to
    preconditions: tuple[str, ...]
    benefits: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    unsupported: tuple[str, ...]
    validation: tuple[str, ...]  # what must hold for its candidates (checked on the overlay)
    analyses: tuple[EvidenceSource, ...]  # the analyses that should evaluate its candidates

    def __post_init__(self) -> None:
        unknown = [p for p in self.properties if not specs_of(p)]
        if unknown:
            raise ValueError(f"rule {self.id} changes properties the IR does not define: {unknown}")
        if not self.triggers or not self.goals or not self.properties:
            raise ValueError(f"rule {self.id} must declare its triggers, goals and properties")

    @property
    def ref(self) -> tuple[str, int]:
        return (self.id, self.version)

    def applies(self, trigger: Trigger, goal: EvolutionGoal) -> bool:
        return (trigger.source, trigger.code) in self.triggers and goal.type in self.goals

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "triggers": [{"source": s.value, "code": c} for s, c in self.triggers],
            "goals": [g.value for g in self.goals],
            "evidence": [e.value for e in self.evidence],
            "properties": list(self.properties),
            "conditions": list(self.conditions),
            "preconditions": list(self.preconditions),
            "benefits": list(self.benefits),
            "tradeoffs": list(self.tradeoffs),
            "unsupported": list(self.unsupported),
            "validation": list(self.validation),
            "analyses": [a.value for a in self.analyses],
        }


@dataclass(frozen=True, slots=True)
class RuleContext:
    """What a rule may read: the baseline IR (never modified), its reference, the goals by key and
    the request's constraints."""

    ir: ArchitectureIR
    baseline: BaselineRef
    goals: Mapping[str, EvolutionGoal]
    constraints: EvolutionConstraints = field(default_factory=EvolutionConstraints)


type Outcome = Candidate | EvolutionFinding


class Rule(Protocol):
    meta: RuleMeta

    def propose(self, trigger: Trigger, context: RuleContext) -> tuple[Outcome, ...]: ...


class Registry:
    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        self._rules: dict[str, Rule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: Rule) -> None:
        if rule.meta.id in self._rules:
            raise DuplicateRule(rule.meta.id)
        self._rules[rule.meta.id] = rule

    def rules(self) -> tuple[Rule, ...]:
        return tuple(self._rules[i] for i in sorted(self._rules))

    def applicable(self, trigger: Trigger, goal: EvolutionGoal) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules() if r.meta.applies(trigger, goal))

    def model_set(self) -> ModelSet:
        return ModelSet.of([ENGINE, *(r.meta.ref for r in self.rules())])


@dataclass(frozen=True, slots=True)
class Generation:
    candidates: tuple[Candidate, ...]
    findings: tuple[EvolutionFinding, ...]


def generate(context: RuleContext, triggers: Iterable[Trigger], registry: Registry) -> Generation:
    """Deterministic: the same context, triggers and rules always give the same candidates and
    findings, whatever order the triggers come in."""
    candidates: dict[str, Candidate] = {}
    findings: list[EvolutionFinding] = []
    for trigger in sorted(set(triggers), key=lambda t: t.key):
        goal = context.goals.get(trigger.goal)
        if goal is None:
            raise InvalidEvolutionResult(details={"fields": ["trigger.goal"]})  # a trigger engine bug
        if trigger.kind is TriggerKind.SCALING_UNSUPPORTED:
            findings.append(_structural(trigger))
            continue
        rules = registry.applicable(trigger, goal)
        if not rules:
            findings.append(_unmatched(trigger))
            continue
        for rule in rules:
            for outcome in rule.propose(trigger, context):
                if isinstance(outcome, EvolutionFinding):
                    findings.append(outcome)
                    continue
                _check_output(rule.meta, trigger, context, outcome)
                excluded = _excluded(outcome, trigger, context.constraints)
                if excluded is not None:
                    findings.append(excluded)
                elif outcome.id in candidates:
                    candidates[outcome.id] = _merged(candidates[outcome.id], outcome)
                else:
                    candidates[outcome.id] = outcome
    return Generation(tuple(candidates.values()), tuple(findings))


def _check_output(meta: RuleMeta, trigger: Trigger, context: RuleContext, candidate: Candidate) -> None:
    """A rule's output honours its declaration, or the rule has a bug (never a silent proposal)."""
    problems = [
        None if (candidate.rule.id, candidate.rule.version) == meta.ref else "rule",
        None if candidate.category is meta.category else "category",
        None if candidate.baseline == context.baseline else "baseline",
        None if {c.property for c in candidate.changes} <= set(meta.properties) else "properties",
        None if trigger.goal in candidate.goals else "goals",
        None if trigger.evidence in candidate.evidence else "evidence",
        None if set(candidate.goals) <= set(context.goals) else "goals.unknown",
    ]
    found = [p for p in problems if p]
    if found:
        raise InvalidEvolutionResult(details={"fields": [f"rule.{meta.id}.{p}" for p in found]})


def _excluded(
    candidate: Candidate, trigger: Trigger, constraints: EvolutionConstraints
) -> EvolutionFinding | None:
    reasons: list[str] = []
    frozen = sorted(set(candidate.element_ids) & set(constraints.frozen_elements))
    if frozen:
        reasons.append(f"it changes {', '.join(frozen)}, which the request freezes")
    if candidate.category in constraints.excluded_categories:
        reasons.append(f"the request excludes {candidate.category.value} candidates")
    ceiling = constraints.max_replicas
    too_many = [
        c.element_id
        for c in candidate.changes
        if c.property == "replicas"
        and isinstance(c.value, int | Decimal)
        and ceiling is not None
        and c.value > ceiling
    ]
    if too_many:
        reasons.append(f"it needs more than {ceiling} replicas on {', '.join(sorted(too_many))}")
    if not reasons:
        return None
    return EvolutionFinding(
        FindingType.EXCLUDED_BY_CONSTRAINT,
        f"{candidate.title} is not proposed: {'; '.join(reasons)}.",
        trigger.goal,
        candidate.element_ids,
        (trigger.evidence,),
    )


def _merged(first: Candidate, second: Candidate) -> Candidate:
    return replace(
        first,
        goals=first.goals + second.goals,
        evidence=first.evidence + second.evidence,
        missing=first.missing + second.missing,
    )


def _structural(trigger: Trigger) -> EvolutionFinding:
    return EvolutionFinding(
        FindingType.STRUCTURAL_CONSIDERATION,
        f"{trigger.element_id} is over its target for {trigger.code} and no model states how it scales. "
        "A structural change (for example a cache, a queue, or splitting the component) may be worth "
        "considering; none is proposed, because no model here evaluates one. For human review.",
        trigger.goal,
        (trigger.element_id,),
        (trigger.evidence,),
        (f"{trigger.element_id}: a model of how {trigger.code} scales",),
    )


def _unmatched(trigger: Trigger) -> EvolutionFinding:
    advice = f" The engine's recommendation: {trigger.message}" if trigger.message else ""
    return EvolutionFinding(
        FindingType.NO_APPLICABLE_RULE,
        f"No evolution rule proposes a configuration change for {trigger.code} on "
        f"{trigger.element_id}.{advice}",
        trigger.goal,
        (trigger.element_id,),
        (trigger.evidence,),
    )
