"""What an evolution analysis produces: for one exact baseline revision and a set of goals, the
candidates the rules could support with current evidence, and findings about what could not be
established — alternatives for engineers to review, never a verdict.

- **Findings** say why something is not a candidate: evidence of another revision (``stale_evidence``,
  never used), evidence that does not exist (``missing_evidence``), a goal no engine evaluates
  (``goal_unsupported``) or cannot evaluate from what is declared (``goal_not_evaluable``), a goal
  the current evidence shows is already met (``goal_already_met``), evidence no rule turns into a
  candidate (``no_applicable_rule``), a structural change no model evaluates
  (``structural_consideration``, for human review), or a candidate a request constraint excludes
  (``excluded_by_constraint``).
- **Candidates** are in a canonical order (category, then id): the order is not a ranking. There is
  no score, no weight and no "best" candidate; several candidates for one goal are alternatives.
- The **status** follows what was established: ``insufficient_evidence`` when no goal could be
  evaluated on current evidence, ``completed`` when every goal was and nothing is stale, missing or
  unsupported, ``partial`` otherwise.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from core.domain.engine_results import Evidence, Limitation, ModelSet, read_evidence
from core.domain.errors import DomainError

from .candidates import MAX_REFERENCE, BaselineRef, Candidate, EvidenceRef, _check, _items, _strings, _text
from .errors import InvalidEvolutionResult
from .goals import EvolutionGoal
from .values import EvidenceState, EvolutionStatus

MAX_GOALS = 20
MAX_CANDIDATES = 200
MAX_FINDINGS = 1000
MAX_EVIDENCE = 1000


class FindingType(StrEnum):
    STALE_EVIDENCE = "stale_evidence"
    MISSING_EVIDENCE = "missing_evidence"
    GOAL_UNSUPPORTED = "goal_unsupported"
    GOAL_NOT_EVALUABLE = "goal_not_evaluable"
    GOAL_ALREADY_MET = "goal_already_met"
    NO_APPLICABLE_RULE = "no_applicable_rule"
    STRUCTURAL_CONSIDERATION = "structural_consideration"
    EXCLUDED_BY_CONSTRAINT = "excluded_by_constraint"


NOT_EVALUATED = frozenset({FindingType.GOAL_UNSUPPORTED, FindingType.GOAL_NOT_EVALUABLE})
INCOMPLETE = NOT_EVALUATED | {FindingType.STALE_EVIDENCE, FindingType.MISSING_EVIDENCE}


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EvolutionFinding:
    type: FindingType
    message: str
    goal: str | None = None  # the key of the goal it is about, if any
    element_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    missing: tuple[str, ...] = ()  # what would let the engine decide

    def __post_init__(self) -> None:
        for name in ("element_ids", "missing"):
            value = getattr(self, name)
            if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
                object.__setattr__(self, name, tuple(sorted(set(value))))
        if isinstance(self.evidence, tuple) and all(isinstance(e, EvidenceRef) for e in self.evidence):
            object.__setattr__(self, "evidence", tuple(sorted(set(self.evidence), key=lambda e: e.key)))
        _check(
            [
                None if isinstance(self.type, FindingType) else "finding.type",
                _text(self.message, "finding.message"),
                _text(self.goal, "finding.goal", MAX_REFERENCE, required=False),
                _strings(self.element_ids, "finding.element_ids"),
                _items(self.evidence, EvidenceRef, "finding.evidence"),
                _strings(self.missing, "finding.missing"),
            ]
        )

    @property
    def id(self) -> str:
        identity = [self.type.value, self.goal, list(self.element_ids), [list(e.key) for e in self.evidence]]
        return f"evf_{_hash(identity)[:16]}"

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.goal or "", self.type.value, self.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "message": self.message,
            "goal": self.goal,
            "element_ids": list(self.element_ids),
            "evidence": [e.to_dict() for e in self.evidence],
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        return cls(
            FindingType(data["type"]),
            data["message"],
            data.get("goal"),
            tuple(data.get("element_ids") or ()),
            tuple(EvidenceRef.from_dict(e) for e in data.get("evidence") or ()),
            tuple(data.get("missing") or ()),
        )


@dataclass(frozen=True, slots=True)
class EvolutionResult:
    baseline: BaselineRef
    model_set: ModelSet  # the evolution engine and each rule, with their versions
    goals: tuple[EvolutionGoal, ...]
    candidates: tuple[Candidate, ...] = ()
    findings: tuple[EvolutionFinding, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()  # everything considered: current, stale and missing
    assumptions: tuple[Evidence, ...] = ()  # stated, never computed with
    limitations: tuple[Limitation, ...] = ()

    def __post_init__(self) -> None:
        self._canonical()
        goals = {g.key for g in self.goals} if _items(self.goals, EvolutionGoal, "goals") is None else set()
        _check(
            [
                None if isinstance(self.baseline, BaselineRef) else "baseline",
                None if isinstance(self.model_set, ModelSet) else "model_set",
                None if isinstance(self.goals, tuple) and 0 < len(self.goals) <= MAX_GOALS else "goals",
                _items(self.goals, EvolutionGoal, "goals", limit=MAX_GOALS),
                None if len(goals) == len(self.goals) else "goals.duplicate",
                _items(self.candidates, Candidate, "candidates", limit=MAX_CANDIDATES),
                _items(self.findings, EvolutionFinding, "findings", limit=MAX_FINDINGS),
                _items(self.evidence, EvidenceRef, "evidence", limit=MAX_EVIDENCE),
                _items(self.assumptions, Evidence, "assumptions"),
                _items(self.limitations, Limitation, "limitations"),
                *self._references(goals),
            ]
        )

    def _canonical(self) -> None:
        sort = object.__setattr__
        if isinstance(self.goals, tuple) and all(isinstance(g, EvolutionGoal) for g in self.goals):
            sort(self, "goals", tuple(sorted(self.goals, key=lambda g: g.key)))
        if isinstance(self.candidates, tuple) and all(isinstance(c, Candidate) for c in self.candidates):
            sort(self, "candidates", tuple(sorted(self.candidates, key=lambda c: (c.category.value, c.id))))
        if isinstance(self.findings, tuple) and all(isinstance(f, EvolutionFinding) for f in self.findings):
            sort(self, "findings", tuple(sorted(set(self.findings), key=lambda f: f.sort_key)))
        if isinstance(self.evidence, tuple) and all(isinstance(e, EvidenceRef) for e in self.evidence):
            sort(self, "evidence", tuple(sorted(set(self.evidence), key=lambda e: e.key)))
        if isinstance(self.limitations, tuple) and all(isinstance(x, Limitation) for x in self.limitations):
            sort(self, "limitations", tuple(sorted(set(self.limitations), key=lambda x: x.code)))

    def _references(self, goals: set[str]) -> list[str | None]:
        if not isinstance(self.candidates, tuple) or not isinstance(self.findings, tuple):
            return []
        candidates = [c for c in self.candidates if isinstance(c, Candidate)]
        findings = [f for f in self.findings if isinstance(f, EvolutionFinding)]
        return [
            None if len({c.id for c in candidates}) == len(candidates) else "candidates.duplicate",
            None if all(c.baseline == self.baseline for c in candidates) else "candidates.baseline",
            None if all(set(c.goals) <= goals for c in candidates) else "candidates.goals",
            None if all(f.goal is None or f.goal in goals for f in findings) else "findings.goal",
        ]

    # --- what the result establishes -------------------------------------------------------------

    @property
    def unsupported_goals(self) -> tuple[str, ...]:
        """The goals no engine could evaluate (unsupported, or not evaluable from what is declared)."""
        return tuple(sorted({f.goal for f in self.findings if f.type in NOT_EVALUATED and f.goal}))

    @property
    def missing(self) -> tuple[str, ...]:
        """Every input that would let the engine decide more, from findings and candidates."""
        found = {m for f in self.findings for m in f.missing} | {
            m for c in self.candidates for m in c.missing
        }
        return tuple(sorted(found))

    @property
    def status(self) -> EvolutionStatus:
        evaluated = {g.key for g in self.goals} - set(self.unsupported_goals)
        if not evaluated:
            return EvolutionStatus.INSUFFICIENT_EVIDENCE
        if any(f.type in INCOMPLETE for f in self.findings):
            return EvolutionStatus.PARTIAL
        return EvolutionStatus.COMPLETED

    def summary(self) -> dict[str, Any]:
        """Counts only, reproducible from the goals, candidates, findings and evidence: no score."""
        findings = Counter(f.type.value for f in self.findings)
        evidence = Counter(e.state.value for e in self.evidence)
        per_goal = Counter(g for c in self.candidates for g in c.goals)
        return {
            "goals": len(self.goals),
            "unsupported_goals": len(self.unsupported_goals),
            "candidates": len(self.candidates),
            "candidates_by_category": dict(
                sorted(Counter(c.category.value for c in self.candidates).items())
            ),
            "candidates_by_goal": {g.key: per_goal.get(g.key, 0) for g in self.goals},
            "findings": {t.value: findings.get(t.value, 0) for t in FindingType},
            "evidence": {s.value: evidence.get(s.value, 0) for s in EvidenceState},
            "missing": len(self.missing),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "model_set": self.model_set.to_dict(),
            "status": self.status.value,
            "goals": [g.to_dict() for g in self.goals],
            "candidates": [c.to_dict() for c in self.candidates],
            "findings": [f.to_dict() for f in self.findings],
            "evidence": [e.to_dict() for e in self.evidence],
            "assumptions": [e.to_dict() for e in self.assumptions],
            "limitations": [x.to_dict() for x in self.limitations],
            "unsupported_goals": list(self.unsupported_goals),
            "missing": list(self.missing),
        }

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                baseline=BaselineRef.from_dict(data["baseline"]),
                model_set=ModelSet.from_dict(data["model_set"]),
                goals=tuple(EvolutionGoal.from_dict(g) for g in data["goals"]),
                candidates=tuple(Candidate.from_dict(c) for c in data.get("candidates") or ()),
                findings=tuple(EvolutionFinding.from_dict(f) for f in data.get("findings") or ()),
                evidence=tuple(EvidenceRef.from_dict(e) for e in data.get("evidence") or ()),
                assumptions=read_evidence(data.get("assumptions")),
                limitations=tuple(Limitation.from_dict(x) for x in data.get("limitations") or ()),
            )
        except InvalidEvolutionResult:
            raise
        except (DomainError, KeyError, TypeError, ValueError) as error:
            raise InvalidEvolutionResult(details={"fields": [type(error).__name__]}) from None
