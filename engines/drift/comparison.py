"""Structural and configuration differences between a baseline revision and a discovery run, from the
identity matching (rule ``drift-differences@1``). Each difference names the compared elements and
property, both values (a secret's never), its evidence and source locations, and the baseline
element's provenance; whether it is confirmed is decided afterwards, with the coverage
(``classification``).

**Matched nodes** are compared through the IR's own diff — the baseline node against the same node
holding what the source declares — so values are compared typed and canonical (``0.50`` equals
``0.5``), never as strings, with the diff's categories:

- ``kind``: only when the source establishes one (a kind the catalog implies is marked inferred);
- ``component``: only when the mapping is confident (an image-based mapping is marked inferred);
- ``technology``: only what the source states — an unstated version is unknown, not removed;
- ``configuration``: only the properties the entity's source type can declare (Kubernetes and Compose:
  replicas, CPU and memory requests and limits, health check; Terraform: instance class, storage;
  Architecture JSON: every property). A declared value that differs is a change; a value the baseline
  lacks is added; a baseline value no longer declared is reported as such, never filled with a
  default; a declared value discovery found invalid or conflicting is unresolved. Properties the
  source type cannot express are not compared at all.

**Unmatched** baseline nodes and connections are removal candidates — never removals yet; unmatched
discovered components and relationships are additions; ambiguous matches are unresolved differences,
with their candidates. A connection's kind is compared only when the source states one.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from core.architecture_ir.component import Technology
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.diff import RESOURCE_PROPERTIES, diff, is_secret_path
from core.architecture_ir.edge import Connection
from core.architecture_ir.errors import InvalidArchitecture
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.discovery.findings import CandidateEntity, CandidateRelationship
from core.domain.discovery.results import CONFIDENT, DiscoveryResult
from core.domain.discovery.values import MappingStatus, SourceType
from core.domain.drift.values import ElementType, FindingType, MatchMethod
from engines.discovery.mapping import CATALOG_RULE, RESOURCES, TERRAFORM_PROPERTIES
from engines.discovery.proposal import ir_value

from .matching import ConnectionMatch, Matching, NodeMatch
from .sources import discovered_from

RULE = "drift-differences@1"
F, E, M = FindingType, ElementType, MatchMethod
WORKLOAD = frozenset({"replicas", "health_check", *RESOURCES.values()})
DECLARABLE: dict[SourceType, frozenset[str] | None] = {  # None: every property
    SourceType.KUBERNETES: WORKLOAD,
    SourceType.DOCKER_COMPOSE: WORKLOAD,
    SourceType.TERRAFORM_JSON: frozenset(TERRAFORM_PROPERTIES.values()),
    SourceType.ARCHITECTURE_JSON: None,
}
IMAGE_SOURCES = frozenset({SourceType.KUBERNETES, SourceType.DOCKER_COMPOSE})
COMPARED = frozenset({"kind", "technology", "configuration", "resources"})
UNSET = "Not declared is not proof it is unset."


@dataclass(frozen=True, slots=True)
class Difference:
    """A difference before classification: what is compared and what it rests on."""

    type: FindingType
    element: ElementType
    subject: str
    explanation: str
    path: str | None = None
    baseline_id: str | None = None
    discovered_key: str | None = None
    match: MatchMethod | None = None
    baseline_value: Any = None
    discovered_value: Any = None
    redacted: bool = False
    evidence: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    baseline_reference: str | None = None
    source_type: SourceType | None = None  # the discovered side's, else where the baseline came from
    baseline_artifact: str | None = None  # where the baseline element was discovered, when it was
    absent: bool = False  # a baseline element without a match: a removal candidate
    inferred: bool = False  # the discovered side rests on an inference
    candidates: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def shown_values(path: str, before: Any, after: Any) -> dict[str, Any]:
    """Both values — or, for a secret-looking property, that it changed and nothing more."""
    if is_secret_path(path):
        return {"baseline_value": None, "discovered_value": None, "redacted": True}
    return {"baseline_value": before, "discovered_value": after}


def _type(path: str) -> FindingType:
    head, _, rest = path.partition(".")
    if head == "configuration":
        return F.RESOURCE_CHANGED if rest in RESOURCE_PROPERTIES else F.CONFIGURATION_CHANGED
    return F.MAPPING_CHANGED if head == "component" else F.COMPONENT_MODIFIED


def _technology(node: Node, entity: CandidateEntity) -> Technology | None:
    if entity.technology is None:
        return node.technology
    version = entity.technology_version or (node.technology.version if node.technology else None)
    try:
        return Technology(entity.technology, version)
    except InvalidArchitecture:
        return node.technology


def _declared(node: Node, entity: CandidateEntity) -> tuple[Node, dict[str, str], list[Difference]]:
    """The baseline node holding what the source declares — only what it can and does state — with
    each compared property's evidence, and what discovery left unresolved."""
    allowed = DECLARABLE[entity.source_type]
    values = dict(node.configuration.values)
    evidence: dict[str, str] = {}
    unresolved: list[Difference] = []
    stated = {m.property: m for m in entity.configuration}
    for name in sorted(set(values) | set(stated)):
        if allowed is not None and name not in allowed:
            continue  # the source type cannot express it: not compared
        mapping = stated.get(name)
        path = f"configuration.{name}"
        if mapping is None:
            values.pop(name, None)  # no longer declared: compared as absent
        elif not mapping.valid or mapping.value is None:
            note = mapping.problem or "The declared value is not usable."
            unresolved.append(
                Difference(
                    F.UNRESOLVED_DIFFERENCE, E.NODE, f"node:{node.id}", f"Discovery could not read {path}.",
                    path, node.id, entity.key, evidence=(mapping.finding_id,), source_type=entity.source_type,
                    notes=(note,),
                )
            )  # fmt: skip
        else:
            values[name] = ir_value(mapping)
            evidence[path] = mapping.finding_id
    confident = entity.mapping.status in CONFIDENT and entity.mapping.component_id is not None
    declared = replace(
        node,
        kind=entity.kind or node.kind,  # an unstated kind is not compared
        component=entity.mapping.component_id if confident else node.component,
        technology=_technology(node, entity),
        configuration=Configuration(values),
    )
    return declared, evidence, unresolved


def _stripped(node: Node) -> Node:
    return replace(node, provenance=None, field_provenance={})


def _node_differences(node: Node, entity: CandidateEntity, method: MatchMethod) -> list[Difference]:
    subject = f"node:{node.id}"
    try:
        declared, evidence, found = _declared(_stripped(node), entity)
    except InvalidArchitecture as error:
        note = f"What the source declares does not fit the baseline node: {error.violations[0].message}"
        reason = "The discovered configuration cannot be compared with this node."
        return [
            Difference(
                F.UNRESOLVED_DIFFERENCE, E.NODE, subject, reason, None, node.id, entity.key, method,
                evidence=entity.finding_ids, source_type=entity.source_type, notes=(note,),
            )
        ]  # fmt: skip
    inferred_kind = entity.kind_rule == CATALOG_RULE
    inferred_component = entity.mapping.status is MappingStatus.MAPPED and entity.source_type in IMAGE_SOURCES
    before, after = ArchitectureIR("b", nodes=(_stripped(node),)), ArchitectureIR("b", nodes=(declared,))
    origin = discovered_from(node)
    for change in diff(before, after).nodes:
        for field in change.fields:
            if field.category not in COMPARED:
                continue
            path = field.field
            inferred = (path == "kind" and inferred_kind) or (path == "component" and inferred_component)
            removed = path.startswith("configuration.") and field.after is None
            explanation = (
                f"{path} is no longer declared." if removed else f"{path} differs from the baseline."
            )
            found.append(
                Difference(
                    _type(path), E.NODE, subject, explanation, path, node.id, entity.key, method,
                    **shown_values(path, field.before, field.after),
                    evidence=(evidence[path],) if path in evidence else entity.finding_ids,
                    locations=(entity.location.reference,),
                    baseline_reference=origin.reference if origin else None,
                    source_type=entity.source_type, inferred=inferred, notes=(UNSET,) if removed else (),
                )
            )  # fmt: skip
    return found


def _missing_node(node: Node, match: NodeMatch) -> Difference:
    origin = discovered_from(node)
    return Difference(
        F.COMPONENT_REMOVED, E.NODE, f"node:{node.id}", "No discovered entity matches this node.",
        None, node.id, None, M.UNMATCHED,
        baseline_reference=origin.reference if origin else None,
        baseline_artifact=origin.artifact if origin else None,
        source_type=origin.source_type if origin else None,
        absent=True, notes=(match.reason,) if match.reason else (),
    )  # fmt: skip


def _added_node(entity: CandidateEntity) -> Difference:
    notes = () if entity.kind else ("Its node kind is not established by the source.",)
    return Difference(
        F.COMPONENT_ADDED, E.NODE, f"node:{entity.key}", "Declared in the sources; not in the baseline.",
        None, None, entity.key, M.UNMATCHED,
        discovered_value=entity.kind.value if entity.kind else None,
        evidence=entity.finding_ids, locations=(entity.location.reference,),
        source_type=entity.source_type, notes=notes,
    )  # fmt: skip


def _nodes(
    ir: ArchitectureIR, entities: Mapping[str, CandidateEntity], matches: tuple[NodeMatch, ...]
) -> list[Difference]:
    by_id = {n.id: n for n in ir.nodes}
    found: list[Difference] = []
    for match in matches:
        node = by_id.get(match.baseline_id) if match.baseline_id else None
        entity = entities.get(match.discovered_key) if match.discovered_key else None
        if match.matched and node is not None and entity is not None:
            found += _node_differences(node, entity, match.method)
        elif match.method is M.AMBIGUOUS:
            if match.baseline_id is None and match.discovered_key in by_id:
                continue  # the baseline node with this id states the same ambiguity, with its candidates
            found.append(
                Difference(
                    F.UNRESOLVED_DIFFERENCE, E.NODE, f"node:{match.baseline_id or match.discovered_key}",
                    match.reason or "The identity is ambiguous.", None, match.baseline_id,
                    match.discovered_key, M.AMBIGUOUS, candidates=match.candidates,
                    source_type=entity.source_type if entity else None,
                    evidence=entity.finding_ids if entity else (),
                    notes=("Confirm the identity to compare it.",),
                )
            )  # fmt: skip
        elif node is not None:
            found.append(_missing_node(node, match))
        elif entity is not None:
            found.append(_added_node(entity))
    return found


def _kind_of(group: list[CandidateRelationship]) -> Any:
    stated = sorted({r.kind.value for r in group if r.kind is not None})
    return stated[0] if len(stated) == 1 else (stated or None)


def _connection(
    match: ConnectionMatch,
    connection: Connection | None,
    group: list[CandidateRelationship],
    entities: Mapping[str, CandidateEntity],
    matched_nodes: frozenset[str],
) -> Difference | None:
    evidence = tuple(f for r in group for f in r.finding_ids)
    locations = tuple(r.location.reference for r in group)
    source = entities[group[0].source].source_type if group else None
    if connection is not None and match.matched:
        stated = sorted({r.kind.value for r in group if r.kind is not None})
        if not stated or connection.kind.value in stated:
            return None  # the same kind, or none stated: nothing to compare
        return Difference(
            F.CONNECTION_MODIFIED, E.CONNECTION, f"connection:{connection.id}",
            "The source states another connection kind.", "kind", connection.id, match.relationships[0],
            match.method, connection.kind.value, _kind_of(group), evidence=evidence, locations=locations,
            source_type=source,
        )  # fmt: skip
    if match.method is M.AMBIGUOUS:
        subject = f"connection:{match.baseline_id or f'{match.source}->{match.target}'}"
        return Difference(
            F.UNRESOLVED_DIFFERENCE, E.CONNECTION, subject, match.reason or "The identity is ambiguous.",
            None, match.baseline_id, None, M.AMBIGUOUS, candidates=match.candidates, source_type=source,
            notes=("State the connection kind in the sources, or review it.",),
        )  # fmt: skip
    if connection is not None:
        origin = discovered_from(connection)
        ends = (connection.source_id, connection.target_id)
        notes = tuple(
            f"Its endpoint {end} has no match in this run." for end in ends if end not in matched_nodes
        )
        return Difference(
            F.CONNECTION_REMOVED, E.CONNECTION, f"connection:{connection.id}",
            "No discovered reference matches this connection.", None, connection.id, None, M.UNMATCHED,
            baseline_value=connection.kind.value,
            baseline_reference=origin.reference if origin else None,
            baseline_artifact=origin.artifact if origin else None,
            source_type=origin.source_type if origin else None, absent=True, notes=notes,
        )  # fmt: skip
    if not group:
        return None
    kind = _kind_of(group)
    return Difference(
        F.CONNECTION_ADDED, E.CONNECTION, f"connection:{match.source}->{match.target}",
        "Referenced in the sources; not in the baseline.", None, None, match.relationships[0], M.UNMATCHED,
        discovered_value=kind, evidence=evidence, locations=locations, source_type=source,
        notes=() if kind else ("The source states no connection kind.",),
    )  # fmt: skip


def differences(ir: ArchitectureIR, result: DiscoveryResult, matching: Matching) -> tuple[Difference, ...]:
    """Every structural and configuration difference the matching allows, in canonical order."""
    entities = {e.key: e for e in result.entities}
    relationships = {r.id: r for r in result.relationships}
    found = _nodes(ir, entities, matching.nodes)
    by_id = {c.id: c for c in ir.connections}
    matched_nodes = frozenset(m.baseline_id for m in matching.nodes if m.matched and m.baseline_id)
    for match in matching.connections:
        connection = by_id.get(match.baseline_id) if match.baseline_id else None
        group = [relationships[i] for i in match.relationships if i in relationships]
        difference = _connection(match, connection, group, entities, matched_nodes)
        if difference is not None:
            found.append(difference)
    return tuple(sorted(found, key=lambda d: (d.subject, d.path or "", d.type.value)))
