"""Helpers the observability analyzers share: element-labelled redacted evidence and certainty
(``core/domain/facts.py``, shared with the other engines), and the finding constructor."""

from typing import TYPE_CHECKING, Any

from core.domain.capacity.results import Certainty
from core.domain.facts import certainty_of, labelled_evidence
from core.domain.observability.results import FindingType, ObservabilityFinding
from core.domain.validation.results import Severity

if TYPE_CHECKING:
    from .engine import AnalyzerMeta

evidence = labelled_evidence
certainty = certainty_of
DECLARED_NOT_VERIFIED = (
    "A declared capability is what the architecture states; that it is emitted, collected, retained "
    "and acted on in production is not established."
)


def finding(
    meta: AnalyzerMeta, type_: FindingType, severity: Severity, certainty_: Certainty, **fields: Any
) -> ObservabilityFinding:
    """A finding of the analyzer ``meta`` describes (its id and version recorded)."""
    return ObservabilityFinding(
        type=type_,
        severity=severity,
        certainty=certainty_,
        analyzer_id=meta.id,
        analyzer_version=meta.version,
        **fields,
    )
