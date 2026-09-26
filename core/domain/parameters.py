"""Typed parameters an engine component (a validation rule, a capacity model) declares, and the
check of the values a request gives them. Parameters are data: a value is accepted only if it has
the declared type and bounds; nothing in them is ever executed."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

MAX_TEXT_PARAMETER = 200


class ParamType(StrEnum):
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    type: ParamType
    description: str
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    choices: frozenset[str] = frozenset()

    def problem(self, value: object) -> str | None:
        match self.type:
            case ParamType.INTEGER:
                if isinstance(value, bool) or not isinstance(value, int):
                    return "not_an_integer"
                too_small = self.minimum is not None and value < self.minimum
                too_large = self.maximum is not None and value > self.maximum
                return "out_of_range" if too_small or too_large else None
            case ParamType.BOOLEAN:
                return None if isinstance(value, bool) else "not_a_boolean"
            case ParamType.TEXT:
                if not isinstance(value, str) or len(value) > MAX_TEXT_PARAMETER:
                    return "not_text"
                return "not_a_choice" if self.choices and value not in self.choices else None


def parameter_problem(specs: Mapping[str, ParamSpec], given: Mapping[str, Any]) -> tuple[str, str] | None:
    """The first (parameter, reason) wrong in ``given``, in name order; None when all are valid."""
    for name, value in sorted(given.items()):
        spec = specs.get(name)
        if spec is None:
            return name, "unknown_parameter"
        problem = spec.problem(value)
        if problem is not None:
            return name, problem
    return None


def with_defaults(specs: Mapping[str, ParamSpec], given: Mapping[str, Any]) -> dict[str, Any]:
    """Every declared parameter: the given value, else its declared default."""
    return {name: given.get(name, spec.default) for name, spec in sorted(specs.items())}
