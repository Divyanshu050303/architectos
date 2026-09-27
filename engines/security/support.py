"""Readings of declared security facts that several analyzers share, each three-valued where the
architecture may not say: True, False, or None (not modeled). Nothing here assumes a default."""

from typing import TYPE_CHECKING, Any

from core.architecture_ir.dependency import ENCRYPTED_PROTOCOLS
from core.architecture_ir.edge import Connection
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.facts import ElementFacts, certainty_of, labelled_evidence
from core.domain.security.inputs import ConnectionSecurity
from core.domain.security.results import FindingType, SecurityFinding
from core.domain.validation.results import Severity

if TYPE_CHECKING:
    from .engine import AnalyzerMeta

# Shared with every engine that reads declared facts (core/domain/facts.py), under the names the
# security analyzers use.
evidence = labelled_evidence
certainty = certainty_of

MECHANISM_NOT_VERIFIED = (
    "A declared control is taken as the architecture names it; whether it is implemented and "
    "configured correctly is not established."
)


def transport_protected(connection: Connection, facts: ConnectionSecurity) -> bool | None:
    """Encrypted in transit by what is declared: ``tls`` true or an encrypted protocol (True),
    ``tls`` false over a protocol that is not encrypted by definition (False), else None."""
    tls = facts.known("tls")
    if tls is True or connection.protocol in ENCRYPTED_PROTOCOLS:
        return True
    return False if tls is False else None


def authenticated(facts: ElementFacts) -> bool | None:
    """Whether the element declares an authentication mechanism (True), declares ``none`` (False),
    or does not say (None)."""
    value = facts.known("authentication")
    return None if value is None else value != "none"


def protocol_evidence(connection: Connection) -> tuple[Evidence, ...]:
    return (Evidence(f"{connection.id}.protocol", connection.protocol),) if connection.protocol else ()


def finding(
    meta: AnalyzerMeta, type_: FindingType, severity: Severity, certainty_: Certainty, **fields: Any
) -> SecurityFinding:
    """A finding of the analyzer ``meta`` describes (its id and version recorded)."""
    return SecurityFinding(
        type=type_,
        severity=severity,
        certainty=certainty_,
        analyzer_id=meta.id,
        analyzer_version=meta.version,
        **fields,
    )
