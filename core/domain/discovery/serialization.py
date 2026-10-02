"""Reading a stored discovery run back: the result rebuilt part by part from what its own ``to_dict``
produced — validated again by each constructor, the proposed architecture by the IR's ``from_dict`` —
and refused when its fingerprint no longer matches its content; decisions, acceptances and the error
likewise. Keys added for display (ids derived from content, summaries) are ignored: recomputed."""

from collections.abc import Mapping
from typing import Any

from core.architecture_ir.serialization import from_dict as ir_from_dict
from core.domain.migrations.serialization import from_dict

from .errors import InvalidDiscoveryResult
from .findings import CandidateEntity, CandidateRelationship, Diagnostic, Finding, SourceArtifact
from .results import DiscoveryResult, ProposedElement, ValidationIssue
from .runs import Acceptance, ReviewDecision, RunError


def result_from_dict(data: Mapping[str, Any], fingerprint: str) -> DiscoveryResult:
    """A stored result, verified against the fingerprint stored with it."""
    try:
        result = DiscoveryResult(
            artifacts=tuple(from_dict(SourceArtifact, a) for a in data["artifacts"]),
            findings=tuple(from_dict(Finding, f) for f in data["findings"]),
            entities=tuple(from_dict(CandidateEntity, e) for e in data["entities"]),
            relationships=tuple(from_dict(CandidateRelationship, r) for r in data["relationships"]),
            diagnostics=tuple(from_dict(Diagnostic, d) for d in data["diagnostics"]),
            proposed=ir_from_dict(data["proposed"]) if data.get("proposed") is not None else None,
            validation=tuple(from_dict(ValidationIssue, v) for v in data["validation"]),
            elements=tuple(from_dict(ProposedElement, e) for e in data.get("elements", ())),
            extractors=dict(data["extractors"]),
            version=data["version"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidDiscoveryResult(details={"fields": ["result"]}) from error
    if result.fingerprint != fingerprint:
        raise InvalidDiscoveryResult(details={"fields": ["fingerprint"]})
    return result


def decisions_from_list(data: list[Any]) -> tuple[ReviewDecision, ...]:
    return tuple(from_dict(ReviewDecision, d) for d in data)


def acceptances_from_list(data: list[Any]) -> tuple[Acceptance, ...]:
    return tuple(from_dict(Acceptance, a) for a in data)


def error_from_dict(data: Mapping[str, Any] | None) -> RunError | None:
    return from_dict(RunError, data) if data is not None else None
