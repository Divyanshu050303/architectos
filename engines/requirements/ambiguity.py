"""Ambiguity detection: statements that cannot become precise engineering constraints as written.

Three sources, all deterministic:

1. **Vague language**: "fast", "high traffic", "large scale", "many users", "highly available",
   "global", "should scale well", "cheap". A vague phrase is only reported when the input does not
   quantify that concern anywhere else ("a fast API … p95 under 300 ms" is resolved).
2. **Quantities that could not be classified**: "10M users" (daily? monthly? registered?
   concurrent?), "within 5 minutes" (a latency? an RTO?), "40 %" (of what?), "2000 connections".
3. **Valid but incomplete candidates**: latency without a percentile, encryption without saying at
   rest or in transit, interpretations with low confidence.

Nothing is ever filled in: each finding says what is missing, offers the readings the text allows,
and suggests how to state it. Ambiguities are warnings (or info); whether a missing concern blocks
architecture work is the completeness engine's decision.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from core.domain.requirements.analysis import Severity
from core.domain.requirements.candidates import RequirementCandidate, SourceSpan
from core.domain.requirements.value_objects import QuantityConstraint, RangeConstraint

from .findings import Finding, FindingKind
from .validation import Validated

LOW_CONFIDENCE = Decimal("0.7")

_TRAFFIC = frozenset({"requests_per_second", "orders_per_second", "daily_active_users", "concurrent_users"})
_USERS = frozenset({"daily_active_users", "monthly_active_users", "concurrent_users"})


@dataclass(frozen=True, slots=True)
class Vague:
    code: str
    pattern: re.Pattern[str]
    resolved_by: frozenset[str]  # metrics (or "category:<name>") that make the phrase precise
    message: str
    suggestion: str


def _phrase(*phrases: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(phrases) + r")\b", re.IGNORECASE)


VAGUE: tuple[Vague, ...] = (
    Vague(
        "vague_traffic",
        _phrase(
            r"(?:high|heavy|huge|massive|lots\s+of|a\s+lot\s+of)\s+(?:traffic|load)",
            r"(?:large|massive|web|internet)\s+scale",
            r"at\s+scale",
        ),
        _TRAFFIC,
        "The traffic volume is unspecified.",
        "State the peak load, e.g. at least 2,000 requests/second.",
    ),
    Vague(
        "vague_user_count",
        _phrase(
            r"(?:many|lots\s+of|a\s+lot\s+of|tons\s+of|millions\s+of|thousands\s+of)\s+users",
            r"large\s+user\s+base",
        ),
        _USERS,
        "The number of users is unspecified.",
        "State daily or monthly active users, e.g. 100,000 daily active users.",
    ),
    Vague(
        "vague_latency",
        _phrase(
            "fast", r"quick(?:ly)?", "responsive", r"low[\s-]latency", "snappy", r"instant(?:ly|aneous)?"
        ),
        frozenset({"latency"}),
        "The latency target is unspecified.",
        "Specify a latency target, e.g. p95 <= 300 ms.",
    ),
    Vague(
        "vague_availability",
        _phrase(
            r"high(?:ly)?[\s-]+availab(?:le|ility)",
            r"always\s+(?:up|on|available|online)",
            r"24/7",
            r"(?:no|zero)\s+downtime",
        ),
        frozenset({"availability"}),
        "The availability target is unspecified.",
        "State an availability target, e.g. 99.9 % (about 43 minutes of downtime a month).",
    ),
    Vague(
        "vague_scalability",
        _phrase(
            r"scale\s+well", r"(?:highly\s+)?scalable", r"should\s+scale", r"elastic(?:ally)?", "infinitely"
        ),
        _TRAFFIC,
        "Scaling expectations are unspecified.",
        "State the expected peak load and growth, e.g. 2,000 requests/second, doubling yearly.",
    ),
    Vague(
        "vague_geography",
        _phrase(r"global(?:ly)?", "worldwide", r"around\s+the\s+world", r"multi[\s-]region"),
        frozenset({"regions"}),
        "Where the system must run is unspecified.",
        "Name the regions, e.g. eu-west-1 and us-east-1, or where the users are.",
    ),
    Vague(
        "vague_reliability",
        _phrase("reliable", r"(?:no|zero)\s+data\s+loss", r"never\s+lose\s+data"),
        frozenset({"rpo", "rto", "durability"}),
        "The recovery objectives are unspecified.",
        "State an RPO and RTO, e.g. RPO <= 5 minutes (RPO = 0 for no data loss) and RTO <= 1 hour.",
    ),
    Vague(
        "vague_cost",
        _phrase(
            "cheap", r"low[\s-]cost", r"cost[\s-]effective", "affordable", r"within\s+budget", "inexpensive"
        ),
        frozenset({"monthly_budget"}),
        "The budget is unspecified.",
        "State a monthly budget, e.g. at most 5,000 USD/month.",
    ),
    Vague(
        "vague_data_volume",
        _phrase(r"(?:lots|a\s+lot|large\s+amounts?|huge\s+amounts?)\s+of\s+data", r"big\s+data"),
        frozenset({"storage"}),
        "The data volume is unspecified.",
        "State the storage or daily volume, e.g. 2 TB, growing 50 GB per day.",
    ),
    Vague(
        "vague_security",
        _phrase(r"secure(?:ly)?"),
        frozenset(
            {"category:encryption", "category:authentication", "category:authorization", "category:pii"}
        ),
        "The security requirements are unspecified.",
        "Name them, e.g. encrypt data at rest and in transit, SSO with MFA, role-based access.",
    ),
)

# Unresolved classifications, in words.
_UNRESOLVED: dict[str, tuple[str, str]] = {
    "user_count_kind_unspecified": (
        "Which users is this: daily active, monthly active, registered or concurrent?",
        "Say which, e.g. 10M monthly active users; the architecture differs by orders of magnitude.",
    ),
    "duration_purpose_unspecified": (
        "What this duration limits is unclear: a response time, a recovery objective or a retention?",
        "Say what it applies to, e.g. p95 latency under 5 s, or restore within 5 minutes.",
    ),
    "percentage_purpose_unspecified": (
        "What this percentage measures is unclear.",
        "Say what it applies to, e.g. 99.9 % availability.",
    ),
    "unitless_number": (
        "This number has no unit the engine understands.",
        "Add a unit, e.g. requests/second, users, ms, GB.",
    ),
}
_UNREADABLE = ("This could not be read as a precise requirement.", "Restate it with a number and a unit.")

_ENCRYPTION_SCOPE = re.compile(r"\b(?:at\s+rest|in\s+transit|end[\s-]to[\s-]end|tls|https)\b", re.IGNORECASE)


def _quantified(candidates: tuple[RequirementCandidate, ...]) -> set[str]:
    """Metrics (and qualitative categories) the input already states precisely."""
    found: set[str] = set()
    for candidate in candidates:
        constraint = candidate.content.constraint
        if constraint is not None:
            found.add(constraint.metric)
        found.add(f"category:{candidate.content.category}")
    return found


def _vague_findings(validated: Validated) -> list[Finding]:
    quantified = _quantified(validated.candidates)
    findings: list[Finding] = []
    for vague in VAGUE:
        if vague.resolved_by & quantified:
            continue
        matches = list(vague.pattern.finditer(validated.raw_input))
        if not matches:
            continue
        # One finding per kind of vagueness, at its first occurrence: repeating "fast" a thousand
        # times says nothing new, and must not produce a thousand findings.
        first, more = matches[0], len(matches) - 1
        also = f" (and {more} more time{'s' if more > 1 else ''})" if more else ""
        findings.append(
            Finding(
                FindingKind.AMBIGUITY,
                vague.code,
                Severity.WARNING,
                f"“{first.group(0)}”{also}: {vague.message}",
                span=SourceSpan(first.start(), first.end(), first.group(0)),
                suggestion=vague.suggestion,
            )
        )
    return findings


def _unresolved_findings(validated: Validated) -> list[Finding]:
    findings = []
    for item in validated.unresolved:
        message, suggestion = _UNRESOLVED.get(item.reason, _UNREADABLE)
        findings.append(
            Finding(
                FindingKind.AMBIGUITY,
                item.reason,
                Severity.WARNING,
                f"“{item.span.text}”: {message}",
                span=item.span,
                suggestion=suggestion,
                options=item.options,
            )
        )
    return findings


def _candidate_findings(candidate: RequirementCandidate) -> list[Finding]:
    content, findings = candidate.content, []
    constraint = content.constraint
    title = f"“{content.title}”"
    unmeasured = (
        isinstance(constraint, QuantityConstraint | RangeConstraint) and constraint.percentile is None
    )
    if unmeasured and constraint is not None and constraint.metric == "latency":
        findings.append(
            Finding(
                FindingKind.AMBIGUITY,
                "missing_percentile",
                Severity.WARNING,
                f"{title}: the latency target does not say at which percentile.",
                candidate_keys=(candidate.key,),
                span=candidate.span,
                field="structured_data.percentile",
                suggestion="Say which requests it covers, e.g. p95 (95 % of requests) or p99.",
                options=("p50", "p95", "p99"),
            )
        )
    if content.category == "encryption" and not _ENCRYPTION_SCOPE.search(content.statement):
        findings.append(
            Finding(
                FindingKind.AMBIGUITY,
                "encryption_scope_unspecified",
                Severity.INFO,
                f"{title}: it does not say whether data is encrypted at rest, in transit or both.",
                candidate_keys=(candidate.key,),
                span=candidate.span,
                suggestion="Say at rest, in transit, or both.",
                options=("at_rest", "in_transit", "both"),
            )
        )
    if candidate.confidence < LOW_CONFIDENCE:
        findings.append(
            Finding(
                FindingKind.AMBIGUITY,
                "low_confidence",
                Severity.WARNING,
                f"{title}: the interpretation is uncertain (confidence {candidate.confidence}).",
                candidate_keys=(candidate.key,),
                span=candidate.span,
                suggestion="Check the interpretation before promoting it.",
            )
        )
    return findings


def find_ambiguities(validated: Validated) -> list[Finding]:
    """Every ambiguity in the validated extraction; pure and deterministic."""
    findings = _vague_findings(validated) + _unresolved_findings(validated)
    for candidate in validated.candidates:
        findings.extend(_candidate_findings(candidate))
    return findings
