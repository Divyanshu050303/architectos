"""Reading stored drift records back: each part rebuilt from what its own ``to_dict`` produced and
validated again by its constructor; a result whose fingerprint no longer matches its content is
refused. Keys added for display (ids derived from content, summaries) are ignored: recomputed."""

from collections.abc import Mapping
from typing import Any

from core.domain.migrations.serialization import from_dict

from .analyses import AnalysisError, DriftRequest, DriftResult
from .errors import InvalidDriftResult
from .identity import IdentityMapping
from .items import DriftItem


def result_from_dict(data: Mapping[str, Any], fingerprint: str) -> DriftResult:
    """A stored result, verified against the fingerprint stored with it."""
    try:
        result = from_dict(DriftResult, data)
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidDriftResult(details={"fields": ["result"]}) from error
    if result.fingerprint != fingerprint:
        raise InvalidDriftResult(details={"fields": ["fingerprint"]})
    return result


def request_from_dict(data: Mapping[str, Any]) -> DriftRequest:
    return from_dict(DriftRequest, data)


def error_from_dict(data: Mapping[str, Any] | None) -> AnalysisError | None:
    return from_dict(AnalysisError, data) if data is not None else None


def item_from_dict(data: Mapping[str, Any]) -> DriftItem:
    return from_dict(DriftItem, data)


def mapping_from_dict(data: Mapping[str, Any]) -> IdentityMapping:
    return from_dict(IdentityMapping, data)
