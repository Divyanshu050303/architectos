"""Structural validation of an architecture document, without raising.

``validate`` answers "is this a well-formed Architecture IR, and if not, what exactly is wrong?"
for any input (an API body, an import, a model's proposal after conversion). It never judges the
architecture itself: capacity, reliability or cost are the engines' questions, and a valid IR
makes no claim about being production-ready.

``requirement_problems`` checks traceability against the requirements that actually exist, when
they are known (they live in another domain, so the caller provides them).
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .errors import ElementType, InvalidArchitecture, Violation, ordered
from .model import ArchitectureIR
from .serialization import from_dict
from .traceability import RequirementRef


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ir: ArchitectureIR | None
    violations: tuple[Violation, ...]

    @property
    def valid(self) -> bool:
        return self.ir is not None and not self.violations


def validate(data: object) -> ValidationResult:
    try:
        return ValidationResult(from_dict(data), ())
    except InvalidArchitecture as error:
        return ValidationResult(None, error.violations)


def _referencing(
    ir: ArchitectureIR,
) -> Iterable[tuple[ElementType, str | None, tuple[RequirementRef, ...]]]:
    yield ElementType.ARCHITECTURE, None, ir.requirement_refs
    for node in ir.nodes:
        yield ElementType.NODE, node.id, node.requirement_refs
    for connection in ir.connections:
        yield ElementType.CONNECTION, connection.id, connection.requirement_refs
    for assumption in ir.assumptions:
        yield ElementType.ASSUMPTION, assumption.id, assumption.requirement_refs


def requirement_problems(ir: ArchitectureIR, available: Mapping[uuid.UUID, int]) -> tuple[Violation, ...]:
    """References to requirements that do not exist (``available`` maps each existing requirement
    of the project to its current version), or to versions they never had."""
    problems: list[Violation] = []
    for element, element_id, refs in _referencing(ir):
        for ref in refs:
            current = available.get(ref.requirement_id)
            if current is None:
                problems.append(
                    Violation(
                        "unknown_requirement",
                        f"Requirement {ref.requirement_id} does not exist in this project.",
                        "requirement_refs",
                        element,
                        element_id,
                    )
                )
            elif ref.version is not None and ref.version > current:
                problems.append(
                    Violation(
                        "unknown_requirement_version",
                        f"Requirement {ref.requirement_id} has no version {ref.version} (latest: {current}).",
                        "requirement_refs",
                        element,
                        element_id,
                    )
                )
    return ordered(problems)
