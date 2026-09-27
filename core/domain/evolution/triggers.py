"""A trigger: one reason, found in current evidence, why the architecture may need to evolve toward a
goal — a scaling option a capacity model states, a finding another engine reported, or a limit no
model can scale past. Triggers are what the evolution rules read; they never carry urgency.

- ``kind``: a ``scaling_option`` (a model states the replicas or resources that would bring a
  resource within its target), a ``finding`` (a stable finding of reliability, security,
  observability or validation), or ``scaling_unsupported`` (a resource is over its target and no
  model states how it scales: only a structural change could help, which no model evaluates).
- ``code``: the finding type, or the resource (``work_rate``, ``cpu``, …).
- ``facts``: what the evidence states, as labelled strings (e.g. ``current``: ``2 replicas``,
  ``required``: ``4 replicas``, ``scaling``: ``horizontal``), copied from the engine's own result.
- ``evidence``: the stored item it comes from, always ``current`` for the baseline.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from core.domain.engine_results import Evidence, read_evidence

from .candidates import MAX_REFERENCE, EvidenceRef, _check, _code, _items, _text
from .values import EvidenceSource, EvidenceState


class TriggerKind(StrEnum):
    SCALING_OPTION = "scaling_option"
    FINDING = "finding"
    SCALING_UNSUPPORTED = "scaling_unsupported"


@dataclass(frozen=True, slots=True)
class Trigger:
    kind: TriggerKind
    source: EvidenceSource
    code: str
    goal: str  # the key of the goal it is relevant to
    element_id: str  # the node or connection it concerns
    evidence: EvidenceRef
    facts: tuple[Evidence, ...] = ()
    message: str | None = None  # the engine's own words (e.g. a finding's recommendation)

    def __post_init__(self) -> None:
        if isinstance(self.facts, tuple) and all(isinstance(f, Evidence) for f in self.facts):
            object.__setattr__(self, "facts", tuple(sorted(set(self.facts), key=lambda f: f.label)))
        _check(
            [
                None if isinstance(self.kind, TriggerKind) else "trigger.kind",
                None if isinstance(self.source, EvidenceSource) else "trigger.source",
                _code(self.code, "trigger.code"),
                _text(self.goal, "trigger.goal", MAX_REFERENCE),
                _text(self.element_id, "trigger.element_id", MAX_REFERENCE),
                None
                if isinstance(self.evidence, EvidenceRef) and self.evidence.state is EvidenceState.CURRENT
                else "trigger.evidence",
                _items(self.facts, Evidence, "trigger.facts"),
                _text(self.message, "trigger.message", required=False),
            ]
        )

    def fact(self, label: str) -> str | None:
        return next((f.value for f in self.facts if f.label == label), None)

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        """Canonical order: goal, then source, kind, element and code."""
        return (self.goal, self.source.value, self.kind.value, self.element_id, self.code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source": self.source.value,
            "code": self.code,
            "goal": self.goal,
            "element_id": self.element_id,
            "evidence": self.evidence.to_dict(),
            "facts": [f.to_dict() for f in self.facts],
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        return cls(
            TriggerKind(data["kind"]),
            EvidenceSource(data["source"]),
            data["code"],
            data["goal"],
            data["element_id"],
            EvidenceRef.from_dict(data["evidence"]),
            read_evidence(data.get("facts")),
            data.get("message"),
        )
