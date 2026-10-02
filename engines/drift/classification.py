"""Coverage-aware classification (rule ``drift-classification@1``): each difference becomes a finding
that says how far the evidence goes — never further.

- **Not comparable**: the inputs are incompatible (one finding per incompatible dimension, nothing
  else is compared), or the difference comes from a source type whose extractor or kind rule changed.
- **Removal** (a baseline element without a match) is ``confirmed`` only when the element was
  discovered from an artifact the run read completely — the scope was inspected, and the element is
  not there. An artifact read only partly makes it ``potential``; an artifact not supplied, or an
  element written by a person (no artifact names it), makes it ``unknown``: absence is not removal.
- **Addition** is ``confirmed`` when the identity between baseline and run is established; otherwise
  ``potential`` — it may be a baseline node under another identifier.
- **Modification** is ``confirmed`` when both sides are stated and the discovered value comes from an
  artifact read completely; ``potential`` when it rests on an inference (a catalog kind, an image),
  on a value no longer declared (not proof it is unset), or on an artifact read only partly.
- **Unresolved** differences (ambiguous identity, unreadable values) are ``unknown``, with what is
  missing.
- **Coverage**: each artifact a baseline element was discovered from that the run did not read
  completely is a ``coverage_changed`` finding — ``unknown``, the scope it leaves uncompared.

"No difference within coverage" is a statement about the inspected scope only.
"""

from core.domain.drift.findings import DriftFinding
from core.domain.drift.values import Classification, Compatibility, ElementType, FindingType

from . import comparison, compatibility
from .comparison import Difference
from .compatibility import Assessment

RULE = "drift-classification@1"
C, K, F = Classification, Compatibility, FindingType
REMOVED = frozenset({F.COMPONENT_REMOVED, F.CONNECTION_REMOVED})
ADDED = frozenset({F.COMPONENT_ADDED, F.CONNECTION_ADDED})
INFERRED = (
    "The discovered side rests on an inference (a kind implied by the catalog, a component from an image)."
)
IDENTITY = (
    "No identity is established between the baseline and the run: it may be a baseline node under another id."
)
UNSET = "Not declared is not proof it is unset."


def _finding(difference: Difference, classification: Classification, limitations: list[str]) -> DriftFinding:
    notes = list(dict.fromkeys([*difference.notes, *limitations]))
    if difference.candidates:
        notes.append(f"Candidates: {', '.join(difference.candidates)}.")
    if classification is not C.CONFIRMED and not notes:
        notes = ["The evidence does not establish it."]
    return DriftFinding(
        difference.type, classification, difference.element, difference.subject, difference.explanation,
        comparison.RULE, difference.path, difference.baseline_id, difference.discovered_key, difference.match,
        difference.baseline_value, difference.discovered_value, difference.redacted, difference.evidence,
        difference.locations, difference.baseline_reference, tuple(notes),
    )  # fmt: skip


def _removal(difference: Difference, assessment: Assessment) -> tuple[Classification, list[str]]:
    artifact = difference.baseline_artifact
    coverage = assessment.coverage
    if artifact is None:
        return C.UNKNOWN, ["It was not discovered from an artifact: absence cannot be established."]
    if artifact in coverage.partial:
        return C.POTENTIAL, [f"{artifact} was read only partly: the element may be in what was not read."]
    if artifact not in coverage.inspected:
        return C.UNKNOWN, [f"{artifact} was not read completely by this run: outside its coverage."]
    if difference.notes:  # an endpoint without a match, or a mapping naming an entity the run lacks
        return C.POTENTIAL, []
    return C.CONFIRMED, []


def _partly_read(difference: Difference, assessment: Assessment) -> list[str]:
    artifacts = {location.split("#", 1)[0].split(":", 1)[0] for location in difference.locations}
    partial = sorted(a for a in artifacts if a in assessment.coverage.partial)
    return [f"{', '.join(partial)} was read only partly."] if partial else []


def classify_difference(difference: Difference, assessment: Assessment) -> DriftFinding:
    if difference.source_type is not None and difference.source_type in assessment.not_comparable:
        note = f"The {difference.source_type.value} extractor or kind rule changed since the baseline."
        return _finding(difference, C.NOT_COMPARABLE, [note])
    if difference.type is F.UNRESOLVED_DIFFERENCE:
        return _finding(difference, C.UNKNOWN, [])
    if difference.type in REMOVED:
        classification, limitations = _removal(difference, assessment)
        return _finding(difference, classification, limitations)
    limitations = _partly_read(difference, assessment)
    if difference.type in ADDED:
        identity = next(c for c in assessment.checks if c.dimension == "identity")
        if identity.outcome is not K.COMPATIBLE:
            limitations.append(IDENTITY)
    elif difference.inferred:
        limitations.append(INFERRED)
    if difference.path is not None and difference.discovered_value is None and not difference.redacted:
        limitations.append(UNSET)
    return _finding(difference, C.POTENTIAL if limitations else C.CONFIRMED, limitations)


def _scope(
    subject: str, message: str, finding_type: FindingType, classification: Classification
) -> DriftFinding:
    return DriftFinding(
        finding_type, classification, ElementType.SCOPE, subject, message, compatibility.RULE,
        limitations=(message,),
    )  # fmt: skip


def incompatibility(assessment: Assessment) -> tuple[DriftFinding, ...]:
    """One finding per incompatible dimension: nothing else is compared."""
    return tuple(
        _scope(f"compatibility:{c.dimension}", c.message, F.COMPARISON_INCOMPATIBLE, C.NOT_COMPARABLE)
        for c in assessment.checks
        if c.outcome is K.INCOMPATIBLE
    )


def coverage_findings(assessment: Assessment) -> tuple[DriftFinding, ...]:
    """Each baseline artifact the run did not read completely: the scope left uncompared."""
    found: list[DriftFinding] = []
    for artifact in sorted(assessment.uninspected):
        state = "read only partly" if artifact in assessment.coverage.partial else "not read completely"
        message = f"{artifact}, which baseline elements were discovered from, was {state} by this run."
        found.append(_scope(f"artifact:{artifact}", message, F.COVERAGE_CHANGED, C.UNKNOWN))
    return tuple(found)


def classify(differences: tuple[Difference, ...], assessment: Assessment) -> tuple[DriftFinding, ...]:
    """The findings of an analysis: incompatible inputs alone, or every classified difference and the
    coverage left uncompared."""
    if assessment.status is K.INCOMPATIBLE:
        return incompatibility(assessment)
    findings = [classify_difference(d, assessment) for d in differences]
    return tuple(sorted([*findings, *coverage_findings(assessment)], key=lambda f: f.sort_key))
