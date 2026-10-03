"""What a person asks to compare: two exact architecture states, and how far to analyze them.

A state is named explicitly — never "the latest", never by name:

- **a revision**: an architecture of the project and a revision number (immutable);
- **a candidate**: an architecture agent run of the project whose candidate exists (it is immutable
  once stored; its content hash is what was reviewed).

Once resolved, a ``ComparedState`` records exactly what was compared (its content hash), so a stored
diff names its two sides without copying either architecture.

**Capacity and cost** are compared only on inputs the person names: a stored capacity analysis (its
workload) and a pricing snapshot. Without them those engines are not evaluated — never estimated.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from .errors import InvalidDiffRequest
from .values import FINGERPRINT, StateKind, check, count, text

MAX_CONTEXT = 2000
MAX_SCOPE = 200


def _invalid(field_name: str, reason: str) -> InvalidDiffRequest:
    return InvalidDiffRequest(details={"field": field_name, "reason": reason})


@dataclass(frozen=True, slots=True)
class StateRef:
    kind: StateKind
    architecture_id: uuid.UUID | None = None  # a revision's architecture
    revision_number: int | None = None
    run_id: uuid.UUID | None = None  # a candidate's agent run

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StateKind):
            raise _invalid("kind", "unsupported")
        if self.kind is StateKind.REVISION:
            if not isinstance(self.architecture_id, uuid.UUID) or self.run_id is not None:
                raise _invalid("architecture_id", "required")
            if count(self.revision_number, "revision_number", minimum=1):
                raise _invalid("revision_number", "required")
        elif not isinstance(self.run_id, uuid.UUID) or self.architecture_id or self.revision_number:
            raise _invalid("run_id", "required")

    @classmethod
    def revision(cls, architecture_id: uuid.UUID, number: int) -> StateRef:
        return cls(StateKind.REVISION, architecture_id=architecture_id, revision_number=number)

    @classmethod
    def candidate(cls, run_id: uuid.UUID) -> StateRef:
        return cls(StateKind.CANDIDATE, run_id=run_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "architecture_id": str(self.architecture_id) if self.architecture_id else None,
            "revision_number": self.revision_number,
            "run_id": str(self.run_id) if self.run_id else None,
        }


def _hash(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and FINGERPRINT.fullmatch(value) else name


@dataclass(frozen=True, slots=True)
class ComparedState:
    """A state as it was compared: its reference, its content hash and how to show it."""

    ref: StateRef
    content_hash: str
    label: str  # e.g. "Orders v3", "Agent candidate (run 0190…)"

    def __post_init__(self) -> None:
        check(
            [
                None if isinstance(self.ref, StateRef) else "state.ref",
                _hash(self.content_hash, "state.content_hash"),
                text(self.label, "state.label", 200),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return self.ref.to_dict() | {"content_hash": self.content_hash, "label": self.label}


def _ids(value: object) -> bool:
    return (
        isinstance(value, tuple) and len(value) <= MAX_SCOPE and all(isinstance(r, uuid.UUID) for r in value)
    )


@dataclass(frozen=True, slots=True)
class DiffRequest:
    base: StateRef
    target: StateRef
    requirement_ids: tuple[uuid.UUID, ...] | None = None  # narrows requirement impact; None: all traced
    capacity_analysis_id: uuid.UUID | None = None  # its workload, for comparing capacity
    pricing_snapshot_id: uuid.UUID | None = None  # for comparing cost (needs capacity too)
    context: str | None = None  # the person's own words about the change, for the explanation
    explain: bool = False  # ask for the AI interpretation now

    def __post_init__(self) -> None:
        if not isinstance(self.base, StateRef) or not isinstance(self.target, StateRef):
            raise _invalid("base", "required")
        if self.base == self.target:
            raise _invalid("target", "same_as_base")
        if self.requirement_ids is not None:
            if not _ids(self.requirement_ids):
                raise _invalid("requirement_ids", "invalid")
            object.__setattr__(self, "requirement_ids", tuple(sorted(set(self.requirement_ids))))
        if self.pricing_snapshot_id is not None and self.capacity_analysis_id is None:
            raise _invalid("pricing_snapshot_id", "needs_capacity_analysis")  # cost reads a capacity basis
        if self.context is not None:
            if not isinstance(self.context, str) or len(self.context) > MAX_CONTEXT:
                raise _invalid("context", "too_long")
            object.__setattr__(self, "context", self.context.strip() or None)
        if not isinstance(self.explain, bool):
            raise _invalid("explain", "invalid")

    def to_dict(self) -> dict[str, Any]:
        scope = self.requirement_ids
        return {
            "base": self.base.to_dict(),
            "target": self.target.to_dict(),
            "requirement_ids": [str(r) for r in scope] if scope is not None else None,
            "capacity_analysis_id": str(self.capacity_analysis_id) if self.capacity_analysis_id else None,
            "pricing_snapshot_id": str(self.pricing_snapshot_id) if self.pricing_snapshot_id else None,
            "context": self.context,
            "explain": self.explain,
        }
