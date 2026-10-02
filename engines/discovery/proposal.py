"""The proposed architecture: the candidates of a discovery, with any review decisions, as a canonical
Architecture IR — plus, for every candidate, what became of it and why (``ProposedElement``).

Built by ``discovery-proposal@1``, a pure function of the stored candidates, the decisions and the
catalog, so a proposal is reproduced exactly from a stored result:

- **Nodes**: each component entity whose node kind is known — from the source, the catalog, or a
  reviewer. Its id is the entity's key; its component is the confident mapping or the reviewer's
  choice (an ambiguous mapping stays without one); its configuration the valid mapped values. A
  component of unknown kind **needs review**; a supporting resource (routing, configuration, volume,
  network) is **excluded** and kept as evidence; a rejected or ignored entity is excluded.
- **Connections**: each resolved relationship between two nodes, its kind established by the source
  or stated by a reviewer — otherwise it **needs review** (a connection kind is never guessed). A
  reference to a Kubernetes Service is followed to the one workload its selector resolves to (an
  inference, recorded with ``via``). Unresolved references, references to supporting resources and
  rejected ones are excluded with the reason. Its id is the relationship's id.
- **Provenance**: each node and connection carries its source (``kubernetes``, ``terraform``,
  ``file_import``) and location, never ``verified``; a field resting on an inference is marked
  ``inferred``, a field a reviewer stated is ``user_edit``.
- **Validation**: the IR's own structural checks (an element that fails is excluded and the problem
  reported as an error), and each node with a component evaluated against its specification by the
  constraint engine (violations and warnings as warnings, what cannot be evaluated as information).

Nothing here writes an architecture: accepting a proposal is a separate, explicit act.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from core.architecture_ir.component import Technology
from core.architecture_ir.configuration import NODE_PROPERTIES, Configuration, ValueType
from core.architecture_ir.edge import Connection
from core.architecture_ir.errors import InvalidArchitecture, Violation
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.provenance import Provenance, ProvenanceSource
from core.architecture_ir.values import MAX_NAME_LENGTH
from core.domain.components.evaluation import Outcome
from core.domain.components.repository import ComponentCatalog
from core.domain.discovery.findings import CandidateEntity, CandidateRelationship, PropertyMapping
from core.domain.discovery.results import Proposal, ProposedElement, ValidationIssue
from core.domain.discovery.runs import ReviewDecision
from core.domain.discovery.values import (
    IR_SOURCES,
    Decision,
    ElementKind,
    ElementStatus,
    EntityRole,
    MappingStatus,
    RelationshipStatus,
    Severity,
    SourceType,
    Verification,
)
from engines.constraints.service import DeterministicConstraintEngine

from .mapping import CATALOG_RULE

RULE = "discovery-proposal@1"
DEFAULT_NAME = "Discovered architecture"
DESCRIPTION = "Proposed from discovered source artifacts: their declared state, not the observed runtime."
CONFIDENT = frozenset({MappingStatus.EXACT_MATCH, MappingStatus.MAPPED})
IMAGE_SOURCES = frozenset({SourceType.KUBERNETES, SourceType.DOCKER_COMPOSE})  # a component from an image
CONSTRAINT_SEVERITY = {
    Outcome.VIOLATION: Severity.WARNING,
    Outcome.WARNING: Severity.WARNING,
    Outcome.CANNOT_EVALUATE: Severity.INFO,
}
INCLUDED, REVIEW, EXCLUDED = ElementStatus.INCLUDED, ElementStatus.NEEDS_REVIEW, ElementStatus.EXCLUDED
NODE, CONNECTION = ElementKind.NODE, ElementKind.CONNECTION
REVIEWER = Provenance(ProvenanceSource.USER_EDIT, verified=True)


def _issue(violation: Violation, element_id: str) -> ValidationIssue:
    return ValidationIssue(violation.rule, Severity.ERROR, violation.message, element_id, "architecture-ir")


def _provenance(source: SourceType, reference: str, *, inferred: bool = False) -> Provenance:
    return Provenance(
        IR_SOURCES[source], reference[:500], inferred=inferred, actor=f"discovery:{source.value}"
    )


def _ir_value(mapping: PropertyMapping) -> Any:
    """A mapped value in the IR's in-memory form: exact decimals as Decimal, lists as tuples."""
    spec = NODE_PROPERTIES[mapping.property]
    if spec.type is ValueType.DECIMAL and isinstance(mapping.value, str | int):
        return Decimal(str(mapping.value))
    if spec.type is ValueType.TEXT_LIST and isinstance(mapping.value, list):
        return tuple(mapping.value)
    return mapping.value


def _origin(fields: Mapping[str, Provenance], inferred: bool) -> Verification:
    if REVIEWER in fields.values():
        return Verification.USER_PROVIDED
    return Verification.INFERRED if inferred else Verification.OBSERVED


class _Builder:
    def __init__(self, catalog: ComponentCatalog, decisions: Mapping[str, ReviewDecision]) -> None:
        self.catalog = catalog
        self.components = {s.id: s for s in catalog.list()}
        self.decisions = decisions
        self.elements: list[ProposedElement] = []
        self.validation: list[ValidationIssue] = []
        self.nodes: dict[str, Node] = {}
        self.pending: set[str] = set()  # entities that become nodes once a reviewer states their kind

    def _out(self, kind: ElementKind, subject: str, status: ElementStatus, reason: str, **extra: Any) -> None:
        self.elements.append(ProposedElement(kind, subject, status, reason=reason, **extra))

    def _accepted(self, subject: str) -> ReviewDecision | None:
        decision = self.decisions.get(subject)
        return decision if decision is not None and decision.decision is Decision.ACCEPTED else None

    def _declined(self, subject: str) -> str | None:
        decision = self.decisions.get(subject)
        if decision is not None and decision.decision in {Decision.REJECTED, Decision.IGNORED}:
            return f"{decision.decision.value.capitalize()} by a reviewer."
        return None

    # --- nodes -----------------------------------------------------------------------------------

    def node(self, entity: CandidateEntity) -> None:
        evidence = entity.finding_ids
        if entity.role is not EntityRole.COMPONENT:
            reason = f"A supporting resource ({entity.role.value}): kept as evidence, not a node."
            self._out(NODE, entity.key, EXCLUDED, reason, finding_ids=evidence)
            return
        declined = self._declined(entity.key)
        if declined:
            self._out(NODE, entity.key, EXCLUDED, declined, finding_ids=evidence)
            return
        decision = self._accepted(entity.key)
        kind = entity.kind or (decision.node_kind if decision else None)
        if kind is None:
            reason = "Its node kind is not established by the source; a reviewer can state it."
            self._out(NODE, entity.key, REVIEW, reason, finding_ids=evidence)
            self.pending.add(entity.key)
            return
        fields: dict[str, Provenance] = {}
        inferences: list[str] = []
        if entity.kind is None:
            fields["kind"] = REVIEWER
        elif entity.kind_rule == CATALOG_RULE:
            fields["kind"] = _provenance(entity.source_type, entity.location.reference, inferred=True)
            inferences.append("its kind is the catalog component's only kind")
        component = self._component(entity, decision, fields, inferences)
        technology = self._technology(entity, fields)
        values, value_fields = self._configuration(entity)
        try:
            node = Node(
                entity.key, kind, entity.name[:MAX_NAME_LENGTH], technology=technology, component=component,
                configuration=Configuration(values), field_provenance=fields | value_fields,
                provenance=_provenance(entity.source_type, entity.location.reference),
            )  # fmt: skip
        except InvalidArchitecture as error:
            self.validation += [_issue(v, entity.key) for v in error.violations]
            reason = "The Architecture IR refuses it: see the validation issues."
            self._out(NODE, entity.key, EXCLUDED, reason, finding_ids=evidence)
            return
        self.nodes[node.id] = node
        note = ("; ".join(inferences).capitalize() + ".") if inferences else None
        origin = _origin(fields, bool(inferences))
        self.elements.append(
            ProposedElement(NODE, entity.key, INCLUDED, node.id, origin, note, finding_ids=evidence)
        )

    def _component(
        self,
        entity: CandidateEntity,
        decision: ReviewDecision | None,
        fields: dict[str, Provenance],
        inferences: list[str],
    ) -> str | None:
        chosen = decision.component_id if decision else None
        if chosen is not None:
            if chosen in self.components:
                fields["component"] = REVIEWER
                return chosen
            message = f"The chosen component {chosen} is not in the component catalog; none is set."
            self.validation.append(
                ValidationIssue("component_not_in_catalog", Severity.WARNING, message, entity.key, RULE)
            )
            return None
        mapping = entity.mapping
        if mapping.status not in CONFIDENT or mapping.component_id is None:
            return None  # ambiguous, unmapped or unsupported: no component until a reviewer chooses
        if mapping.status is MappingStatus.MAPPED and entity.source_type in IMAGE_SOURCES:
            fields["component"] = _provenance(entity.source_type, entity.location.reference, inferred=True)
            inferences.append("its component is inferred from its container image")
        return mapping.component_id

    def _technology(self, entity: CandidateEntity, fields: dict[str, Provenance]) -> Technology | None:
        if entity.technology is None:
            return None
        technology = None
        for version in (entity.technology_version, None):  # a version the IR cannot hold is left out
            try:
                technology = Technology(entity.technology, version)
                break
            except InvalidArchitecture:
                continue
        if technology is not None and entity.source_type in IMAGE_SOURCES:
            fields["technology"] = _provenance(entity.source_type, entity.location.reference, inferred=True)
        return technology

    def _configuration(self, entity: CandidateEntity) -> tuple[dict[str, Any], dict[str, Provenance]]:
        values: dict[str, Any] = {}
        fields: dict[str, Provenance] = {}
        for mapping in entity.configuration:
            if not mapping.valid or mapping.value is None:
                continue  # an invalid or conflicting value stays out (its problem is on the candidate)
            inferred = mapping.verification is not Verification.OBSERVED
            values[mapping.property] = _ir_value(mapping)
            fields[f"configuration.{mapping.property}"] = _provenance(
                entity.source_type, entity.location.reference, inferred=inferred
            )
        return values, fields

    # --- connections -----------------------------------------------------------------------------

    def connections(
        self, relationships: tuple[CandidateRelationship, ...], entities: Mapping[str, CandidateEntity]
    ) -> list[Connection]:
        routes = {
            r.source: r.target
            for r in relationships
            if r.target is not None
            and r.reference.startswith("selector:")
            and entities[r.source].role is EntityRole.ROUTING
        }
        seen: dict[tuple[str, str, str], str] = {}
        found = [self.connection(r, entities, routes, seen) for r in relationships]
        return [c for c in found if c is not None]

    def connection(  # noqa: PLR0911 - each way a relationship stays out is a return
        self,
        relationship: CandidateRelationship,
        entities: Mapping[str, CandidateEntity],
        routes: Mapping[str, str],
        seen: dict[tuple[str, str, str], str],
    ) -> Connection | None:
        subject, evidence = relationship.id, relationship.finding_ids
        source = entities[relationship.source]
        if source.role is EntityRole.ROUTING and relationship.reference.startswith("selector:"):
            reason = "A Service's selector: followed by the connections that reach the Service."
            self._out(CONNECTION, subject, EXCLUDED, reason, finding_ids=evidence)
            return None
        if relationship.status is RelationshipStatus.UNRESOLVED or relationship.target is None:
            self._out(
                CONNECTION, subject, EXCLUDED, relationship.reason or "Unresolved.", finding_ids=evidence
            )
            return None
        declined = self._declined(subject)
        if declined:
            self._out(CONNECTION, subject, EXCLUDED, declined, finding_ids=evidence)
            return None
        target, via = relationship.target, None
        if entities[target].role is EntityRole.ROUTING and target in routes:
            target, via = routes[target], target
        reached = entities[target]
        if reached.role is not EntityRole.COMPONENT:
            reason = f"It reaches a supporting resource ({reached.role.value}): kept as evidence."
            self._out(CONNECTION, subject, EXCLUDED, reason, via=via, finding_ids=evidence)
            return None
        missing = [k for k in (relationship.source, target) if k not in self.nodes]
        if missing:
            waiting = all(k in self.pending for k in missing)
            reason = (
                f"It waits for {missing[0]}, whose node kind needs review."
                if waiting
                else f"{missing[0]} is not a node of the proposal."
            )
            status = REVIEW if waiting else EXCLUDED
            self._out(CONNECTION, subject, status, reason, via=via, finding_ids=evidence)
            return None
        decision = self._accepted(subject)
        kind = relationship.kind or (decision.connection_kind if decision else None)
        if kind is None:
            reason = "Its connection kind is not established by the source; a reviewer can state it."
            self._out(CONNECTION, subject, REVIEW, reason, via=via, finding_ids=evidence)
            return None
        signature = (relationship.source, target, kind.value)
        if signature in seen:
            reason = f"The same connection is proposed from {seen[signature]}."
            self._out(CONNECTION, subject, EXCLUDED, reason, via=via, finding_ids=evidence)
            return None
        seen[signature] = subject
        fields = {"kind": REVIEWER} if relationship.kind is None else {}
        provenance = _provenance(
            source.source_type, relationship.location.reference, inferred=via is not None
        )
        connection = Connection(
            subject, relationship.source, target, kind, provenance=provenance, field_provenance=fields
        )
        note = f"Followed through {via}, whose selector names the target." if via else relationship.reason
        self.elements.append(
            ProposedElement(
                CONNECTION, subject, INCLUDED, subject, _origin(fields, via is not None), note, via, evidence
            )
        )
        return connection

    # --- validation ------------------------------------------------------------------------------

    def constraints(self, architecture: ArchitectureIR) -> None:
        engine = DeterministicConstraintEngine()
        for node in architecture.nodes:
            if node.component is None or node.component not in self.components:
                continue
            evaluation = engine.evaluate_node(node, self.components[node.component], self.catalog)
            for finding in evaluation.findings:
                severity = CONSTRAINT_SEVERITY.get(finding.outcome)
                if severity is None:
                    continue
                rule = f"{finding.specification or finding.component}:{finding.check}"[:128]
                code = f"constraint_{finding.outcome.value}"
                self.validation.append(
                    ValidationIssue(code, severity, finding.explanation[:2000], node.id, rule)
                )


def propose(
    entities: tuple[CandidateEntity, ...],
    relationships: tuple[CandidateRelationship, ...],
    catalog: ComponentCatalog,
    decisions: Mapping[str, ReviewDecision] | None = None,
    name: str | None = None,
) -> Proposal:
    """The proposed architecture of these candidates under these decisions (the latest per subject)."""
    builder = _Builder(catalog, decisions or {})
    for entity in sorted(entities, key=lambda e: e.key):
        builder.node(entity)
    by_key = {e.key: e for e in entities}
    connections = builder.connections(tuple(sorted(relationships, key=lambda r: r.id)), by_key)
    architecture: ArchitectureIR | None = None
    if builder.nodes:
        try:
            architecture = ArchitectureIR(
                (name or DEFAULT_NAME)[:MAX_NAME_LENGTH],
                DESCRIPTION,
                nodes=tuple(builder.nodes.values()),
                connections=tuple(connections),
            )
        except InvalidArchitecture as error:
            builder.validation += [_issue(v, v.element_id or "architecture") for v in error.violations]
        else:
            builder.constraints(architecture)
    return Proposal(architecture, tuple(builder.elements), tuple(builder.validation))


def latest(decisions: tuple[ReviewDecision, ...]) -> dict[str, ReviewDecision]:
    """The latest decision per subject (decisions are history)."""
    return {d.subject: d for d in decisions}
