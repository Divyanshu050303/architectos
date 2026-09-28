"""The constraint engine: every node of an architecture revision that refers to a catalog component,
evaluated against that component's specification — the current version, or the exact version an
earlier evaluation used (``versions``), so a historical result can be reproduced. Nodes without a
component reference are not evaluated (nothing links them to a specification; names are never
matched). The evaluation records every specification version it used and the catalog's
fingerprint."""

from collections.abc import Mapping

from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.components.errors import ComponentNotFound
from core.domain.components.evaluation import ConstraintEvaluation, ConstraintFinding
from core.domain.components.repository import ComponentCatalog
from core.domain.components.specifications import ComponentSpecification
from core.domain.validation.options import RevisionInfo

from .evaluator import evaluate_node, not_in_catalog


class DeterministicConstraintEngine:
    def evaluate_architecture(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        catalog: ComponentCatalog,
        versions: Mapping[str, int] | None = None,
    ) -> ConstraintEvaluation:
        findings: list[ConstraintFinding] = []
        used: dict[str, str] = {}
        for node in sorted(ir.nodes, key=lambda n: n.id):
            if node.component is None:
                continue
            try:
                spec = catalog.get(node.component, (versions or {}).get(node.component))
            except ComponentNotFound:
                findings.append(not_in_catalog(node))
                continue
            used[spec.ref] = spec.content_hash
            findings += evaluate_node(node, spec)
        return ConstraintEvaluation(tuple(findings), used, catalog.fingerprint, revision)

    def evaluate_node(
        self, node: Node, specification: ComponentSpecification, catalog: ComponentCatalog
    ) -> ConstraintEvaluation:
        """A configuration evaluated against one specification, outside any architecture."""
        findings = evaluate_node(node, specification)
        used = {specification.ref: specification.content_hash}
        return ConstraintEvaluation(tuple(findings), used, catalog.fingerprint)
