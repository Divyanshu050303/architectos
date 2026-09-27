"""Data-protection analysis: where the architecture does not model how sensitive its data is, so
encryption and access to it cannot be judged.

Sensitivity is declared, never guessed from a name or a technology: ``data_classification``
(``public`` < ``internal`` < ``confidential`` < ``restricted``) and ``personal_data``.

- A data store (database, cache, object storage, queue, observability store) whose sensitivity is
  not established — no classification, and not declared to hold personal data — is
  ``data_classification_not_modeled``: whether it needs encryption or tighter access is unknown,
  never assumed "not needed".
- A flow crossing a declared boundary whose own sensitivity is not declared, between ends of which
  at least one is not classified either (and neither is declared sensitive), is reported the same
  way: what crosses the boundary is unknown.

Policies that require every element to be classified are checked by the policy analyzer.
"""

from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.security.inputs import STORES
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress
from .support import certainty, evidence, finding
from .trust_boundaries import crossings

T = FindingType
CLASSIFICATION = ("data_classification", "personal_data")


class DataProtection:
    meta = AnalyzerMeta(
        id="data-protection",
        version=1,
        name="Data protection",
        description="Data stores and boundary-crossing flows whose data sensitivity is not modeled.",
        category=FindingCategory.DATA_PROTECTION,
        finding_types=(T.DATA_CLASSIFICATION_NOT_MODELED,),
        inputs=("components", "connections", "boundaries"),
        properties=CLASSIFICATION,
        rules=(
            "A data store with no data classification that is not declared to hold personal data cannot "
            "be judged for encryption or access.",
            "A flow crossing a declared boundary whose sensitivity is not declared, between ends not both "
            "classified, carries data of unknown sensitivity.",
        ),
        unsupported=("Sensitivity inferred from names, schemas or technologies (never done).",),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        findings = [f for node in context.components if (f := self._store(context, node))]
        found = crossings(context)
        for connection in context.connections:
            if connection.id in found and (f := self._flow(context, connection)):
                findings.append(f)
        return AnalyzerOutput(tuple(findings))

    def _store(self, context: SecurityContext, node: Node) -> SecurityFinding | None:
        facts = context.facts[node.id]
        if node.kind not in STORES or facts.sensitive is not None:
            return None
        return finding(
            self.meta,
            T.DATA_CLASSIFICATION_NOT_MODELED,
            Severity.LOW,
            certainty((facts, CLASSIFICATION)),
            title=f"How sensitive the data in {node.id} is, is not modeled",
            explanation=f"{node.id} stores data, but its classification is not declared (nor that it holds "
            "personal data): whether it needs encryption or tighter access cannot be judged.",
            recommendation=f"State the data_classification of {node.id}, and personal_data if it holds any.",
            node_ids=(node.id,),
            evidence=evidence(facts, CLASSIFICATION),
            missing=(f"{node.id}.configuration.data_classification",),
        )

    def _flow(self, context: SecurityContext, connection: Connection) -> SecurityFinding | None:
        if not connection.kind.communicates:
            return None
        link = context.connection_facts[connection.id]
        ends = (context.facts[connection.source_id], context.facts[connection.target_id])
        if link.sensitive is not None or any(e.sensitive is True for e in ends):
            return None  # declared (on the flow, or sensitive at an end: the encryption analyzer's)
        if all(e.sensitive is False for e in ends):
            return None  # both ends declared non-sensitive
        label = f"{connection.id} ({connection.source_id} → {connection.target_id})"
        return finding(
            self.meta,
            T.DATA_CLASSIFICATION_NOT_MODELED,
            Severity.LOW,
            certainty((link, CLASSIFICATION), *((e, CLASSIFICATION) for e in ends)),
            title=f"How sensitive the data crossing a boundary over {label} is, is not modeled",
            explanation=f"{connection.id} crosses a declared boundary, but neither it nor both of its ends "
            "declare how sensitive the data is: what crosses cannot be judged.",
            recommendation=f"State the data_classification of {connection.id} (and personal_data if any).",
            node_ids=(connection.source_id, connection.target_id),
            connection_ids=(connection.id,),
            evidence=tuple(e for facts in (link, *ends) for e in evidence(facts, CLASSIFICATION)),
            missing=(f"{connection.id}.configuration.data_classification",),
        )
