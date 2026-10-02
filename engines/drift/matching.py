"""Identity matching between a baseline revision's elements and a discovery run's entities and
relationships (rule ``drift-identity@1``) — deterministic, never by name.

**Nodes** are matched to discovered component entities, in order:

1. ``same_id``: the node id is the entity's discovery key — a stable source identifier (format,
   namespace or project, resource type and name), which is how an accepted discovery names its nodes;
2. ``confirmed_mapping``: a person confirmed the node and the entity are the same (a rename);
3. otherwise unmatched — unless the baseline node was discovered at a place (artifact, document,
   pointer) where an unmatched entity now stands: that is a candidate, not a match (``ambiguous``),
   until a person confirms it.

A discovery key claimed by several baseline nodes (an id and a mapping, or two mappings) is
``ambiguous`` for each of them: nothing is merged. A mapping naming an entity the run does not hold
matches nothing, with the reason.

**Connections** are matched to discovered relationships whose endpoints both matched baseline nodes —
a reference to a Kubernetes Service followed to the workload it selects, as the proposal does: by the
relationship id (``same_id``), else by endpoints (``signature``); several baseline connections between
the same endpoints are told apart only by a kind the relationship states — otherwise ``ambiguous``.
Relationships to supporting resources (configuration, volumes, networks) are not connections.

Excluded ids and keys are left out of both sides.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.discovery.findings import CandidateEntity, CandidateRelationship
from core.domain.discovery.results import DiscoveryResult
from core.domain.discovery.values import EntityRole, RelationshipStatus
from core.domain.drift.values import MatchMethod
from engines.discovery.proposal import service_routes

from .sources import discovered_from

RULE = "drift-identity@1"
M = MatchMethod
NEAR = "An entity now stands where this node was discovered: confirm the identity to compare them."
FAR = "It stands where a baseline node was discovered: confirm the identity to compare them."
UNSTATED = "Several baseline connections join these endpoints and the source states no kind."


@dataclass(frozen=True, slots=True)
class NodeMatch:
    baseline_id: str | None  # None: a discovered entity no baseline node matched
    discovered_key: str | None  # None: a baseline node no entity matched
    method: MatchMethod
    candidates: tuple[str, ...] = ()  # an ambiguous match's other side
    reason: str | None = None

    @property
    def matched(self) -> bool:
        return self.method in {M.SAME_ID, M.CONFIRMED_MAPPING}


@dataclass(frozen=True, slots=True)
class ConnectionMatch:
    baseline_id: str | None  # None: discovered relationships no baseline connection matched
    relationships: tuple[str, ...]  # the discovered relationship ids
    method: MatchMethod
    source: str  # the endpoints: baseline node ids, or discovery keys where not matched
    target: str
    candidates: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def matched(self) -> bool:
        return self.method in {M.SAME_ID, M.SIGNATURE}


@dataclass(frozen=True)
class Matching:
    nodes: tuple[NodeMatch, ...]
    connections: tuple[ConnectionMatch, ...]
    excluded: tuple[str, ...] = ()

    def by_baseline(self) -> dict[str, NodeMatch]:
        return {m.baseline_id: m for m in self.nodes if m.baseline_id is not None}

    def baseline_of(self) -> dict[str, str]:
        """Discovery key -> the baseline node id it matched."""
        return _baseline_of(self.nodes)


def _baseline_of(nodes: tuple[NodeMatch, ...]) -> dict[str, str]:
    return {
        m.discovered_key: m.baseline_id for m in nodes if m.matched and m.discovered_key and m.baseline_id
    }


def _claims(
    nodes: list[Node], entities: Mapping[str, CandidateEntity], mappings: Mapping[str, str]
) -> tuple[dict[str, list[tuple[str, MatchMethod]]], dict[str, NodeMatch]]:
    claims: dict[str, list[tuple[str, MatchMethod]]] = defaultdict(list)
    unmatched: dict[str, NodeMatch] = {}
    for node in nodes:
        mapped = mappings.get(node.id)
        if node.id in entities:
            claims[node.id].append((node.id, M.SAME_ID))
        elif mapped is not None and mapped in entities:
            claims[mapped].append((node.id, M.CONFIRMED_MAPPING))
        elif mapped is not None:
            reason = f"The confirmed mapping names {mapped}, which this run does not hold."
            unmatched[node.id] = NodeMatch(node.id, None, M.UNMATCHED, reason=reason)
        else:
            unmatched[node.id] = NodeMatch(node.id, None, M.UNMATCHED)
    return claims, unmatched


def match_nodes(
    ir: ArchitectureIR,
    result: DiscoveryResult,
    mappings: Mapping[str, str],
    excluded: frozenset[str] = frozenset(),
) -> tuple[NodeMatch, ...]:
    entities = {e.key: e for e in result.entities if e.role is EntityRole.COMPONENT and e.key not in excluded}
    nodes = [n for n in ir.nodes if n.id not in excluded]
    claims, unmatched = _claims(nodes, entities, mappings)
    found: list[NodeMatch] = []
    for key_, claimants in sorted(claims.items()):
        if len(claimants) == 1:
            found.append(NodeMatch(claimants[0][0], key_, claimants[0][1]))
            continue
        ids = tuple(sorted(b for b, _ in claimants))
        reason = f"{key_} is claimed by several baseline nodes ({', '.join(ids)}): confirm which one."
        found += [NodeMatch(b, None, M.AMBIGUOUS, (key_,), reason) for b in ids]
        found.append(NodeMatch(None, key_, M.AMBIGUOUS, ids, reason))
    free = {k: e for k, e in entities.items() if k not in claims}
    at_place: dict[str, list[str]] = defaultdict(list)
    for entity in free.values():
        at_place[entity.location.reference].append(entity.key)
    candidates_of: dict[str, list[str]] = defaultdict(list)  # discovery key -> baseline ids
    by_id = {n.id: n for n in nodes}
    for baseline_id, match in sorted(unmatched.items()):
        origin = discovered_from(by_id[baseline_id])
        nearby = sorted(at_place.get(origin.reference, ())) if origin else []
        if nearby and match.reason is None:
            found.append(NodeMatch(baseline_id, None, M.AMBIGUOUS, tuple(nearby), NEAR))
            for key_ in nearby:
                candidates_of[key_].append(baseline_id)
        else:
            found.append(match)
    for key_ in sorted(free):
        if key_ in candidates_of:
            found.append(NodeMatch(None, key_, M.AMBIGUOUS, tuple(sorted(candidates_of[key_])), FAR))
        else:
            found.append(NodeMatch(None, key_, M.UNMATCHED))
    return tuple(sorted(found, key=lambda m: (m.baseline_id or "", m.discovered_key or "")))


def _endpoints(
    relationships: tuple[CandidateRelationship, ...], entities: Mapping[str, CandidateEntity]
) -> list[tuple[CandidateRelationship, str, str]]:
    """Resolved relationships between components, a Service reference followed to its workload."""
    routes = service_routes(relationships, entities)
    found: list[tuple[CandidateRelationship, str, str]] = []
    for relationship in relationships:
        if relationship.status is not RelationshipStatus.RESOLVED or relationship.target is None:
            continue
        target = relationship.target
        if entities[target].role is EntityRole.ROUTING:
            if target not in routes:
                continue
            target = routes[target]
        if (
            entities[relationship.source].role is EntityRole.COMPONENT
            and entities[target].role is EntityRole.COMPONENT
        ):
            found.append((relationship, relationship.source, target))
    return found


def _baseline_side(
    connections: list[Connection],
    by_pair: Mapping[tuple[str, str], list[CandidateRelationship]],
    pair_of: Mapping[str, tuple[str, str]],
) -> tuple[list[ConnectionMatch], set[str]]:
    found: list[ConnectionMatch] = []
    used: set[str] = set()
    rest: list[Connection] = []
    for connection in connections:  # a relationship id kept as the connection id: the same reference
        pair = (connection.source_id, connection.target_id)
        if pair_of.get(connection.id) == pair:
            found.append(ConnectionMatch(connection.id, (connection.id,), M.SAME_ID, *pair))
            used.add(connection.id)
        else:
            rest.append(connection)
    siblings: dict[tuple[str, str], list[Connection]] = defaultdict(list)
    for connection in rest:
        siblings[(connection.source_id, connection.target_id)].append(connection)
    for connection in rest:
        pair = (connection.source_id, connection.target_id)
        group = [r for r in by_pair.get(pair, []) if r.id not in used]
        if len(siblings[pair]) == 1:
            chosen = tuple(sorted(r.id for r in group))
        else:
            chosen = tuple(sorted(r.id for r in group if r.kind is connection.kind))
        if chosen:
            found.append(ConnectionMatch(connection.id, chosen, M.SIGNATURE, *pair))
            used.update(chosen)
            continue
        unknown = tuple(sorted(r.id for r in group if r.kind is None))
        if unknown:
            found.append(ConnectionMatch(connection.id, (), M.AMBIGUOUS, *pair, unknown, UNSTATED))
        else:
            found.append(ConnectionMatch(connection.id, (), M.UNMATCHED, *pair))
    return found, used


def match_connections(
    ir: ArchitectureIR,
    result: DiscoveryResult,
    nodes: tuple[NodeMatch, ...],
    excluded: frozenset[str] = frozenset(),
) -> tuple[ConnectionMatch, ...]:
    entities = {e.key: e for e in result.entities}
    baseline_of = _baseline_of(nodes)
    relationships = tuple(r for r in result.relationships if r.id not in excluded)
    by_pair: dict[tuple[str, str], list[CandidateRelationship]] = defaultdict(list)
    pair_of: dict[str, tuple[str, str]] = {}
    unmatched_pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for relationship, source, target in _endpoints(relationships, entities):
        if source in baseline_of and target in baseline_of:
            pair = (baseline_of[source], baseline_of[target])
            by_pair[pair].append(relationship)
            pair_of[relationship.id] = pair
        else:
            ends = (baseline_of.get(source, source), baseline_of.get(target, target))
            unmatched_pairs[ends].append(relationship.id)
    connections = [c for c in ir.connections if c.id not in excluded]
    found, used = _baseline_side(connections, by_pair, pair_of)
    claimed = {i for m in found for i in m.candidates}
    for pair, group in sorted(by_pair.items()):
        rest = tuple(sorted(r.id for r in group if r.id not in used and r.id not in claimed))
        if rest:
            found.append(ConnectionMatch(None, rest, M.UNMATCHED, *pair))
    for pair, ids in sorted(unmatched_pairs.items()):
        found.append(ConnectionMatch(None, tuple(sorted(ids)), M.UNMATCHED, *pair))
    return tuple(sorted(found, key=lambda m: (m.source, m.target, m.baseline_id or "", m.relationships)))


def match(
    ir: ArchitectureIR, result: DiscoveryResult, mappings: Mapping[str, str], excluded: tuple[str, ...] = ()
) -> Matching:
    """Every baseline node and connection matched to the run's entities and relationships — or why not."""
    left_out = frozenset(excluded)
    nodes = match_nodes(ir, result, mappings, left_out)
    return Matching(nodes, match_connections(ir, result, nodes, left_out), tuple(sorted(left_out)))
