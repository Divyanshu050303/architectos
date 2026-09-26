"""Deterministic classification: what a quantity in a sentence *is* (type, category, metric, scope)
and what a sentence without quantities asks for (encryption, PII, GDPR, a feature, a region).

Pure and rule-based: a model is only needed where meaning cannot be read from units and keywords,
and that is the LLM extraction port's job (later), never this module's. Rules, in order:

1. The **unit** decides when it can: requests/second → throughput, orders/second → orders, users
   with a qualifier → daily/monthly/concurrent users, money per month → budget.
2. Otherwise the **nearest keyword** in the quantity's own context decides (durations: latency,
   RPO, RTO or retention; percentages: availability or durability; sizes: storage or data volume).
3. Otherwise the quantity is **unresolved**, with the reason and the readings it could have: a
   classification is never guessed ("10M users" could be daily, monthly, registered or concurrent).

A context is the text between the previous quantity and the next one in the same sentence, so in
"p95 latency below 300ms and 99.9% availability" the 300 ms reads "latency" and the 99.9 % reads
"availability". Scope is only set when the context names one (API, database, queue, service,
region); otherwise it stays ``system``. When the text states no operator, the metric's natural one
is used (at least N requests, at most N ms) and the classification says so.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from core.domain.requirements.enums import RequirementPriority, RequirementScope, RequirementType
from core.domain.requirements.value_objects import Dimension, Operator

from .normalizer import Bound

T = RequirementType
_Rule = tuple[RequirementType, str, str, str]  # type, category, metric, title


@dataclass(frozen=True, slots=True)
class Classification:
    type: RequirementType
    category: str
    metric: str
    title: str
    operator: Operator  # stated, or the metric's natural one
    operator_implied: bool
    confidence: Decimal
    scope: RequirementScope = RequirementScope.SYSTEM


@dataclass(frozen=True, slots=True)
class Unclassified:
    reason: str  # user_count_kind_unspecified, duration_purpose_unspecified, unitless_number, …
    options: tuple[str, ...] = ()  # the readings it could have


@dataclass(frozen=True, slots=True)
class QualitativeMatch:
    type: RequirementType
    category: str
    title: str
    start: int
    end: int
    confidence: Decimal
    values: tuple[str, ...] = ()  # set constraints (regions)


# --- rules -----------------------------------------------------------------------------------------

UNIT_DECIDES = Decimal("0.95")  # the unit alone decides
KEYWORD_DECIDES = Decimal("0.9")  # a keyword next to the quantity decides
IMPLIED_OPERATOR_PENALTY = Decimal("0.05")  # less sure when the operator is the metric's default
QUALITATIVE_CONFIDENCE = Decimal("0.8")  # a keyword in a sentence, no quantity

# The metric's natural reading when the text states no operator.
NATURAL_OPERATOR = {
    "requests_per_second": Operator.AT_LEAST,
    "orders_per_second": Operator.AT_LEAST,
    "daily_active_users": Operator.AT_LEAST,
    "monthly_active_users": Operator.AT_LEAST,
    "concurrent_users": Operator.AT_LEAST,
    "storage": Operator.AT_LEAST,
    "availability": Operator.AT_LEAST,
    "durability": Operator.AT_LEAST,
    "retention": Operator.AT_LEAST,  # "retain for 7 years": at least that long
    "latency": Operator.AT_MOST,
    "rpo": Operator.AT_MOST,
    "rto": Operator.AT_MOST,
    "monthly_budget": Operator.AT_MOST,
}
_USER_METRICS = {
    "daily active": ("daily_active_users", "Daily active users"),
    "daily": ("daily_active_users", "Daily active users"),
    "monthly active": ("monthly_active_users", "Monthly active users"),
    "monthly": ("monthly_active_users", "Monthly active users"),
    "concurrent": ("concurrent_users", "Concurrent users"),
    "simultaneous": ("concurrent_users", "Concurrent users"),
}
USER_COUNT_READINGS = ("daily_active_users", "monthly_active_users", "registered_users", "concurrent_users")


def _words(*words: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)


_DURATION_KEYWORDS: list[tuple[re.Pattern[str], _Rule]] = [
    (
        _words(
            "latency",
            r"response\s+times?",
            r"respond(?:s|ing)?",
            r"p\d{1,2}(?:\.\d+)?",
            "percentile",
            "median",
        ),
        (T.PERFORMANCE, "latency", "latency", "Latency"),
    ),
    (
        _words("rpo", r"recovery\s+point", r"data\s+loss"),
        (T.RELIABILITY, "rpo", "rpo", "Recovery point objective"),
    ),
    (
        _words("rto", r"recovery\s+time", r"recover(?:ed|y)?", r"restor(?:e|ed)", "failover", "downtime"),
        (T.RELIABILITY, "rto", "rto", "Recovery time objective"),
    ),
    (
        _words(r"retain(?:ed)?", "retention", "keep", "kept", r"stored?", r"archiv(?:e|ed)", r"backups?"),
        (T.DATA, "retention", "retention", "Data retention"),
    ),
]
_PERCENT_KEYWORDS: list[tuple[re.Pattern[str], _Rule]] = [
    (_words("uptime"), (T.AVAILABILITY, "uptime", "availability", "Uptime")),
    (
        _words("availability", "available", "sla", "nines"),
        (T.AVAILABILITY, "availability", "availability", "Availability"),
    ),
    (_words("durability", "durable"), (T.DATA, "durability", "durability", "Durability")),
]
_SIZE_KEYWORDS: list[tuple[re.Pattern[str], _Rule]] = [
    (
        _words(r"data\s+volume", r"ingest(?:ed|ion)?", r"per\s+day", "daily"),
        (T.DATA, "data_volume", "storage", "Data volume"),
    ),
    (
        _words("storage", "store", "stored", "disk", "database", "size"),
        (T.CAPACITY, "storage", "storage", "Storage"),
    ),
]
_SCOPES: list[tuple[re.Pattern[str], RequirementScope]] = [
    (_words("api", "apis", r"endpoints?", "rest", "graphql"), RequirementScope.API),
    (_words("database", "databases", "db", r"postgres(?:ql)?", "mysql"), RequirementScope.DATABASE),
    (_words(r"queues?", "kafka", "rabbitmq", "sqs"), RequirementScope.QUEUE),
    (_words(r"(?:micro)?services?"), RequirementScope.SERVICE),
    (_words(r"regions?"), RequirementScope.REGION),
]
_INFRASTRUCTURE = _words("infrastructure", "hosting", "cloud")

# Sentences without quantities.
_QUALITATIVE: list[tuple[re.Pattern[str], tuple[RequirementType, str, str]]] = [
    (_words(r"encrypt(?:ed|ion|s)?", "tls", "https"), (T.SECURITY, "encryption", "Encryption")),
    (
        _words("pii", r"personal\s+data", r"personally\s+identifiable"),
        (T.SECURITY, "pii", "Personal data (PII)"),
    ),
    (
        _words("mfa", "2fa", "sso", r"single\s+sign[\s-]on", r"two[\s-]factor"),
        (T.SECURITY, "authentication", "Authentication"),
    ),
    (
        _words("rbac", r"role[\s-]based", "authorization", "permissions"),
        (T.SECURITY, "authorization", "Authorization"),
    ),
    (_words(r"secrets?", r"api\s+keys?", "credentials"), (T.SECURITY, "secrets", "Secrets management")),
    (_words("gdpr"), (T.COMPLIANCE, "gdpr", "GDPR")),
    (_words("hipaa"), (T.COMPLIANCE, "hipaa", "HIPAA")),
    (_words(r"pci(?:[\s-]dss)?"), (T.COMPLIANCE, "pci_dss", "PCI DSS")),
    (_words(r"soc\s*2"), (T.COMPLIANCE, "soc2", "SOC 2")),
    (_words(r"data\s+residency"), (T.COMPLIANCE, "data_residency", "Data residency")),
    (
        _words(
            r"strong(?:ly)?\s+consisten(?:t|cy)",
            r"eventual(?:ly)?\s+consisten(?:t|cy)",
            "acid",
            "transactional",
        ),
        (T.DATA, "consistency", "Consistency"),
    ),
    (_words(r"payments?", "checkout", "billing"), (T.FUNCTIONAL, "payment", "Payments")),
    (
        _words(r"notifications?", r"push\s+messages?", r"alerts?"),
        (T.FUNCTIONAL, "notification", "Notifications"),
    ),
    (_words(r"orders?", "ordering"), (T.FUNCTIONAL, "order", "Orders")),
    (
        _words(r"log\s*in", r"sign\s*(?:in|up)", "login", "registration"),
        (T.FUNCTIONAL, "authentication", "Sign-in"),
    ),
    (_words("search"), (T.FUNCTIONAL, "search", "Search")),
    (_words(r"reports?", "reporting", r"dashboards?", "analytics"), (T.FUNCTIONAL, "reporting", "Reporting")),
    (_words(r"real[\s-]time\s+tracking", "tracking"), (T.FUNCTIONAL, "tracking", "Tracking")),
    (
        _words(r"postgres(?:ql)?", "mysql", "mongodb", "redis", "kafka", "kubernetes", "aws", "gcp", "azure"),
        (T.OPERATIONAL, "technology", "Technology"),
    ),
]
_REGION_CODE = re.compile(r"\b(?:[a-z]{2}-[a-z]+-\d|[a-z]+-[a-z]+\d)\b")
_PRIORITY: list[tuple[re.Pattern[str], RequirementPriority]] = [
    (_words("critical", r"mission[\s-]critical", r"non[\s-]negotiable"), RequirementPriority.CRITICAL),
    (_words("must", "required", "important"), RequirementPriority.HIGH),
    (_words(r"nice\s+to\s+have", "optional", "ideally", r"if\s+possible"), RequirementPriority.LOW),
]


# --- classification --------------------------------------------------------------------------------


def _nearest[R](rules: list[tuple[re.Pattern[str], R]], context: str, anchor: int) -> R | None:
    """The result of the rule whose keyword is closest to ``anchor`` (a position in ``context``)."""
    best: tuple[int, R] | None = None
    for pattern, result in rules:
        for match in pattern.finditer(context):
            distance = min(abs(match.start() - anchor), abs(match.end() - anchor))
            if best is None or distance < best[0]:
                best = (distance, result)
    return best[1] if best else None


def scope_in(context: str, anchor: int) -> RequirementScope:
    return _nearest(_SCOPES, context, anchor) or RequirementScope.SYSTEM


def priority_of(sentence: str) -> RequirementPriority:
    """Only explicit words; otherwise medium. Priority is a person's decision: this is a proposal."""
    for pattern, priority in _PRIORITY:
        if pattern.search(sentence):
            return priority
    return RequirementPriority.MEDIUM


def _by_unit(bound: Bound, context: str) -> _Rule | Unclassified | None:
    """Classification decided by the unit alone, an explicit refusal, or None (keywords decide)."""
    unit = bound.unit
    assert unit is not None  # noqa: S101 - checked by the caller
    match unit.dimension:
        case Dimension.RATE:
            return (T.CAPACITY, "throughput", "requests_per_second", "Throughput")
        case Dimension.ORDER_RATE:
            return (T.CAPACITY, "orders_per_second", "orders_per_second", "Order throughput")
        case Dimension.MONEY_PER_MONTH:
            category = "infrastructure_budget" if _INFRASTRUCTURE.search(context) else "budget"
            return (T.COST, category, "monthly_budget", "Monthly budget")
        case Dimension.COUNT:
            reading = _USER_METRICS.get(bound.qualifier or "")
            if reading is None:
                return Unclassified("user_count_kind_unspecified", USER_COUNT_READINGS)
            metric, title = reading
            return (T.CAPACITY, metric, metric, title)
        case _:
            return None


_BY_KEYWORD: dict[Dimension, tuple[list[tuple[re.Pattern[str], _Rule]], Unclassified | _Rule]] = {
    Dimension.DURATION: (
        _DURATION_KEYWORDS,
        Unclassified("duration_purpose_unspecified", ("latency", "rpo", "rto", "retention")),
    ),
    Dimension.RATIO: (
        _PERCENT_KEYWORDS,
        Unclassified("percentage_purpose_unspecified", ("availability", "durability")),
    ),
    Dimension.DATA_SIZE: (_SIZE_KEYWORDS, (T.CAPACITY, "storage", "storage", "Storage")),
}


def classify(bound: Bound, context: str, anchor: int) -> Classification | Unclassified:
    """``context`` is the text around the bound; ``anchor`` is the bound's position in it."""
    if bound.unit is None:
        return Unclassified("unitless_number")
    confidence = UNIT_DECIDES
    found = _by_unit(bound, context)
    if found is None:
        rules, fallback = _BY_KEYWORD[bound.unit.dimension]
        found, confidence = _nearest(rules, context, anchor) or fallback, KEYWORD_DECIDES
    if isinstance(found, Unclassified):
        return found
    type_, category, metric, title = found
    implied = bound.operator is None
    return Classification(
        type=type_,
        category=category,
        metric=metric,
        title=title,
        operator=bound.operator or NATURAL_OPERATOR[metric],
        operator_implied=implied,
        confidence=confidence - (IMPLIED_OPERATOR_PENALTY if implied else 0),
        scope=scope_in(context, anchor),
    )


def qualitative(sentence: str, offset: int) -> list[QualitativeMatch]:
    """Non-numeric requirements in a sentence: one per category, at its first mention; ``offset``
    is the sentence's position in the raw input."""
    found: dict[tuple[RequirementType, str], QualitativeMatch] = {}
    for pattern, (type_, category, title) in _QUALITATIVE:
        match = pattern.search(sentence)
        if match and (type_, category) not in found:
            found[type_, category] = QualitativeMatch(
                type_, category, title, offset + match.start(), offset + match.end(), QUALITATIVE_CONFIDENCE
            )
    regions = list(dict.fromkeys(m.group(0) for m in _REGION_CODE.finditer(sentence)))
    first = _REGION_CODE.search(sentence)
    if first is not None:
        found[T.OPERATIONAL, "regions"] = QualitativeMatch(
            T.OPERATIONAL,
            "regions",
            "Regions",
            offset + first.start(),
            offset + first.end(),
            UNIT_DECIDES,
            tuple(regions),
        )
    return sorted(found.values(), key=lambda m: m.start)
