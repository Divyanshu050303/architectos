"""Constraints of a component specification: what a configuration of the technology must (or should)
respect, typed by how binding the source says it is.

Types — never promoted: a recommendation is never treated as a limit.

- ``hard_limit``: the technology cannot exceed it (documented).
- ``configurable_limit``: a documented default or quota that can be raised (e.g. by a service quota
  request) — exceeding it needs that change, it is not impossible.
- ``conditional_limit``: a documented limit that holds only under stated ``conditions`` (e.g. for
  one storage type).
- ``recommended_range``: the source recommends it; outside it is a warning, never a violation of a
  limit (at most ``medium`` severity).
- ``unsupported_configuration``: values of a property the technology does not support.
- ``unknown``: a limit is known to exist but its value is not documented here: an evaluation says it
  cannot be evaluated, never that it passed.

Each constraint bounds one Architecture IR property (its unit is part of the property's name) with a
comparison (``at_most``, ``at_least``, ``between``, ``one_of``, ``not_one_of``), a severity when
violated (the Validation Engine's scale), applicability ``conditions``, the technology versions it
was checked for, remediation guidance and its provenance.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from functools import partial
from typing import Any, Self

from core.architecture_ir.configuration import NODE_PROPERTIES, ConfigValue, ValueType
from core.domain.validation.results import Severity

from .entities import (
    MAX_NAME,
    Provenance,
    ProvenanceKind,
    as_tuple,
    check,
    code,
    items,
    read_provenance,
    strict,
    text,
    texts,
    within,
)

NUMERIC = frozenset({ValueType.INTEGER, ValueType.DECIMAL})
MAX_VALUES = 50


class ConstraintType(StrEnum):
    HARD_LIMIT = "hard_limit"
    CONFIGURABLE_LIMIT = "configurable_limit"
    CONDITIONAL_LIMIT = "conditional_limit"
    RECOMMENDED_RANGE = "recommended_range"
    UNSUPPORTED_CONFIGURATION = "unsupported_configuration"
    UNKNOWN = "unknown"


class Comparison(StrEnum):
    AT_MOST = "at_most"  # value <= limit
    AT_LEAST = "at_least"  # value >= limit
    BETWEEN = "between"  # minimum <= value <= maximum
    ONE_OF = "one_of"  # value in values
    NOT_ONE_OF = "not_one_of"  # value not in values


BOUNDS = frozenset({Comparison.AT_MOST, Comparison.AT_LEAST, Comparison.BETWEEN})
LISTS = frozenset({Comparison.ONE_OF, Comparison.NOT_ONE_OF})
# A recommendation, or a limit that can be raised, is never as severe as an impossibility.
ADVISORY = frozenset({ConstraintType.RECOMMENDED_RANGE, ConstraintType.CONFIGURABLE_LIMIT})
ADVISORY_SEVERITIES = frozenset({Severity.MEDIUM, Severity.LOW, Severity.INFO})


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool | float):
        raise TypeError("not an exact number")
    return Decimal(str(value).strip())


def _value(value: object, prop: str) -> ConfigValue:
    """A configuration value as the IR types it (decimals from text; no floats)."""
    if isinstance(value, float):
        raise TypeError("floats are not exact")
    decimal = NODE_PROPERTIES[prop].type is ValueType.DECIMAL
    if decimal and isinstance(value, str | int) and not isinstance(value, bool):
        return Decimal(str(value).strip())
    return value  # type: ignore[return-value]  # checked by the property's specification


def _json(value: object) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def _valid_values(prop: str, values: tuple[ConfigValue, ...]) -> bool:
    spec = NODE_PROPERTIES[prop]
    return 0 < len(values) <= MAX_VALUES and all(not spec.problems(v, prop) for v in values)


@dataclass(frozen=True, slots=True)
class Condition:
    """The constraint applies only when ``property`` has one of ``values``."""

    property: str
    values: tuple[ConfigValue, ...]

    def __post_init__(self) -> None:
        known = isinstance(self.property, str) and self.property in NODE_PROPERTIES
        valid = known and isinstance(self.values, tuple) and _valid_values(self.property, self.values)
        check([None if known else "property", None if valid else "values"])

    def holds(self, configured: ConfigValue | None) -> bool | None:
        """Whether the condition holds for a configured value; ``None`` when the value is not stated."""
        return None if configured is None else configured in self.values

    def to_dict(self) -> dict[str, Any]:
        return {"property": self.property, "values": [_json(v) for v in self.values]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        prop = data["property"]
        values = as_tuple(data.get("values"))
        typed = tuple(_value(v, prop) for v in values) if prop in NODE_PROPERTIES else values
        return cls(prop, typed)


@dataclass(frozen=True, slots=True)
class Constraint:
    id: str
    type: ConstraintType
    description: str
    property: str  # the Architecture IR node property it bounds
    severity: Severity  # when violated
    provenance: Provenance
    comparison: Comparison | None = None  # None only for an unknown constraint
    limit: Decimal | None = None  # at_most, at_least
    minimum: Decimal | None = None  # between
    maximum: Decimal | None = None  # between
    values: tuple[ConfigValue, ...] = ()  # one_of, not_one_of
    conditions: tuple[Condition, ...] = ()
    technology_versions: tuple[str, ...] = ()  # the versions it was checked for (none: any)
    remediation: str | None = None

    def __post_init__(self) -> None:
        spec = NODE_PROPERTIES.get(self.property) if isinstance(self.property, str) else None
        provenance = self.provenance if isinstance(self.provenance, Provenance) else None
        check(
            [
                code(self.id, "id"),
                None if isinstance(self.type, ConstraintType) else "type",
                text(self.description, "description"),
                None if spec is not None else "property",
                None if isinstance(self.severity, Severity) else "severity",
                None if provenance is not None else "provenance",
                None if self.comparison is None or isinstance(self.comparison, Comparison) else "comparison",
                items(self.conditions, Condition, "conditions"),
                texts(self.technology_versions, "technology_versions", MAX_NAME),
                text(self.remediation, "remediation", required=False),
            ]
        )
        if spec is None or provenance is None:  # already refused above
            return
        check(self._semantics(spec.type, provenance))

    def _semantics(self, value_type: ValueType, provenance: Provenance) -> list[str | None]:
        kind, comparison = self.type, self.comparison
        numbers = (self.limit, self.minimum, self.maximum)
        stated = [n for n in numbers if n is not None]
        unknown = kind is ConstraintType.UNKNOWN
        single = comparison in {Comparison.AT_MOST, Comparison.AT_LEAST}
        between = comparison is Comparison.BETWEEN
        return [
            # an unknown limit states nothing to compare with, and rests on no evidence
            "comparison" if unknown and comparison is not None else None,
            "comparison" if not unknown and comparison is None else None,
            "provenance" if unknown and provenance.is_known else None,
            # every stated limit is documented: never inferred, estimated or assumed
            "provenance" if not unknown and provenance.kind is not ProvenanceKind.DOCUMENTED else None,
            "limit" if unknown and (stated or self.values) else None,
            # the comparison fits the property and says what it compares with
            "comparison" if comparison in BOUNDS and value_type not in NUMERIC else None,
            "limit" if single and (self.limit is None or len(stated) != 1 or self.values) else None,
            "minimum"
            if between and (self.minimum is None or self.maximum is None or self.limit is not None)
            else None,
            "maximum"
            if between
            and self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
            else None,
            "limit"
            if value_type is ValueType.INTEGER and any(n != n.to_integral_value() for n in stated)
            else None,
            "limit" if any(n < 0 for n in stated) else None,
            "values"
            if comparison in LISTS and (not _valid_values(self.property, self.values) or stated)
            else None,
            "values" if comparison in BOUNDS and self.values else None,
            "comparison"
            if kind is ConstraintType.UNSUPPORTED_CONFIGURATION and comparison is not Comparison.NOT_ONE_OF
            else None,
            # a conditional limit states its conditions; the others hold unconditionally
            "conditions" if (kind is ConstraintType.CONDITIONAL_LIMIT) != bool(self.conditions) else None,
            # a recommendation or a raisable default is never as severe as an impossibility
            "severity" if kind in ADVISORY and self.severity not in ADVISORY_SEVERITIES else None,
        ]

    def properties(self) -> tuple[str, ...]:
        """The IR properties it reads: the one it bounds and those of its conditions."""
        return (self.property, *(c.property for c in self.conditions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "description": self.description,
            "property": self.property,
            "comparison": self.comparison.value if self.comparison else None,
            "limit": _json(self.limit),
            "minimum": _json(self.minimum),
            "maximum": _json(self.maximum),
            "values": [_json(v) for v in self.values],
            "severity": self.severity.value,
            "conditions": [c.to_dict() for c in self.conditions],
            "technology_versions": list(self.technology_versions),
            "remediation": self.remediation,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        prop = data["property"]
        comparison = data.get("comparison")
        values = as_tuple(data.get("values"))
        conditions = as_tuple(data.get("conditions"))
        return cls(
            id=data["id"],
            type=ConstraintType(data["type"]),
            description=data["description"],
            property=prop,
            severity=Severity(data["severity"]),
            provenance=read_provenance(data),
            comparison=Comparison(comparison) if comparison is not None else None,
            limit=_decimal(data.get("limit")),
            minimum=_decimal(data.get("minimum")),
            maximum=_decimal(data.get("maximum")),
            values=tuple(_value(v, prop) for v in values) if prop in NODE_PROPERTIES else values,
            conditions=tuple(
                within(f"conditions[{i}]", partial(Condition.from_dict, c)) for i, c in enumerate(conditions)
            ),
            technology_versions=as_tuple(data.get("technology_versions")),
            remediation=data.get("remediation"),
        )
