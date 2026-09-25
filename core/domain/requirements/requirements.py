"""Requirement business rules: the taxonomy (types, categories, metrics) and the status lifecycle.
Deterministic and authoritative; an LLM may later help *interpret* text, never validate it.

Categories are an open vocabulary: the database only checks their format. For the types the
engines compute on (capacity, performance, availability, reliability, data, cost) the category
must be one of the known ones below; for the other types the known categories are suggestions and
any well-formed category is accepted.

Status lifecycle:

    draft ──> active ──> satisfied
      │         │ │          │
      │         │ └─> invalid ──> draft
      │         │          │
      └─────────┴──────────┴──> deprecated (final)
                 satisfied ──> active (reopened)

- active and satisfied requirements are *in force*: changing them needs a change reason, and
  measurable ones must carry a structured constraint;
- satisfied requirements must be reopened (set to active) before their content changes, and
  deprecated ones never change: that is what an architecture was checked against;
- AI-sourced requirements start as drafts: a person promotes them.
"""

from dataclasses import dataclass
from decimal import Decimal

from .enums import RequirementSource, RequirementStatus, RequirementType
from .errors import InvalidStatusTransition
from .value_objects import (
    Dimension,
    Operator,
    QuantityConstraint,
    RangeConstraint,
    SetConstraint,
    StructuredConstraint,
    invalid,
)

T = RequirementType
S = RequirementStatus

# --- categories ----------------------------------------------------------------------------------

KNOWN_CATEGORIES: dict[RequirementType, frozenset[str]] = {
    T.FUNCTIONAL: frozenset(
        {"user", "order", "payment", "notification", "authentication", "search", "reporting"}
    ),
    T.NON_FUNCTIONAL: frozenset({"usability", "maintainability", "portability", "accessibility"}),
    T.CAPACITY: frozenset(
        {
            "throughput",
            "requests_per_second",
            "orders_per_second",
            "concurrent_users",
            "daily_active_users",
            "monthly_active_users",
            "storage",
        }
    ),
    T.PERFORMANCE: frozenset({"latency", "throughput"}),
    T.AVAILABILITY: frozenset({"availability", "uptime"}),
    T.RELIABILITY: frozenset({"availability", "durability", "rpo", "rto"}),
    T.SECURITY: frozenset({"encryption", "authentication", "authorization", "pii", "secrets"}),
    T.DATA: frozenset({"retention", "consistency", "durability", "storage", "data_volume"}),
    T.COMPLIANCE: frozenset({"retention", "data_residency", "gdpr", "hipaa", "pci_dss", "soc2"}),
    T.OPERATIONAL: frozenset({"regions", "deployment", "monitoring", "backup"}),
    T.COST: frozenset({"budget", "monthly_budget", "infrastructure_budget"}),
}
# Types whose category must be a known one (the engines branch on them).
CLOSED_CATEGORY_TYPES = frozenset({T.CAPACITY, T.PERFORMANCE, T.AVAILABILITY, T.RELIABILITY, T.DATA, T.COST})
# Types that are only usable by the engines with a number attached: in force => constraint required.
MEASURABLE_TYPES = frozenset({T.CAPACITY, T.PERFORMANCE, T.AVAILABILITY, T.RELIABILITY, T.COST})

# --- metrics -------------------------------------------------------------------------------------

# Every quantity accepts a target (==). Ranges only make sense where both directions do.
_BOTH = frozenset(
    {
        Operator.AT_LEAST,
        Operator.MORE_THAN,
        Operator.AT_MOST,
        Operator.LESS_THAN,
        Operator.EQUALS,
        Operator.BETWEEN,
    }
)
_FLOOR = frozenset({Operator.AT_LEAST, Operator.MORE_THAN, Operator.EQUALS})  # availability, durability
_CEILING = frozenset({Operator.AT_MOST, Operator.LESS_THAN, Operator.EQUALS})  # latency, RPO, budget


@dataclass(frozen=True, slots=True)
class MetricRule:
    types: frozenset[RequirementType]
    categories: frozenset[str]
    # None: a set constraint (operator "in"); otherwise the dimension the unit must have.
    dimension: Dimension | None
    operators: frozenset[Operator] = frozenset({Operator.ONE_OF})
    # Bounds on the value in the canonical unit (inclusive unless noted).
    minimum: Decimal = Decimal(0)
    minimum_exclusive: bool = True
    maximum: Decimal | None = None
    integral: bool = False
    percentile: bool = False


METRICS: dict[str, MetricRule] = {
    "requests_per_second": MetricRule(
        frozenset({T.CAPACITY, T.PERFORMANCE}),
        frozenset({"throughput", "requests_per_second"}),
        Dimension.RATE,
        _BOTH,
    ),
    "concurrent_users": MetricRule(
        frozenset({T.CAPACITY}), frozenset({"concurrent_users"}), Dimension.COUNT, _BOTH, integral=True
    ),
    "daily_active_users": MetricRule(
        frozenset({T.CAPACITY}), frozenset({"daily_active_users"}), Dimension.COUNT, _BOTH, integral=True
    ),
    "monthly_active_users": MetricRule(
        frozenset({T.CAPACITY}), frozenset({"monthly_active_users"}), Dimension.COUNT, _BOTH, integral=True
    ),
    # A business rate, kept apart from requests: 500 orders/second is not 500 requests/second.
    "orders_per_second": MetricRule(
        frozenset({T.CAPACITY, T.PERFORMANCE}),
        frozenset({"throughput", "orders_per_second"}),
        Dimension.ORDER_RATE,
        _BOTH,
    ),
    "storage": MetricRule(
        frozenset({T.CAPACITY, T.DATA}), frozenset({"storage", "data_volume"}), Dimension.DATA_SIZE, _BOTH
    ),
    "latency": MetricRule(
        frozenset({T.PERFORMANCE}), frozenset({"latency"}), Dimension.DURATION, _CEILING, percentile=True
    ),
    "availability": MetricRule(
        frozenset({T.AVAILABILITY, T.RELIABILITY}),
        frozenset({"availability", "uptime"}),
        Dimension.RATIO,
        _FLOOR,
        maximum=Decimal(1),
    ),
    "durability": MetricRule(
        frozenset({T.DATA, T.RELIABILITY}),
        frozenset({"durability"}),
        Dimension.RATIO,
        _FLOOR,
        maximum=Decimal(1),
    ),
    # Zero is meaningful for recovery objectives: no data loss, instant failover.
    "rpo": MetricRule(
        frozenset({T.RELIABILITY}), frozenset({"rpo"}), Dimension.DURATION, _CEILING, minimum_exclusive=False
    ),
    "rto": MetricRule(
        frozenset({T.RELIABILITY}), frozenset({"rto"}), Dimension.DURATION, _CEILING, minimum_exclusive=False
    ),
    # Both directions: keep at least 30 days (backups), keep at most 90 days (GDPR).
    "retention": MetricRule(
        frozenset({T.DATA, T.COMPLIANCE}), frozenset({"retention"}), Dimension.DURATION, _BOTH
    ),
    "monthly_budget": MetricRule(
        frozenset({T.COST}),
        frozenset({"budget", "monthly_budget", "infrastructure_budget"}),
        Dimension.MONEY_PER_MONTH,
        _CEILING,
        minimum_exclusive=False,
    ),
    "regions": MetricRule(
        frozenset({T.OPERATIONAL, T.COMPLIANCE}), frozenset({"regions", "data_residency"}), None
    ),
}

# --- status lifecycle ----------------------------------------------------------------------------

TRANSITIONS: dict[RequirementStatus, frozenset[RequirementStatus]] = {
    S.DRAFT: frozenset({S.ACTIVE, S.DEPRECATED}),
    S.ACTIVE: frozenset({S.SATISFIED, S.INVALID, S.DEPRECATED}),
    S.SATISFIED: frozenset({S.ACTIVE, S.DEPRECATED}),
    S.INVALID: frozenset({S.DRAFT, S.DEPRECATED}),
    S.DEPRECATED: frozenset(),
}
IN_FORCE = frozenset({S.ACTIVE, S.SATISFIED})
# Only a person's own structured requirement can start active; anything interpreted, imported or
# inferred is a draft until a person promotes it.
INITIAL_STATUSES: dict[RequirementSource, frozenset[RequirementStatus]] = {
    source: frozenset({S.DRAFT, S.ACTIVE}) if source is RequirementSource.USER else frozenset({S.DRAFT})
    for source in RequirementSource
}
# Machine interpretations must say how sure they are; a person's own requirement has no confidence.
CONFIDENCE_REQUIRED = frozenset({RequirementSource.AI, RequirementSource.DISCOVERY})


def check_transition(current: RequirementStatus, target: RequirementStatus) -> None:
    if target is not current and target not in TRANSITIONS[current]:
        raise InvalidStatusTransition(details={"from": current.value, "to": target.value})


def content_locked(current: RequirementStatus, target: RequirementStatus) -> bool:
    """Whether the content (anything but status) may not change in this revision."""
    if current is S.DEPRECATED:
        return True
    return current is S.SATISFIED and target is not S.ACTIVE


# --- validation ----------------------------------------------------------------------------------


def validate_category(type_: RequirementType, category: str) -> None:
    if type_ in CLOSED_CATEGORY_TYPES and category not in KNOWN_CATEGORIES[type_]:
        raise invalid("category", "unknown_for_type")


def validate_constraint(type_: RequirementType, category: str, constraint: StructuredConstraint) -> None:
    rule = METRICS.get(constraint.metric)
    if rule is None:
        raise invalid("structured_data.metric", "unknown_metric")
    if type_ not in rule.types:
        raise invalid("structured_data.metric", "not_allowed_for_type")
    if category not in rule.categories:
        raise invalid("structured_data.metric", "not_allowed_for_category")
    if constraint.operator not in rule.operators:
        raise invalid("structured_data.operator", "not_allowed_for_metric")
    if isinstance(constraint, SetConstraint):
        return
    if constraint.unit.dimension is not rule.dimension:
        raise invalid("structured_data.unit", "not_allowed_for_metric")
    if constraint.percentile is not None and not rule.percentile:
        raise invalid("structured_data.percentile", "not_allowed_for_metric")
    if isinstance(constraint, RangeConstraint):
        _check_value(rule, constraint.unit.to_canonical(constraint.minimum), "structured_data.min")
        _check_value(rule, constraint.unit.to_canonical(constraint.maximum), "structured_data.max")
        return
    _validate_quantity(rule, constraint)


def _check_value(rule: MetricRule, value: Decimal, field: str) -> None:
    too_low = value <= rule.minimum if rule.minimum_exclusive else value < rule.minimum
    too_high = rule.maximum is not None and value > rule.maximum
    if too_low or too_high:
        raise invalid(field, "out_of_range")
    if rule.integral and value != value.to_integral_value():
        raise invalid(field, "not_integral")


def _validate_quantity(rule: MetricRule, constraint: QuantityConstraint) -> None:
    value = constraint.canonical_value
    _check_value(rule, value, "structured_data.value")
    # A bound nothing can satisfy: "< 0 s" for an RPO, "> 100 %" for availability.
    if constraint.operator is Operator.LESS_THAN and not rule.minimum_exclusive and value == rule.minimum:
        raise invalid("structured_data.value", "unsatisfiable")
    if constraint.operator is Operator.MORE_THAN and rule.maximum is not None and value == rule.maximum:
        raise invalid("structured_data.value", "unsatisfiable")


def validate_content(
    type_: RequirementType,
    category: str,
    status: RequirementStatus,
    constraint: StructuredConstraint | None,
) -> None:
    """Everything present must be valid in any status; in force, measurable requirements must also
    carry a constraint (a draft may still be incomplete)."""
    validate_category(type_, category)
    if constraint is not None:
        validate_constraint(type_, category, constraint)
    elif status in IN_FORCE and type_ in MEASURABLE_TYPES:
        raise invalid("structured_data", "required_when_in_force")
