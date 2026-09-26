"""Assumption detection: every interpretation the engine made that the text did not state.

The rule is symmetrical: an assumption the engine *applied* is always surfaced, and one it would
have had to make to go further is never applied at all.

- **Applied, and surfaced here** (each with its reason, a confidence in the assumed reading and the
  candidate it affects; the candidate says which method made it):
  an operator the text did not state ("2000 rps" read as at least 2000), a year as 365 days, a
  month as 30 days, "$" as US dollars, a range's lower value read in its upper value's unit.
- **Declined, never applied**: readings that would change the requirement's meaning by orders of
  magnitude ("10M users": daily active? monthly? registered? concurrent?). Those stay unresolved,
  and the ambiguity engine presents the choice; no candidate is created until a person makes it.

Pure: works on the extraction's notes and the validated candidates only.
"""

from dataclasses import dataclass
from decimal import Decimal

from core.domain.requirements.analysis import Severity

from .extractor import Extraction
from .findings import Finding, FindingKind
from .validation import Validated


@dataclass(frozen=True, slots=True)
class _Kind:
    severity: Severity
    confidence: Decimal  # in the assumed reading
    suggestion: str
    options: tuple[str, ...] = ()


# Why each assumption is safe (or not) and how to remove it. Unknown codes are surfaced as warnings.
KINDS: dict[str, _Kind] = {
    "operator_implied": _Kind(
        Severity.INFO,
        Decimal("0.9"),
        "State the bound explicitly (at least / at most / exactly) if the usual reading is not meant.",
        (">=", "<=", "=="),
    ),
    "year_as_365_days": _Kind(
        Severity.INFO, Decimal("0.95"), "State the retention in days if leap years matter (e.g. 2557 days)."
    ),
    "month_as_30_days": _Kind(
        Severity.INFO, Decimal("0.9"), "State the period in days if calendar months are meant."
    ),
    "dollar_as_usd": _Kind(
        Severity.WARNING,
        Decimal("0.8"),
        "Name the currency (USD, CAD, AUD, …): “$” is used by many.",
        ("USD", "CAD", "AUD", "NZD", "SGD", "HKD"),
    ),
    "unit_shared_in_range": _Kind(
        Severity.INFO, Decimal("0.95"), "Give both ends of the range a unit if they differ."
    ),
}
_UNKNOWN = _Kind(Severity.WARNING, Decimal("0.5"), "Check this interpretation.")


def find_assumptions(extraction: Extraction, validated: Validated) -> list[Finding]:
    """The assumptions behind the candidates that survived validation (a dropped or duplicate
    candidate's assumptions no longer matter)."""
    kept = {candidate.key: candidate for candidate in validated.candidates}
    findings: list[Finding] = []
    for note in extraction.notes:
        candidate = kept.get(note.candidate_key)
        if candidate is None:
            continue
        kind = KINDS.get(note.interpretation.code, _UNKNOWN)
        findings.append(
            Finding(
                FindingKind.ASSUMPTION,
                note.interpretation.code,
                kind.severity,
                f"“{candidate.content.title}”: {note.interpretation.detail}",
                candidate_keys=(candidate.key,),
                span=candidate.span,
                suggestion=kind.suggestion,
                options=kind.options,
                confidence=kind.confidence,
            )
        )
    return findings
