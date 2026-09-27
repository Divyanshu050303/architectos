"""Readings of declared security facts that several analyzers share, each three-valued where the
architecture may not say: True, False, or None (not modeled). Nothing here assumes a default."""

from collections.abc import Iterable

from core.architecture_ir.dependency import ENCRYPTED_PROTOCOLS
from core.architecture_ir.edge import Connection
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.facts import ElementFacts
from core.domain.security.inputs import ConnectionSecurity
from core.domain.security.values import redacted

MECHANISM_NOT_VERIFIED = (
    "A declared control is taken as the architecture names it; whether it is implemented and "
    "configured correctly is not established."
)


def evidence(facts: ElementFacts, names: Iterable[str]) -> tuple[Evidence, ...]:
    """The declared facts among ``names``, labelled with their element (``api.configuration.x``),
    with their provenance; a secret-looking value is never shown."""
    return tuple(redacted(f"{facts.element_id}.{e.label}", e.value) for e in facts.evidence(names))


def certainty(*used: tuple[ElementFacts, Iterable[str]]) -> Certainty:
    """``modeled`` when every fact used was declared by a person or read from a system;
    ``candidate`` when any was inferred or proposed by a language model."""
    for facts, names in used:
        if any(facts.facts[n].proposed for n in names if n in facts.facts):
            return Certainty.CANDIDATE
    return Certainty.MODELED


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
