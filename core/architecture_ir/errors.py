"""Structural violations of the Architecture IR, reported precisely.

A violation names the element (node, connection, …) and its id, the field, the rule broken (a
stable code clients may branch on) and a sentence a person can act on. Every IR type exposes its
violations through ``problems()``; constructors raise ``InvalidArchitecture`` with all of them.

Structural validity only: a valid IR is well-formed, not necessarily a good architecture.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from core.domain.errors import DomainError


class ElementType(StrEnum):
    ARCHITECTURE = "architecture"
    NODE = "node"
    CONNECTION = "connection"
    ASSUMPTION = "assumption"
    DECISION = "decision"


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str  # stable lower_snake code, e.g. "dangling_reference"
    message: str
    field: str | None = None  # path within the element, e.g. "configuration.replicas"
    element: ElementType | None = None
    element_id: str | None = None

    def within(self, element: ElementType, element_id: str | None, prefix: str | None = None) -> Violation:
        """The same violation placed inside an element, under ``prefix`` in it. A violation that
        already names its element is left as it is."""
        if self.element is not None:
            return self
        field = f"{prefix}.{self.field}" if prefix and self.field else (prefix or self.field)
        return replace(self, element=element, element_id=element_id, field=field)

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.element or "", self.element_id or "", self.field or "", self.rule)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.element.value if self.element else None,
            "element_id": self.element_id,
            "field": self.field,
            "rule": self.rule,
            "message": self.message,
        }


def ordered(violations: Iterable[Violation]) -> tuple[Violation, ...]:
    """Deduplicated, in a stable order (by element, id, field, rule)."""
    return tuple(sorted(dict.fromkeys(violations), key=Violation.sort_key))


class InvalidArchitecture(DomainError):
    """``details`` = {"violations": [{element, element_id, field, rule, message}, …]}."""

    code = "invalid_architecture"
    message = "The architecture is invalid."

    def __init__(self, violations: Iterable[Violation]) -> None:
        self.violations = ordered(violations)
        first = self.violations[0].message if self.violations else self.message
        more = len(self.violations) - 1
        text = f"{first} (and {more} more problem{'s' if more > 1 else ''})" if more > 0 else first
        super().__init__(text, details={"violations": [v.to_dict() for v in self.violations]})


def raise_if(violations: Iterable[Violation]) -> None:
    found = list(violations)
    if found:
        raise InvalidArchitecture(found)
