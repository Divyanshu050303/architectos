"""Deterministic requirement analysis: per-requirement validation reports and project-level
conflicts, completeness, ambiguity and unbounded metrics. Pure functions over domain objects; an
LLM may later suggest fixes, but these results are authoritative.

Scope: requirements that are draft, active or satisfied (invalid and deprecated ones are out of
play). Every finding names the exact versions it was computed from.

- A *conflict* is a mathematical impossibility: no value satisfies both requirements, e.g.
  requests_per_second >= 10000 and <= 5000, or regions in {eu-west-1} and in {us-east-1}.
  Tensions (high availability on a small budget) are not conflicts and are not reported.
- *Completeness* lists the common architecture concerns covered and missing. Missing concerns
  are warnings: a project need not specify everything up front.
- *Ambiguous*: a requirement an engine cannot use as is (measurable but without a constraint,
  latency without a percentile, AI interpretation with low confidence).
- *Unbounded*: a sizing metric (traffic, users, storage) that only has upper bounds, so there is
  no target to design for.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import combinations

from .entities import Requirement, RequirementContent
from .enums import RequirementScope, RequirementSource, RequirementStatus, RequirementType
from .errors import InvalidRequirement
from .normalization import CanonicalQuantity, canonical
from .requirements import MEASURABLE_TYPES, validate_content
from .value_objects import QuantityConstraint, RangeConstraint, SetConstraint

ANALYZED_STATUSES = frozenset(
    {RequirementStatus.DRAFT, RequirementStatus.ACTIVE, RequirementStatus.SATISFIED}
)
LOW_CONFIDENCE = Decimal("0.7")
SIZING_METRICS = frozenset({"requests_per_second", "concurrent_users", "daily_active_users", "storage"})


class Severity(StrEnum):
    """How much a finding matters for architecture work."""

    BLOCKING = "blocking"  # architecture cannot proceed: an invalid requirement, a contradiction
    WARNING = "warning"  # should be resolved: an ambiguity, a missing common concern
    INFO = "info"  # worth knowing: one requirement strengthens another


@dataclass(frozen=True, slots=True)
class Issue:
    severity: Severity
    field: str
    reason: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    requirement: Requirement
    issues: tuple[Issue, ...]
    ready_for_active: bool

    @property
    def valid(self) -> bool:
        return not any(issue.severity is Severity.BLOCKING for issue in self.issues)


def _error_of(requirement: Requirement, status: RequirementStatus) -> InvalidRequirement | None:
    content = requirement.content
    try:
        validate_content(content.type, content.category, status, content.constraint)
    except InvalidRequirement as error:
        return error
    return None


def _ambiguities(requirement: Requirement) -> list[Issue]:
    content, issues = requirement.content, []
    if content.constraint is None and content.type in MEASURABLE_TYPES:
        issues.append(Issue(Severity.WARNING, "structured_data", "missing_constraint"))
    constraint = content.constraint
    if (
        isinstance(constraint, (QuantityConstraint, RangeConstraint))
        and constraint.metric == "latency"
        and constraint.percentile is None
    ):
        issues.append(Issue(Severity.WARNING, "structured_data.percentile", "missing_percentile"))
    if requirement.source is RequirementSource.AI and (requirement.confidence or 0) < LOW_CONFIDENCE:
        issues.append(Issue(Severity.WARNING, "confidence", "low_confidence"))
    return issues


def validate_requirement(requirement: Requirement) -> ValidationReport:
    """Checks the current state against today's rules (stored history is never re-validated on
    load, so this is how a requirement written under older rules is found) and whether it could
    become active."""
    issues: list[Issue] = []
    current = _error_of(requirement, requirement.content.status)
    if current is not None:
        issues.append(Issue(Severity.BLOCKING, current.details["field"], current.details["reason"]))
    as_active = _error_of(requirement, RequirementStatus.ACTIVE)
    issues += _ambiguities(requirement)
    if as_active is not None and current is None:
        issues.append(Issue(Severity.WARNING, as_active.details["field"], "not_ready_for_active"))
    unique = tuple(dict.fromkeys(issues))
    return ValidationReport(requirement=requirement, issues=unique, ready_for_active=as_active is None)


# --- project analysis ----------------------------------------------------------------------------


class Concern(StrEnum):
    TRAFFIC = "traffic"
    LATENCY = "latency"
    AVAILABILITY = "availability"
    DATA = "data"
    SECURITY = "security"
    RETENTION = "retention"


def _concerns_of(requirement: Requirement) -> set[Concern]:
    content = requirement.content
    metric = content.constraint.metric if content.constraint else None
    found: set[Concern] = set()
    if metric in {"requests_per_second", "concurrent_users", "daily_active_users"}:
        found.add(Concern.TRAFFIC)
    if metric == "latency":
        found.add(Concern.LATENCY)
    if metric == "availability":
        found.add(Concern.AVAILABILITY)
    if content.type is RequirementType.DATA or metric == "storage":
        found.add(Concern.DATA)
    if content.type is RequirementType.SECURITY:
        found.add(Concern.SECURITY)
    if metric == "retention":
        found.add(Concern.RETENTION)
    return found


@dataclass(frozen=True, slots=True)
class Conflict:
    """A contradiction: always blocking. Tensions and stricter duplicates are not conflicts."""

    reason: str  # disjoint_bounds | disjoint_sets
    metric: str
    requirements: tuple[Requirement, Requirement]
    message: str
    severity: Severity = Severity.BLOCKING

    @property
    def key(self) -> str:
        """Stable within a project: the same two requirements give the same key."""
        first, second = self.requirements
        return f"{self.reason}:{first.reference}:{second.reference}"


@dataclass(frozen=True, slots=True)
class Ambiguity:
    requirement: Requirement
    reason: str


@dataclass(frozen=True, slots=True)
class Unbounded:
    metric: str
    requirements: tuple[Requirement, ...]
    reason: str = "no_lower_bound"


@dataclass(frozen=True, slots=True)
class Coverage:
    concern: Concern
    requirements: tuple[Requirement, ...]


@dataclass(frozen=True, slots=True)
class ProjectAnalysis:
    requirements: tuple[Requirement, ...]
    conflicts: tuple[Conflict, ...]
    covered: tuple[Coverage, ...]
    missing: tuple[Concern, ...]
    ambiguous: tuple[Ambiguity, ...]
    unbounded: tuple[Unbounded, ...]
    truncated: bool = False


# (metric, scope, percentile, canonical unit): only requirements in one group are compared.
class Relation(StrEnum):
    """How two comparable requirements relate."""

    DISJOINT = "disjoint"  # no value satisfies both: a conflict
    EQUAL = "equal"  # they allow exactly the same values
    FIRST_STRICTER = "first_stricter"  # everything the first allows, the second allows too
    SECOND_STRICTER = "second_stricter"
    OVERLAP = "overlap"  # compatible, neither contains the other (">= 100" and "<= 500")


@dataclass(frozen=True, slots=True)
class Comparison[T]:
    first: T
    second: T
    metric: str
    relation: Relation
    first_bound: str  # human-readable, e.g. "<= 300 ms", "in {eu-west-1}"
    second_bound: str
    sets: bool = False  # a set membership (regions) rather than a quantity

    @property
    def reason(self) -> str:
        """The conflict code, when the relation is DISJOINT."""
        return "disjoint_sets" if self.sets else "disjoint_bounds"


def _relate(first_in_second: bool, second_in_first: bool, intersect: bool) -> Relation:
    if not intersect:
        return Relation.DISJOINT
    if first_in_second and second_in_first:
        return Relation.EQUAL
    if first_in_second:
        return Relation.FIRST_STRICTER
    if second_in_first:
        return Relation.SECOND_STRICTER
    return Relation.OVERLAP


# (metric, scope, percentile, canonical unit): only requirements in one group are compared.
type _Group = tuple[str, RequirementScope, Decimal | None, str]


def compare_like_with_like[T](items: list[tuple[T, RequirementContent]]) -> list[Comparison[T]]:
    """Every pair of comparable requirements with how they relate. Only like is compared with like:
    the same metric, scope, percentile and canonical unit (latency p95 is not p99, an API is not a
    database, USD is not EUR), after exact conversion (600,000 requests/minute is 10,000/second)."""
    quantities: dict[_Group, list[tuple[T, CanonicalQuantity]]] = defaultdict(list)
    sets: dict[tuple[str, RequirementScope], list[tuple[T, SetConstraint]]] = defaultdict(list)
    for item, content in items:
        constraint = content.constraint
        if isinstance(constraint, QuantityConstraint | RangeConstraint):
            quantity = canonical(constraint)
            group = (quantity.metric, content.scope, quantity.percentile, quantity.unit)
            quantities[group].append((item, quantity))
        elif isinstance(constraint, SetConstraint):
            sets[constraint.metric, content.scope].append((item, constraint))

    comparisons: list[Comparison[T]] = []
    for (metric, _, _, _), members in quantities.items():
        for (first, a), (second, b) in combinations(members, 2):
            relation = _relate(
                b.interval.contains(a.interval),
                a.interval.contains(b.interval),
                a.interval.intersects(b.interval),
            )
            comparisons.append(Comparison(first, second, metric, relation, a.describe(), b.describe()))
    for (metric, _), choices in sets.items():
        for (first, x), (second, y) in combinations(choices, 2):
            left, right = set(x.values), set(y.values)
            relation = _relate(left <= right, right <= left, bool(left & right))
            described = (f"in {{{', '.join(x.values)}}}", f"in {{{', '.join(y.values)}}}")
            comparisons.append(Comparison(first, second, metric, relation, *described, sets=True))
    return comparisons


def find_conflicts(requirements: list[Requirement]) -> list[Conflict]:
    """Pairs of requirements no value can satisfy together (see ``compare_like_with_like``)."""
    conflicts: list[Conflict] = []
    for comparison in compare_like_with_like([(r, r.content) for r in requirements]):
        if comparison.relation is not Relation.DISJOINT:
            continue
        first, second, metric = comparison.first, comparison.second, comparison.metric
        if comparison.reason == "disjoint_sets":
            message = f"{first.reference} and {second.reference} allow no {metric} in common."
        else:
            message = (
                f"{first.reference} requires {metric} {comparison.first_bound}, but {second.reference} "
                f"requires {metric} {comparison.second_bound}: no value satisfies both."
            )
        conflicts.append(Conflict(comparison.reason, metric, (first, second), message))
    return conflicts


def find_unbounded(requirements: list[Requirement]) -> list[Unbounded]:
    by_metric: dict[str, list[Requirement]] = defaultdict(list)
    for requirement in requirements:
        constraint = requirement.content.constraint
        if (
            isinstance(constraint, (QuantityConstraint, RangeConstraint))
            and constraint.metric in SIZING_METRICS
        ):
            by_metric[constraint.metric].append(requirement)
    return [
        Unbounded(metric, tuple(members))
        for metric, members in sorted(by_metric.items())
        if not any(
            isinstance(r.content.constraint, (QuantityConstraint, RangeConstraint))
            and r.content.constraint.operator.is_lower_bound
            for r in members
        )
    ]


def analyze(requirements: list[Requirement], *, truncated: bool = False) -> ProjectAnalysis:
    """Deterministic for a given set of requirement versions: ordered by requirement number."""
    in_play = sorted(
        (r for r in requirements if r.content.status in ANALYZED_STATUSES), key=lambda r: r.number
    )
    coverage: dict[Concern, list[Requirement]] = {concern: [] for concern in Concern}
    for requirement in in_play:
        for concern in _concerns_of(requirement):
            coverage[concern].append(requirement)
    ambiguous = [
        Ambiguity(requirement, issue.reason) for requirement in in_play for issue in _ambiguities(requirement)
    ]
    return ProjectAnalysis(
        requirements=tuple(in_play),
        conflicts=tuple(find_conflicts(in_play)),
        covered=tuple(Coverage(c, tuple(rs)) for c, rs in coverage.items() if rs),
        missing=tuple(c for c, rs in coverage.items() if not rs),
        ambiguous=tuple(ambiguous),
        unbounded=tuple(find_unbounded(in_play)),
        truncated=truncated,
    )
