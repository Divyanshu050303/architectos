"""Encryption analysis: whether sensitive data is modeled as encrypted at rest and in transit.

Only what the architecture declares counts: ``encryption_at_rest`` on data stores (databases,
caches, object storage, queues, observability stores), ``tls`` or an encrypted protocol on
connections, and the data's declared sensitivity (``data_classification`` ``confidential`` or
``restricted``, or ``personal_data``). A technology that commonly supports encryption is not taken
as encrypting; no algorithm, key length or protocol version is assumed; a ``true`` flag says the
architecture declares encryption, not that it is implemented well.

- **At rest.** A store declared sensitive with ``encryption_at_rest: false`` is a modeled gap
  (``unencrypted_data_at_rest``); without a declared value it cannot be evaluated
  (``encryption_not_modeled``).
- **In transit.** A flow carries sensitive data when the connection is declared sensitive, or, when
  its own sensitivity is not declared, it reads, writes, replicates or queues data of a store at
  either end declared sensitive. Declared unencrypted (``tls: false`` over a protocol that is not
  encrypted by definition) is a gap (``unencrypted_data_in_transit``); not declared cannot be
  evaluated. Crossings the trust-boundary analyzer already reported are not repeated.

Stores and flows whose sensitivity is not modeled are the data-protection analyzer's to report.
"""

from typing import Any

from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.facts import ElementFacts
from core.domain.security.inputs import STORES, ComponentSecurity, ConnectionSecurity
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress
from .support import (
    MECHANISM_NOT_VERIFIED,
    certainty,
    evidence,
    finding,
    protocol_evidence,
    transport_protected,
)

T = FindingType
CLASSIFICATION = ("data_classification", "personal_data")
# Flows that move a store's data (a plain request to a sensitive service need not carry its data).
DATA_FLOWS = frozenset(
    {ConnectionKind.DATA_ACCESS, ConnectionKind.REPLICATION, ConnectionKind.PUBLISH, ConnectionKind.CONSUME}
)
# Findings of the trust-boundary analyzer that already cover a connection's transport protection.
CROSSING_TYPES = frozenset({T.UNPROTECTED_BOUNDARY_CROSSING, T.CROSSING_CONTROLS_NOT_MODELED})
NO_ALGORITHM = "No algorithm, key length, key management or protocol version is assumed or checked."


def _severity(*facts: ComponentSecurity | ConnectionSecurity) -> Severity:
    """High for restricted or personal data, medium for confidential."""
    most = any(
        f.known("data_classification") == "restricted" or f.known("personal_data") is True for f in facts
    )
    return Severity.HIGH if most else Severity.MEDIUM


class Encryption:
    meta = AnalyzerMeta(
        id="encryption",
        version=1,
        name="Encryption",
        description="Whether data declared sensitive is modeled as encrypted at rest and in transit.",
        category=FindingCategory.ENCRYPTION,
        finding_types=(T.UNENCRYPTED_DATA_AT_REST, T.UNENCRYPTED_DATA_IN_TRANSIT, T.ENCRYPTION_NOT_MODELED),
        inputs=("components", "connections", "findings"),
        properties=("encryption_at_rest", "tls", "data_classification", "personal_data"),
        rules=(
            "A data store declared sensitive with encryption_at_rest false is a gap; without a declared "
            "value it cannot be evaluated.",
            "A flow carrying sensitive data (declared on the connection, or moving the data of a store "
            "declared sensitive) that declares tls false over an unencrypted protocol is a gap; without "
            "tls declared or an encrypted protocol it cannot be evaluated.",
        ),
        unsupported=(
            "Algorithms, key lengths, key management and protocol versions.",
            "Whether a declared encryption flag is implemented correctly.",
            "Data whose sensitivity is not declared (reported by the data-protection analyzer).",
        ),
        limitations=(MECHANISM_NOT_VERIFIED, NO_ALGORITHM),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        covered = {c for f in progress.findings if f.type in CROSSING_TYPES for c in f.connection_ids}
        findings = [f for node in context.components if (f := self._at_rest(context, node))]
        for connection in context.connections:
            if connection.id not in covered and (f := self._in_transit(context, connection)):
                findings.append(f)
        return AnalyzerOutput(tuple(findings))

    def _at_rest(self, context: SecurityContext, node: Node) -> SecurityFinding | None:
        facts = context.facts[node.id]
        if node.kind not in STORES or facts.sensitive is not True:
            return None
        encrypted = facts.known("encryption_at_rest")
        if encrypted is True:
            return None
        props = ("encryption_at_rest", *CLASSIFICATION)
        common: dict[str, Any] = {
            "node_ids": (node.id,),
            "evidence": evidence(facts, props),
            "assumptions": (NO_ALGORITHM,),
        }
        if encrypted is False:
            return finding(
                self.meta,
                T.UNENCRYPTED_DATA_AT_REST,
                _severity(facts),
                certainty((facts, props)),
                title=f"{node.id} stores sensitive data unencrypted",
                explanation=f"{node.id} is declared to hold sensitive data and declares "
                "encryption_at_rest false: its stored data, backups and snapshots are readable by anyone "
                "who obtains the storage.",
                recommendation=f"Review whether {node.id} should encrypt its data at rest.",
                **common,
            )
        return finding(
            self.meta,
            T.ENCRYPTION_NOT_MODELED,
            Severity.MEDIUM,
            certainty((facts, props)),
            title=f"Whether {node.id} encrypts its sensitive data at rest is not modeled",
            explanation=f"{node.id} is declared to hold sensitive data, but the architecture does not say "
            "whether it is encrypted at rest: it is neither shown encrypted nor shown unencrypted.",
            recommendation=f"State encryption_at_rest for {node.id}.",
            missing=(f"{node.id}.configuration.encryption_at_rest",),
            **common,
        )

    def _in_transit(self, context: SecurityContext, connection: Connection) -> SecurityFinding | None:
        if not connection.kind.communicates:
            return None  # a dependency's content is not described
        link = context.connection_facts[connection.id]
        stores = [
            context.facts[node_id]
            for node_id in (connection.source_id, connection.target_id)
            if (n := context.topology.node(node_id)) is not None and n.kind in STORES
        ]
        sources: list[ComponentSecurity | ConnectionSecurity]
        if link.sensitive is True:
            sources = [link]
        elif link.sensitive is None and connection.kind in DATA_FLOWS:
            sources = [s for s in stores if s.sensitive is True]
        else:
            sources = []
        if not sources:
            return None
        protected = transport_protected(connection, link)
        if protected is True:
            return None
        label = f"{connection.id} ({connection.source_id} → {connection.target_id})"
        used: list[tuple[ElementFacts, tuple[str, ...]]] = [(link, ("tls", *CLASSIFICATION))]
        used += [(s, CLASSIFICATION) for s in sources if s is not link]
        common: dict[str, Any] = {
            "node_ids": (connection.source_id, connection.target_id),
            "connection_ids": (connection.id,),
            "evidence": protocol_evidence(connection)
            + tuple(e for facts, props in used for e in evidence(facts, props)),
            "assumptions": (NO_ALGORITHM,),
        }
        if protected is False:
            return finding(
                self.meta,
                T.UNENCRYPTED_DATA_IN_TRANSIT,
                _severity(*sources),
                certainty(*used),
                title=f"{label} carries sensitive data unencrypted",
                explanation=f"{connection.id} carries data declared sensitive and declares tls false over "
                f"{connection.protocol or 'an unstated protocol'}: anyone on the network path can read it.",
                recommendation=f"Review whether {connection.id} should be encrypted in transit.",
                **common,
            )
        return finding(
            self.meta,
            T.ENCRYPTION_NOT_MODELED,
            Severity.MEDIUM,
            certainty(*used),
            title=f"Whether {label} encrypts sensitive data in transit is not modeled",
            explanation=f"{connection.id} carries data declared sensitive, but neither tls nor an encrypted "
            "protocol is declared: it is neither shown encrypted nor shown unencrypted.",
            recommendation=f"State tls for {connection.id} (or its encrypted protocol).",
            missing=(f"{connection.id}.configuration.tls",),
            **common,
        )
