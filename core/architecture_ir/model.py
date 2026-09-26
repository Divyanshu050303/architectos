"""The Architecture IR: one complete, structurally valid architecture state.

The canonical representation every ArchitectOS engine reads (capacity, constraints, validation,
reliability, security, cost, observability, simulation) and every producer writes (the
Architecture Engine, discovery, imports, people's edits). It is immutable, and it depends on no
LLM, HTTP or storage format.

Graph rules (explicit, so engines can rely on them):

- ids are unique across nodes, connections and assumptions (one id names one element);
- every connection joins two existing, different nodes; neither may be a boundary;
- the same connection (source, target, kind, protocol) is not stated twice; different kinds or
  protocols between the same two nodes are different connections;
- cycles are allowed (services call each other, replication goes both ways);
- a node's parent is an existing boundary, and containment has no cycles;
- assumptions and decisions refer only to existing nodes and connections;
- an empty architecture (no nodes) is valid: it is where every design starts.

Structural validity says nothing about whether the architecture is any good; that is the
engines' job.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from .component import NodeKind
from .edge import Connection
from .errors import ElementType, Violation, raise_if
from .node import Node
from .provenance import Provenance
from .traceability import Assumption, DecisionRef, RequirementRef, normalized_refs, refs_problems
from .values import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    clean_block,
    clean_line,
    frozen,
    metadata_problems,
    text_problems,
)
from .versioning import IR_SCHEMA_VERSION

MAX_NODES = 1000
MAX_CONNECTIONS = 5000
MAX_ASSUMPTIONS = 500
MAX_DECISIONS = 500


def _sorted_by_id(items: object, kind: type) -> object:
    if isinstance(items, tuple) and all(isinstance(i, kind) for i in items):
        return tuple(sorted(items, key=lambda i: i.id))
    return items


@dataclass(frozen=True, slots=True)
class ArchitectureIR:
    name: str
    description: str | None = None
    nodes: tuple[Node, ...] = ()
    connections: tuple[Connection, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    decisions: tuple[DecisionRef, ...] = ()
    requirement_refs: tuple[RequirementRef, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)
    provenance: Provenance | None = None  # applies to every element without its own
    schema_version: int = IR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.name, str):
            object.__setattr__(self, "name", clean_line(self.name))
        if isinstance(self.description, str):
            object.__setattr__(self, "description", clean_block(self.description) or None)
        object.__setattr__(self, "nodes", _sorted_by_id(self.nodes, Node))
        object.__setattr__(self, "connections", _sorted_by_id(self.connections, Connection))
        object.__setattr__(self, "assumptions", _sorted_by_id(self.assumptions, Assumption))
        if isinstance(self.decisions, tuple) and all(isinstance(d, DecisionRef) for d in self.decisions):
            decisions = tuple(sorted(self.decisions, key=lambda d: str(d.decision_id)))
            object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "requirement_refs", normalized_refs(self.requirement_refs))
        if isinstance(self.metadata, Mapping):
            object.__setattr__(self, "metadata", frozen(self.metadata))
        raise_if(self.problems())

    # --- reading ---------------------------------------------------------------------------------

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def connection(self, connection_id: str) -> Connection | None:
        return next((c for c in self.connections if c.id == connection_id), None)

    def nodes_of_kind(self, *kinds: NodeKind) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes if n.kind in kinds)

    def element_ids(self) -> frozenset[str]:
        """Ids that assumptions and decisions may refer to: nodes and connections."""
        return frozenset(n.id for n in self.nodes) | frozenset(c.id for c in self.connections)

    # --- rules -----------------------------------------------------------------------------------

    def problems(self) -> list[Violation]:
        problems = self._own_problems()
        shapes = self._shape_problems()
        if shapes:  # the graph rules need well-typed collections
            return problems + shapes
        return [
            *problems,
            *self._duplicate_id_problems(),
            *self._containment_problems(),
            *self._connection_problems(),
            *self._reference_problems(),
        ]

    def _own_problems(self) -> list[Violation]:
        problems = text_problems(self.name, "name", MAX_NAME_LENGTH, required=True)
        problems += text_problems(
            self.description, "description", MAX_DESCRIPTION_LENGTH, required=False, block=True
        )
        if isinstance(self.schema_version, bool) or self.schema_version != IR_SCHEMA_VERSION:
            problems.append(
                Violation(
                    "unsupported_schema_version",
                    f"Schema version {self.schema_version!r} is not supported here; "
                    f"this is version {IR_SCHEMA_VERSION}.",
                    "schema_version",
                )
            )
        problems += refs_problems(self.requirement_refs)
        problems += metadata_problems(self.metadata)
        if self.provenance is not None and not isinstance(self.provenance, Provenance):
            problems.append(
                Violation("invalid_value", "provenance must be a provenance record.", "provenance")
            )
        return [p.within(ElementType.ARCHITECTURE, None) for p in problems]

    def _shape_problems(self) -> list[Violation]:
        problems: list[Violation] = []
        collections: list[tuple[str, object, type, int]] = [
            ("nodes", self.nodes, Node, MAX_NODES),
            ("connections", self.connections, Connection, MAX_CONNECTIONS),
            ("assumptions", self.assumptions, Assumption, MAX_ASSUMPTIONS),
            ("decisions", self.decisions, DecisionRef, MAX_DECISIONS),
        ]
        for name, items, kind, limit in collections:
            if not isinstance(items, tuple) or not all(isinstance(i, kind) for i in items):
                problems.append(
                    Violation("invalid_value", f"{name} must be a list of {kind.__name__}.", name)
                )
            elif len(items) > limit:
                problems.append(Violation("too_many", f"An architecture has at most {limit} {name}.", name))
        return [p.within(ElementType.ARCHITECTURE, None) for p in problems]

    def _duplicate_id_problems(self) -> list[Violation]:
        elements: list[tuple[ElementType, str]] = [
            *((ElementType.NODE, n.id) for n in self.nodes),
            *((ElementType.CONNECTION, c.id) for c in self.connections),
            *((ElementType.ASSUMPTION, a.id) for a in self.assumptions),
        ]
        counts = Counter(element_id for _, element_id in elements)
        return [
            Violation(
                "duplicate_id",
                f"The id {element_id!r} is used by more than one element; ids must be unique.",
                "id",
                element,
                element_id,
            )
            for element, element_id in elements
            if counts[element_id] > 1
        ]

    def _containment_problems(self) -> list[Violation]:
        nodes = {n.id: n for n in self.nodes}
        problems: list[Violation] = []
        for node in self.nodes:
            if node.parent_id is None:
                continue
            parent = nodes.get(node.parent_id)
            if parent is None:
                rule, message = (
                    "dangling_reference",
                    f"The parent {node.parent_id!r} is not a node of this architecture.",
                )
            elif parent.kind is not NodeKind.BOUNDARY:
                rule, message = (
                    "invalid_parent",
                    f"Only a boundary can contain nodes; {parent.id!r} is a {parent.kind}.",
                )
            elif _in_containment_cycle(node, nodes):
                rule, message = "containment_cycle", "This node is (indirectly) its own parent."
            else:
                continue
            problems.append(Violation(rule, message, "parent_id", ElementType.NODE, node.id))
        return problems

    def _connection_problems(self) -> list[Violation]:
        nodes = {n.id: n for n in self.nodes}
        seen: dict[tuple[str, str, str, str | None], str] = {}
        problems: list[Violation] = []
        for connection in self.connections:
            for field_name, node_id in (
                ("source_id", connection.source_id),
                ("target_id", connection.target_id),
            ):
                node = nodes.get(node_id)
                if node is None:
                    rule, message = (
                        "dangling_reference",
                        f"{field_name} {node_id!r} is not a node of this architecture.",
                    )
                elif node.kind is NodeKind.BOUNDARY:
                    rule, message = (
                        "boundary_not_connectable",
                        f"{node_id!r} is a boundary; connect the components inside it instead.",
                    )
                else:
                    continue
                problems.append(Violation(rule, message, field_name, ElementType.CONNECTION, connection.id))
            signature = (
                connection.source_id,
                connection.target_id,
                connection.kind.value,
                connection.protocol,
            )
            if signature in seen:
                problems.append(
                    Violation(
                        "duplicate_connection",
                        f"The same connection is already stated as {seen[signature]!r}.",
                        None,
                        ElementType.CONNECTION,
                        connection.id,
                    )
                )
            else:
                seen[signature] = connection.id
        return problems

    def _reference_problems(self) -> list[Violation]:
        known = self.element_ids()
        subjects = [
            *((ElementType.ASSUMPTION, a.id, a.subject_ids) for a in self.assumptions),
            *((ElementType.DECISION, str(d.decision_id), d.subject_ids) for d in self.decisions),
        ]
        problems = [
            Violation(
                "dangling_reference",
                f"{subject!r} is not a node or connection of this architecture.",
                "subject_ids",
                element,
                element_id,
            )
            for element, element_id, subject_ids in subjects
            for subject in subject_ids
            if subject not in known
        ]
        decision_counts = Counter(d.decision_id for d in self.decisions)
        problems += [
            Violation(
                "duplicate_decision",
                "This decision is referenced more than once; list all its subjects in one reference.",
                None,
                ElementType.DECISION,
                str(decision_id),
            )
            for decision_id, count in decision_counts.items()
            if count > 1
        ]
        return problems


def _in_containment_cycle(node: Node, nodes: Mapping[str, Node]) -> bool:
    """True when following parents from ``node`` leads back to ``node`` itself."""
    seen: set[str] = set()
    current = nodes.get(node.parent_id) if node.parent_id else None
    while current is not None and current.id not in seen:
        if current.id == node.id:
            return True
        seen.add(current.id)
        current = nodes.get(current.parent_id) if current.parent_id else None
    return False
