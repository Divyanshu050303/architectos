"""A stored architecture diff: two exact states, what changed between them, and what that touches.

Immutable once produced: the request, both states (by reference and content hash — never copies of the
architectures), the semantic diff with its groups, the requirement and decision impacts and the
engines' comparison. AI explanations are separate, appended runs (``ExplanationRun``): the deterministic
diff never changes because an explanation was asked for, and never depends on one.

Every impact points at changes of this diff: a requirement or decision cannot cite a change that is
not in it.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .changes import SemanticDiff
from .impacts import DecisionImpact, EngineImpact, RequirementImpact
from .references import ComparedState, DiffRequest
from .values import check, items, texts

MAX_REQUIREMENTS = 1000
MAX_DECISIONS = 500


def _unique(values: list[Any], name: str) -> str | None:
    return name if len(set(values)) != len(values) else None


@dataclass(frozen=True, slots=True)
class ArchitectureDiff:
    id: uuid.UUID
    project_id: uuid.UUID
    request: DiffRequest
    base: ComparedState
    target: ComparedState
    semantic: SemanticDiff
    requested_by_user_id: uuid.UUID
    created_at: datetime
    requirements: tuple[RequirementImpact, ...] = ()
    decisions: tuple[DecisionImpact, ...] = ()
    engines: tuple[EngineImpact, ...] = ()
    warnings: tuple[str, ...] = ()  # e.g. the states belong to different architectures
    unknowns: tuple[str, ...] = ()  # what could not be determined, said

    def __post_init__(self) -> None:
        typed = (
            isinstance(self.request, DiffRequest)
            and isinstance(self.base, ComparedState)
            and isinstance(self.target, ComparedState)
            and isinstance(self.semantic, SemanticDiff)
        )
        check(
            [
                None if typed else "diff.parts",
                items(self.requirements, RequirementImpact, "diff.requirements", MAX_REQUIREMENTS),
                items(self.decisions, DecisionImpact, "diff.decisions", MAX_DECISIONS),
                items(self.engines, EngineImpact, "diff.engines", 10),
                texts(self.warnings, "diff.warnings", 500),
                texts(self.unknowns, "diff.unknowns", 500),
            ]
        )
        known = {c.id for c in self.semantic.changes}
        cited = {
            *(c for r in self.requirements for c in r.change_ids),
            *(c for d in self.decisions for c in d.change_ids),
        }
        check(
            [
                None
                if self.request.base == self.base.ref and self.request.target == self.target.ref
                else "diff.states",
                None if self.semantic.base_hash == self.base.content_hash else "diff.semantic",
                None if self.semantic.target_hash == self.target.content_hash else "diff.semantic",
                _unique([r.requirement_id for r in self.requirements], "diff.requirements"),
                _unique([d.decision_id for d in self.decisions], "diff.decisions"),
                _unique([e.engine for e in self.engines], "diff.engines"),
                "diff.impacts" if not cited <= known else None,  # impacts cite only this diff's changes
            ]
        )

    @property
    def identical(self) -> bool:
        return self.semantic.identical

    def engine(self, name: str) -> EngineImpact | None:
        return next((e for e in self.engines if e.engine == name), None)
