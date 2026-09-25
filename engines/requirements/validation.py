"""Validation of extracted candidates: the gate between "proposed" and "worth analyzing".

Every candidate, whatever produced it (rules now, a language model later), goes through the same
deterministic checks, in order:

1. **Provenance**: a candidate that points at text must point at text that is really in the input
   (a proposal that cannot show where it came from is rejected, not repaired).
2. **Domain rules**: exactly the validation a person's own requirement gets (metric allowed for the
   type and category, unit, bounds, lengths). A requirement that breaks them — 150 % availability, a
   negative rate — is *blocking*: the input asks for something impossible.
3. **Duplicates**: the same interpretation stated twice ("2000 rps … 2k requests/sec") is kept once.

Normalization problems (ambiguous or unsupported units, negative or oversized values) become
findings too. Unresolved classifications ("10M users") are passed on untouched: the ambiguity and
assumption engines own them.
"""

from dataclasses import dataclass

from core.domain.requirements.analysis import Severity
from core.domain.requirements.candidates import RequirementCandidate
from core.domain.requirements.errors import InvalidRequirement

from .extractor import Extraction, Unresolved
from .findings import Finding, FindingKind

# Why a candidate breaks the domain rules, in words, and how to fix it. Unknown reasons fall back
# to a generic message: the code is always the domain's own.
_INVALID: dict[str, tuple[str, str | None]] = {
    "out_of_range": (
        "The value is outside what the metric allows.",
        "Use a positive value; percentages cannot exceed 100 %.",
    ),
    "unsatisfiable": ("No value can satisfy this bound.", "Relax the bound (e.g. use <= instead of <)."),
    "not_integral": ("This count must be a whole number.", "Use a whole number of users."),
    "not_above_min": ("The range is empty.", "The maximum must be above the minimum."),
    "unknown_for_type": ("This category is not known for the requirement type.", None),
    "not_allowed_for_metric": ("The unit or bound does not fit the metric.", None),
    "length": ("The text is empty or too long.", "Split long statements into separate requirements."),
    "control_characters": ("The text contains control characters.", "Remove invisible characters."),
}
_PROBLEMS: dict[str, tuple[Severity, str, str]] = {
    "negative_value": (
        Severity.BLOCKING,
        "A negative quantity cannot be a requirement.",
        "State the value as a positive number.",
    ),
    "out_of_range": (Severity.BLOCKING, "The value is too large to be meaningful.", "Check the number."),
    "too_precise": (Severity.WARNING, "The value has more than 9 decimal places.", "Round the value."),
    "ambiguous_unit": (
        Severity.WARNING,
        "The unit is ambiguous (e.g. m: milli, million or minutes; gb: bits or bytes).",
        "Spell the unit out: ms, minutes, M users, GB.",
    ),
    "unsupported_unit": (
        Severity.WARNING,
        "The unit is not supported.",
        "Use a supported unit (see the requirements documentation), e.g. GB instead of GiB, "
        "or a budget per month.",
    ),
}


@dataclass(frozen=True, slots=True)
class Validated:
    raw_input: str
    candidates: tuple[RequirementCandidate, ...]  # valid, unique, provenance checked
    unresolved: tuple[Unresolved, ...]  # classification left open: for the ambiguity engines
    findings: tuple[Finding, ...]


def _identity(candidate: RequirementCandidate) -> tuple[object, ...]:
    content = candidate.content
    return (content.type, content.category, content.scope, repr(content.structured_data))


def _invalid(candidate: RequirementCandidate, error: InvalidRequirement) -> Finding:
    reason = str(error.details.get("reason", "invalid"))
    message, suggestion = _INVALID.get(reason, ("The requirement breaks the rules.", None))
    return Finding(
        FindingKind.INVALID,
        reason,
        Severity.BLOCKING,
        f"“{candidate.content.title}”: {message}",
        candidate_keys=(candidate.key,),
        span=candidate.span,
        field=error.details.get("field"),
        suggestion=suggestion,
    )


def _invalid_structure(reason: str) -> tuple[Severity, str, str] | None:
    """Reasons the extractor reports as ``invalid_<domain reason>``: a stated value the domain's
    structure refuses (an empty range, an impossible percentile) is as blocking as any invalid one."""
    if not reason.startswith("invalid_"):
        return None
    message, suggestion = _INVALID.get(reason.removeprefix("invalid_"), ("The value is invalid.", None))
    return Severity.BLOCKING, message, suggestion or "Restate the value."


def validate(extraction: Extraction) -> Validated:
    raw = extraction.raw_input
    kept: list[RequirementCandidate] = []
    seen: dict[tuple[object, ...], RequirementCandidate] = {}
    findings: list[Finding] = []
    for candidate in extraction.candidates:
        if candidate.span is not None and not candidate.span.matches(raw):
            findings.append(
                Finding(
                    FindingKind.REJECTED,
                    "span_not_in_input",
                    Severity.WARNING,
                    f"“{candidate.content.title}” was dropped: it points at text that is not in the input.",
                    candidate_keys=(candidate.key,),
                    suggestion="Restate the requirement explicitly.",
                )
            )
            continue
        problem = candidate.problem()
        if problem is not None:
            findings.append(_invalid(candidate, problem))
            continue
        first = seen.get(_identity(candidate))
        if first is not None:
            findings.append(
                Finding(
                    FindingKind.DUPLICATE,
                    "stated_twice",
                    Severity.INFO,
                    f"“{candidate.content.title}” is stated twice; it is kept once.",
                    candidate_keys=(first.key, candidate.key),
                    span=candidate.span,
                )
            )
            continue
        seen[_identity(candidate)] = candidate
        kept.append(candidate)

    open_items: list[Unresolved] = []
    for item in extraction.unresolved:
        known = _PROBLEMS.get(item.reason) or _invalid_structure(item.reason)
        if known is None:
            open_items.append(item)
            continue
        severity, message, suggestion = known
        findings.append(
            Finding(
                FindingKind.UNRESOLVED,
                item.reason,
                severity,
                f"“{item.span.text}”: {message}",
                span=item.span,
                suggestion=suggestion,
            )
        )
    return Validated(raw, tuple(kept), tuple(open_items), tuple(findings))
