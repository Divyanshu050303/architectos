"""Completeness: is there enough information to meaningfully design and validate this system?

Not "is every possible requirement present?". What is essential depends on the system, so the
assessment is contextual:

1. **Context**: profiles are recognised from the input's own words (each with the words that
   triggered it, so the judgement can be explained): payments, health data, an internal tool, a
   public high-traffic platform, data/IoT ingestion, a SaaS product. None recognised: a general
   baseline.
2. **Importance**: each profile rates the ten areas (traffic, capacity, latency, availability,
   reliability, data, security, compliance, operational, cost) as essential, recommended, optional
   or not relevant. Several profiles: the strongest rating wins. A payment system needs security,
   compliance and consistency; an internal admin tool does not need traffic figures.
3. **Coverage**: an area is covered by any new candidate or existing requirement in it.

A missing *essential* area is blocking (architecture work cannot meaningfully start), a missing
recommended one a warning, a missing optional one info. The status is ``unknown`` when there is
nothing to judge, ``incomplete`` when an essential area is missing, ``complete`` otherwise (warnings
may remain).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from core.domain.requirements.analysis import Severity
from core.domain.requirements.entities import RequirementContent
from core.domain.requirements.enums import RequirementType

from .findings import Finding, FindingKind

T = RequirementType


class Area(StrEnum):
    TRAFFIC = "traffic"
    CAPACITY = "capacity"
    LATENCY = "latency"
    AVAILABILITY = "availability"
    RELIABILITY = "reliability"
    DATA = "data"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    OPERATIONAL = "operational"
    COST = "cost"


class Importance(IntEnum):
    NOT_RELEVANT = 0
    OPTIONAL = 1
    RECOMMENDED = 2
    ESSENTIAL = 3


class CompletenessStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


ESSENTIAL, RECOMMENDED, OPTIONAL = Importance.ESSENTIAL, Importance.RECOMMENDED, Importance.OPTIONAL


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    signals: re.Pattern[str]
    ratings: dict[Area, Importance]
    reason: str  # why these areas matter, for messages


def _signals(*words: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)


# Areas a profile does not rate keep the baseline's rating.
BASELINE = Profile(
    "general",
    re.compile(r"(?!)"),  # never matches: it applies when nothing else rates an area
    {
        Area.TRAFFIC: ESSENTIAL,
        Area.CAPACITY: RECOMMENDED,
        Area.LATENCY: RECOMMENDED,
        Area.AVAILABILITY: ESSENTIAL,
        Area.RELIABILITY: RECOMMENDED,
        Area.DATA: OPTIONAL,
        Area.SECURITY: RECOMMENDED,
        Area.COMPLIANCE: OPTIONAL,
        Area.OPERATIONAL: OPTIONAL,
        Area.COST: OPTIONAL,
    },
    "any production system needs it",
)
PROFILES: tuple[Profile, ...] = (
    Profile(
        "payments",
        _signals(
            r"payments?",
            "checkout",
            "billing",
            r"banks?",
            "banking",
            r"wallets?",
            "fintech",
            "pci",
            r"money\s+transfers?",
            "ledger",
        ),
        {
            Area.SECURITY: ESSENTIAL,
            Area.COMPLIANCE: ESSENTIAL,
            Area.AVAILABILITY: ESSENTIAL,
            Area.RELIABILITY: ESSENTIAL,
            Area.DATA: ESSENTIAL,
        },
        "payments must be secure, compliant, consistent and recoverable",
    ),
    Profile(
        "health",
        _signals(r"patients?", "medical", r"health(?:care)?", "clinical", "hipaa", "ehr"),
        {
            Area.SECURITY: ESSENTIAL,
            Area.COMPLIANCE: ESSENTIAL,
            Area.DATA: ESSENTIAL,
            Area.RELIABILITY: RECOMMENDED,
        },
        "health data is regulated and sensitive",
    ),
    Profile(
        "internal_tool",
        _signals(
            "internal",
            r"admin\s+(?:tool|panel|dashboard)",
            r"back[\s-]?office",
            "employees",
            "staff",
            "intranet",
        ),
        {
            Area.TRAFFIC: OPTIONAL,
            Area.CAPACITY: OPTIONAL,
            Area.LATENCY: OPTIONAL,
            Area.AVAILABILITY: RECOMMENDED,
            Area.SECURITY: ESSENTIAL,
            Area.COST: OPTIONAL,
        },
        "an internal tool needs access control more than scale",
    ),
    Profile(
        "public_platform",
        _signals(  # not "platform" alone: a payment platform is not a consumer product
            "marketplace",
            r"e-?commerce",
            r"online\s+store",
            r"shop(?:ping)?",
            "social",
            "delivery",
            r"consumers?",
            r"mobile\s+app",
            "streaming",
        ),
        {
            Area.TRAFFIC: ESSENTIAL,
            Area.LATENCY: ESSENTIAL,
            Area.AVAILABILITY: ESSENTIAL,
            Area.CAPACITY: RECOMMENDED,
            Area.SECURITY: RECOMMENDED,
            Area.COST: RECOMMENDED,
        },
        "a public platform is designed around its load and responsiveness",
    ),
    Profile(
        "data_ingestion",
        _signals(
            "iot",
            "telemetry",
            r"sensors?",
            r"devices?",
            r"events?\s+per",
            r"ingest(?:ion)?",
            r"time[\s-]series",
        ),
        {
            Area.TRAFFIC: ESSENTIAL,
            Area.DATA: ESSENTIAL,
            Area.CAPACITY: ESSENTIAL,
            Area.RELIABILITY: RECOMMENDED,
            Area.LATENCY: OPTIONAL,
        },
        "an ingestion system is sized by its event rate, volume and retention",
    ),
    Profile(
        "saas",
        _signals("saas", r"tenants?", r"multi[\s-]tenant", r"subscriptions?", "b2b"),
        {
            Area.AVAILABILITY: ESSENTIAL,
            Area.SECURITY: ESSENTIAL,
            Area.TRAFFIC: RECOMMENDED,
            Area.LATENCY: RECOMMENDED,
            Area.COST: RECOMMENDED,
        },
        "a SaaS product is judged by its availability and tenant isolation",
    ),
)

_SUGGESTIONS: dict[Area, str] = {
    Area.TRAFFIC: "State the peak load, e.g. at least 2,000 requests/second.",
    Area.CAPACITY: "State the users or storage, e.g. 100,000 daily active users, 2 TB.",
    Area.LATENCY: "State a latency target, e.g. p95 latency under 300 ms.",
    Area.AVAILABILITY: "State an availability target, e.g. 99.9 % availability.",
    Area.RELIABILITY: "State recovery objectives, e.g. RPO under 5 minutes, RTO under 1 hour.",
    Area.DATA: "State retention and consistency, e.g. retain orders for 7 years, strong consistency.",
    Area.SECURITY: "State the security controls, e.g. encrypt data at rest, SSO with MFA, role-based access.",
    Area.COMPLIANCE: "Name the regulations that apply, e.g. GDPR, PCI DSS.",
    Area.OPERATIONAL: "State where and how it runs, e.g. deploy to eu-west-1.",
    Area.COST: "State a budget, e.g. at most 5,000 USD/month.",
}

_METRIC_AREAS = {
    "requests_per_second": Area.TRAFFIC,
    "orders_per_second": Area.TRAFFIC,
    "concurrent_users": Area.CAPACITY,
    "daily_active_users": Area.CAPACITY,
    "monthly_active_users": Area.CAPACITY,
    "storage": Area.CAPACITY,
    "latency": Area.LATENCY,
    "availability": Area.AVAILABILITY,
    "rpo": Area.RELIABILITY,
    "rto": Area.RELIABILITY,
    "durability": Area.RELIABILITY,
    "retention": Area.DATA,
    "monthly_budget": Area.COST,
    "regions": Area.OPERATIONAL,
}
_TYPE_AREAS = {
    T.SECURITY: Area.SECURITY,
    T.COMPLIANCE: Area.COMPLIANCE,
    T.DATA: Area.DATA,
    T.OPERATIONAL: Area.OPERATIONAL,
    T.COST: Area.COST,
    T.AVAILABILITY: Area.AVAILABILITY,
    T.RELIABILITY: Area.RELIABILITY,
}
_SEVERITY = {ESSENTIAL: Severity.BLOCKING, RECOMMENDED: Severity.WARNING, OPTIONAL: Severity.INFO}


NOTHING_TO_ASSESS = Finding(
    FindingKind.COMPLETENESS,
    "nothing_to_assess",
    Severity.BLOCKING,
    "The input describes no system and states no requirement: there is nothing to design from.",
    suggestion="Describe what the system does, for whom, and its expected load, e.g. "
    "“A food delivery platform for 100,000 daily users, 2,000 requests/second at peak.”",
)


@dataclass(frozen=True, slots=True)
class DetectedProfile:
    name: str
    evidence: tuple[str, ...]  # the words that triggered it


@dataclass(frozen=True, slots=True)
class Completeness:
    status: CompletenessStatus
    profiles: tuple[DetectedProfile, ...]
    importance: dict[Area, Importance]
    covered: tuple[Area, ...]
    missing: tuple[Area, ...]  # relevant areas nobody specified, most important first
    findings: tuple[Finding, ...]


def areas_of(content: RequirementContent) -> set[Area]:
    found: set[Area] = set()
    if content.constraint is not None and content.constraint.metric in _METRIC_AREAS:
        found.add(_METRIC_AREAS[content.constraint.metric])
    if content.type in _TYPE_AREAS:
        found.add(_TYPE_AREAS[content.type])
    return found


def detect_profiles(raw_input: str) -> tuple[DetectedProfile, ...]:
    detected = []
    for profile in PROFILES:
        words = tuple(dict.fromkeys(m.group(0).lower() for m in profile.signals.finditer(raw_input)))
        if words:
            detected.append(DetectedProfile(profile.name, words))
    return tuple(detected)


def rate(profiles: tuple[DetectedProfile, ...]) -> tuple[dict[Area, Importance], dict[Area, str]]:
    """The strongest rating per area across the detected profiles (the baseline fills the rest),
    and the reason behind each rating."""
    ratings = dict(BASELINE.ratings)
    reasons = dict.fromkeys(Area, BASELINE.reason)
    names = {p.name for p in profiles}
    active = [p for p in PROFILES if p.name in names]
    for area in Area:
        rated = [(p.ratings[area], p) for p in active if area in p.ratings]
        if rated:
            strongest, profile = max(rated, key=lambda pair: pair[0])
            ratings[area], reasons[area] = strongest, profile.reason
    return ratings, reasons


def assess(raw_input: str, requirements: Iterable[RequirementContent]) -> Completeness:
    """``requirements``: the new candidates' content plus the project's existing requirements."""
    contents = list(requirements)
    profiles = detect_profiles(raw_input)
    importance, reasons = rate(profiles)
    covered: set[Area] = set().union(*(areas_of(c) for c in contents))
    order = list(Area)
    missing = sorted(
        (area for area in Area if importance[area] > Importance.NOT_RELEVANT and area not in covered),
        key=lambda area: (-importance[area], order.index(area)),
    )
    findings = tuple(
        Finding(
            FindingKind.COMPLETENESS,
            f"missing_{area.value}",
            _SEVERITY[importance[area]],
            f"No {area.value} requirement ({importance[area].name.lower()}: {reasons[area]}).",
            suggestion=_SUGGESTIONS[area],
        )
        for area in missing
    )
    if not contents and not profiles:
        # Nothing to judge: one finding asking for a description, not a list of every area.
        status = CompletenessStatus.UNKNOWN
        findings = (NOTHING_TO_ASSESS,)
    elif any(importance[area] is ESSENTIAL for area in missing):
        status = CompletenessStatus.INCOMPLETE
    else:
        status = CompletenessStatus.COMPLETE
    return Completeness(
        status=status,
        profiles=profiles,
        importance=importance,
        covered=tuple(area for area in Area if area in covered),
        missing=tuple(missing),
        findings=findings,
    )
