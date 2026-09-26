"""What an architecture element answers to: requirements, decisions and assumptions.

The IR *references* requirements and decisions by their stable identifiers; it never copies them.
The Requirements Engine owns requirements (and their versions), the decisions domain owns ADRs.
Assumptions are part of the architecture: statements taken as true without verification, always
with their provenance, never silently turned into facts.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from .errors import Violation, raise_if
from .provenance import Provenance
from .values import clean_block, id_problems, text_problems

MAX_REFERENCES = 100
MAX_ASSUMPTION_LENGTH = 1000
MAX_SUBJECTS = 100


@dataclass(frozen=True, slots=True)
class RequirementRef:
    """A canonical requirement, optionally pinned to one of its versions."""

    requirement_id: uuid.UUID
    version: int | None = None  # None: the requirement as it evolves; n: exactly version n

    def __post_init__(self) -> None:
        raise_if(self.problems())

    def sort_key(self) -> tuple[str, int]:
        return (str(self.requirement_id), self.version or 0)

    def problems(self) -> list[Violation]:
        problems: list[Violation] = []
        if not isinstance(self.requirement_id, uuid.UUID):
            problems.append(
                Violation("invalid_reference", "requirement_id must be a requirement's id.", "requirement_id")
            )
        if self.version is not None and (
            isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1
        ):
            problems.append(
                Violation("invalid_reference", "version must be a positive whole number.", "version")
            )
        return problems


def sorted_refs(refs: Iterable[RequirementRef]) -> tuple[RequirementRef, ...]:
    """Deduplicated, in a stable order."""
    return tuple(sorted(set(refs), key=RequirementRef.sort_key))


def normalized_refs(refs: object) -> object:
    """``refs`` sorted and deduplicated if they are requirement references, else as given (for
    ``refs_problems`` to report)."""
    if isinstance(refs, tuple) and all(isinstance(r, RequirementRef) for r in refs):
        return sorted_refs(refs)
    return refs


def refs_problems(refs: object, field_name: str = "requirement_refs") -> list[Violation]:
    if not isinstance(refs, tuple) or not all(isinstance(r, RequirementRef) for r in refs):
        return [Violation("invalid_reference", f"{field_name} must be requirement references.", field_name)]
    if len(refs) > MAX_REFERENCES:
        return [Violation("too_many", f"{field_name} has more than {MAX_REFERENCES} references.", field_name)]
    return []


def normalized_ids(ids: object) -> object:
    if isinstance(ids, tuple) and all(isinstance(i, str) for i in ids):
        return tuple(sorted(set(ids)))
    return ids


def subject_problems(subject_ids: object, field_name: str = "subject_ids") -> list[Violation]:
    if not isinstance(subject_ids, tuple):
        return [Violation("invalid_reference", f"{field_name} must be element ids.", field_name)]
    if len(subject_ids) > MAX_SUBJECTS:
        return [Violation("too_many", f"{field_name} has more than {MAX_SUBJECTS} ids.", field_name)]
    return [p for i, s in enumerate(subject_ids) for p in id_problems(s, f"{field_name}[{i}]")]


@dataclass(frozen=True, slots=True)
class DecisionRef:
    """An architecture decision (owned by the decisions domain) and the elements it concerns."""

    decision_id: uuid.UUID
    subject_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_ids", normalized_ids(self.subject_ids))
        raise_if(self.problems())

    def problems(self) -> list[Violation]:
        problems = subject_problems(self.subject_ids)
        if not isinstance(self.decision_id, uuid.UUID):
            problems.append(
                Violation("invalid_reference", "decision_id must be a decision's id.", "decision_id")
            )
        return problems


@dataclass(frozen=True, slots=True)
class Assumption:
    """Something taken as true without verification, e.g. "peak traffic is 3x the daily average".
    Its provenance says who assumed it; ``subject_ids`` are the elements that rely on it."""

    id: str
    statement: str
    provenance: Provenance
    subject_ids: tuple[str, ...] = ()
    requirement_refs: tuple[RequirementRef, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.statement, str):
            object.__setattr__(self, "statement", clean_block(self.statement))
        object.__setattr__(self, "subject_ids", normalized_ids(self.subject_ids))
        object.__setattr__(self, "requirement_refs", normalized_refs(self.requirement_refs))
        raise_if(self.problems())

    def problems(self) -> list[Violation]:
        problems = id_problems(self.id, "id")
        problems += text_problems(
            self.statement, "statement", MAX_ASSUMPTION_LENGTH, required=True, block=True
        )
        if not isinstance(self.provenance, Provenance):
            problems.append(Violation("required", "An assumption must say where it came from.", "provenance"))
        problems += subject_problems(self.subject_ids)
        problems += refs_problems(self.requirement_refs)
        return problems
