"""From a parsed proposal to a candidate architecture: canonical IR, or why not — never anything between.

    Proposal → an IR document → the IR's own reading (``from_dict``: kinds, ids, references,
    configuration types, ranges and applicability) → the Architecture Engine contract
    (``check_proposal``: generated provenance, never verified, only given requirements)
    → a ``Candidate``

Built by ``agent-candidate@1``, a pure function of the proposal, the requirement set's planning input,
the passages of the context and the component catalog:

- **Provenance.** The architecture, every node, connection and assumption is an ``llm_proposal``
  with the model's stated confidence, the model as its actor, the prompt version as its reference —
  never verified. A person's acceptance makes it a revision; nothing here does.
- **Requirements.** ``REQ-n`` labels become references to exactly the version in the planning input.
- **Components.** A component must be in the catalog, not deprecated, and allowed for the node's
  kind; its technology must agree with the node's. A node with a component and no technology takes
  the component's (recorded as a normalization).
- **Assumptions.** Every ``assumption`` claim becomes an IR assumption, with its own provenance.
- **Evidence.** Every cited passage, with its citation; requirements no element traces to are listed.

**All or nothing.** Any problem rejects the whole proposal with every reason found: a partial
architecture is never presented as a candidate, and nothing is repaired. The deterministic
normalizations (the case of identifiers, a technology taken from its component) are recorded.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from core.architecture_ir.errors import InvalidArchitecture, Violation
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.provenance import ProvenanceSource
from core.architecture_ir.serialization import from_dict
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.architecture_agent.proposals import Proposal, ProposedConnection, ProposedNode
from core.domain.architecture_agent.results import Candidate, EvidenceRef, Rejection
from core.domain.architecture_agent.values import Basis, digest
from core.domain.components.entities import SupportStatus
from core.domain.components.errors import ComponentNotFound
from core.domain.components.repository import ComponentCatalog
from core.domain.requirements.planning import PlanningInputV2
from engines.architecture.service import ArchitectureProposal, check_proposal

RULE = "agent-candidate@1"
MAX_REJECTIONS = 100


@dataclass(frozen=True, slots=True)
class CandidateInput:
    proposal: Proposal
    planning_input: PlanningInputV2  # the requirement set the run designs against
    labels: Mapping[str, uuid.UUID]  # REQ-n → requirement id, as the context listed them
    passages: Mapping[str, EvidenceRef]  # passage id → its citation, as the context listed them
    model: str  # provider/model
    prompt_version: str
    recorded_at: datetime
    requirement_set_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Construction:
    candidate: Candidate | None
    rejections: tuple[Rejection, ...] = ()


def _rejection(violation: Violation) -> Rejection:
    element = violation.element.value if violation.element else "architecture"
    where = f"{element}:{violation.element_id}" if violation.element_id else element
    path = f"{where}.{violation.field}" if violation.field else where
    return Rejection(violation.rule, path[:200], violation.message[:500])


def _label_order(label: str) -> tuple[int, str]:
    number = label.removeprefix("REQ-")
    return (int(number), label) if number.isdigit() else (0, label)


class _Builder:
    def __init__(self, source: CandidateInput, catalog: ComponentCatalog) -> None:
        self.source = source
        self.catalog = catalog
        self.rejections: list[Rejection] = []
        self.normalizations: list[str] = []
        versions = {r["id"]: r["version"] for r in source.planning_input["requirements"]}
        self.refs: dict[str, dict[str, Any]] = {
            label: {"requirement_id": str(rid), "version": versions[str(rid)]}
            for label, rid in source.labels.items()
            if str(rid) in versions
        }

    def provenance(self, confidence: Decimal | None) -> dict[str, Any]:
        return {
            "source": ProvenanceSource.LLM_PROPOSAL.value,
            "confidence": None if confidence is None else str(confidence),  # the IR refuses a missing one
            "actor": f"agent:{self.source.model}"[:128],
            "reference": self.source.prompt_version,
            "verified": False,
            "recorded_at": self.source.recorded_at.isoformat(),
        }

    def requirement_refs(self, labels: tuple[str, ...], path: str) -> list[dict[str, Any]]:
        if any(label not in self.refs for label in labels):
            detail = "Cites a requirement that is not in the requirement set."
            self.rejections.append(Rejection("requirement_not_given", path, detail))
        return [self.refs[label] for label in dict.fromkeys(labels) if label in self.refs]

    def evidence(self, ids: tuple[str, ...], path: str) -> None:
        if any(i not in self.source.passages for i in ids):
            self.rejections.append(
                Rejection("unknown_passage", path, "Cites a passage the context did not list.")
            )

    def technology(self, node: ProposedNode, path: str) -> tuple[dict[str, Any] | None, str | None]:
        """The node's technology and component, checked against the catalog."""
        name = node.technology.strip().lower() if node.technology else None
        if node.technology and name != node.technology:
            self.normalizations.append(f"{path}: technology written in lower case")
        if node.component is None:
            return ({"name": name} if name else None), None
        try:
            spec = self.catalog.get(node.component)
        except ComponentNotFound:
            self.rejections.append(
                Rejection("component_not_in_catalog", f"{path}.component", "Not a component of the catalog.")
            )
            return None, None
        if spec.support_status is SupportStatus.DEPRECATED:
            detail = f"{spec.id} is deprecated; the catalog keeps it for existing architectures only."
            self.rejections.append(Rejection("component_deprecated", f"{path}.component", detail))
        if node.kind not in {k.value for k in spec.node_kinds}:
            detail = f"{spec.id} cannot be a {node.kind}."
            self.rejections.append(Rejection("component_kind_mismatch", f"{path}.component", detail))
        if name is not None and name != spec.technology:
            detail = f"{spec.id} is {spec.technology}, not the stated technology."
            self.rejections.append(Rejection("component_technology_mismatch", f"{path}.technology", detail))
        if name is None:
            self.normalizations.append(f"{path}: technology {spec.technology} taken from component {spec.id}")
            name = spec.technology
        return {"name": name}, spec.id

    def node(self, node: ProposedNode) -> dict[str, Any]:
        path = f"node:{node.id}"
        technology, component = self.technology(node, path)
        self.evidence(node.evidence, path)
        values = {k: list(v) if isinstance(v, tuple) else v for k, v in (node.configuration or {}).items()}
        document: dict[str, Any] = {
            "id": node.id,
            "kind": node.kind,
            "name": node.name,
            "description": node.rationale,
            "configuration": {"values": values},
            "requirement_refs": self.requirement_refs(node.requirement_refs, path),
            "provenance": self.provenance(node.confidence),
        }
        if technology is not None:
            document["technology"] = technology
        if component is not None:
            document["component"] = component
        return document

    def connection(self, connection: ProposedConnection) -> dict[str, Any]:
        path = f"connection:{connection.id}"
        self.evidence(connection.evidence, path)
        protocol = connection.protocol.strip().lower() if connection.protocol else None
        if connection.protocol and protocol != connection.protocol:
            self.normalizations.append(f"{path}: protocol written in lower case")
        document: dict[str, Any] = {
            "id": connection.id,
            "source_id": connection.source,
            "target_id": connection.target,
            "kind": connection.kind,
            "description": connection.rationale,
            "requirement_refs": self.requirement_refs(connection.requirement_refs, path),
            "provenance": self.provenance(connection.confidence),
        }
        if protocol:
            document["protocol"] = protocol
        return document

    def assumptions(self, proposal: Proposal) -> list[dict[str, Any]]:
        documents = []
        for claim in proposal.claims_of(Basis.ASSUMPTION):
            assumption_id = digest("assumption", claim.statement)
            path = f"assumption:{assumption_id}"
            self.evidence(claim.evidence, path)
            documents.append(
                {
                    "id": assumption_id,
                    "statement": claim.statement,
                    "requirement_refs": self.requirement_refs(claim.requirement_refs, path),
                    "provenance": self.provenance(claim.confidence),
                }
            )
        return documents

    def document(self) -> dict[str, Any]:
        proposal = self.source.proposal
        for index, claim in enumerate(proposal.claims):
            if claim.basis is not Basis.ASSUMPTION:
                self.evidence(claim.evidence, f"claim:{index}")
                self.requirement_refs(claim.requirement_refs, f"claim:{index}")
        for index, decision in enumerate(proposal.decisions):
            self.evidence(decision.evidence, f"decision:{index}")
            self.requirement_refs(decision.requirement_refs, f"decision:{index}")
        return {
            "schema_version": IR_SCHEMA_VERSION,
            "name": proposal.name,
            "description": proposal.summary,
            "nodes": [self.node(n) for n in proposal.nodes],
            "connections": [self.connection(c) for c in proposal.connections],
            "assumptions": self.assumptions(proposal),
            "provenance": self.provenance(proposal.confidence),
        }

    def cited_passages(self) -> tuple[EvidenceRef, ...]:
        proposal = self.source.proposal
        ids = {
            *(e for n in proposal.nodes for e in n.evidence),
            *(e for c in proposal.connections for e in c.evidence),
            *(e for d in proposal.decisions for e in d.evidence),
            *(e for c in proposal.claims for e in c.evidence),
        }
        return tuple(self.source.passages[i] for i in sorted(ids) if i in self.source.passages)

    def uncovered(self, ir: ArchitectureIR) -> tuple[str, ...]:
        """Labels of the set's requirements that no node, connection or assumption traces to."""
        refs = [
            *(r for n in ir.nodes for r in n.requirement_refs),
            *(r for c in ir.connections for r in c.requirement_refs),
            *(r for a in ir.assumptions for r in a.requirement_refs),
        ]
        traced = {str(ref.requirement_id) for ref in refs}
        labels = (label for label, ref in self.refs.items() if ref["requirement_id"] not in traced)
        return tuple(sorted(labels, key=_label_order))


def build_candidate(source: CandidateInput, catalog: ComponentCatalog) -> Construction:
    """The candidate, or every reason it cannot be one."""
    builder = _Builder(source, catalog)
    document = builder.document()
    try:
        ir = from_dict(document)
    except InvalidArchitecture as error:
        found = builder.rejections + [_rejection(v) for v in error.violations]
        return Construction(None, tuple(found[:MAX_REJECTIONS]))
    proposal = ArchitectureProposal(ir, RULE, source.proposal.summary, source.requirement_set_id)
    found = builder.rejections + [_rejection(v) for v in check_proposal(proposal, source.planning_input)]
    if found:
        return Construction(None, tuple(found[:MAX_REJECTIONS]))
    normalizations = tuple(dict.fromkeys(builder.normalizations))
    return Construction(Candidate(ir, normalizations, builder.cited_passages(), builder.uncovered(ir)))
