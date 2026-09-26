"""Structural rules: shapes the IR allows (it must, to hold work in progress) but that deserve a
finding. The IR's own invariants (unique ids, existing endpoints, containment without cycles, …)
are enforced when an architecture is built and are deliberately not repeated here: an IR that
reaches a rule is structurally valid.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Lifecycle
from core.architecture_ir.versioning import IR_SCHEMA_VERSION
from core.domain.validation.results import MAX_REFERENCES, Category, Finding, Severity

from ..context import ValidationContext
from ..engine import Outcome, ParamSpec, ParamType, RuleMeta
from ..findings import finding

ALL_PROFILES = frozenset({"default", "strict"})


def capped(ids: Sequence[str]) -> tuple[str, ...]:
    """At most as many ids as a finding may name (its evidence then says how many there were)."""
    return tuple(sorted(set(ids)))[:MAX_REFERENCES]


@dataclass(frozen=True, slots=True)
class DisconnectedComponents:
    meta = RuleMeta(
        "structure.disconnected-component",
        1,
        "Disconnected components",
        "Every component takes part in the architecture: it has at least one connection.",
        Category.STRUCTURE,
        Severity.MEDIUM,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        topology = context.topology
        components = topology.components()
        if len(components) < 2:  # a single component has nothing to connect to
            return Outcome()
        return Outcome(
            tuple(
                finding(
                    self.meta,
                    "disconnected",
                    title=f"{node.name} is not connected to anything",
                    explanation=(
                        f"The {node.kind} {node.name!r} has no incoming or outgoing connection, so "
                        "nothing uses it and it uses nothing: it is either missing its connections "
                        "or not part of this architecture."
                    ),
                    remediation="Connect it to the components it serves or uses, or remove it.",
                    entity_ids=[node.id],
                    field_paths=["connections"],
                    expected="at least one connection",
                    actual="no connections",
                )
                for node in components
                if not topology.incoming(node.id) and not topology.outgoing(node.id)
            )
        )


@dataclass(frozen=True, slots=True)
class EmptyBoundaries:
    meta = RuleMeta(
        "structure.empty-boundary",
        1,
        "Empty boundaries",
        "Every boundary contains at least one node.",
        Category.STRUCTURE,
        Severity.LOW,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        topology = context.topology
        return Outcome(
            tuple(
                finding(
                    self.meta,
                    "empty_boundary",
                    title=f"The boundary {boundary.name} is empty",
                    explanation=(
                        f"{boundary.name!r} delimits nothing: no node has it as its parent, so it "
                        "places, isolates or groups nothing."
                    ),
                    remediation="Move the nodes it should contain into it, or remove it.",
                    entity_ids=[boundary.id],
                    expected="at least one node inside",
                    actual="no nodes",
                )
                for boundary in topology.nodes_of_kind(NodeKind.BOUNDARY)
                if not topology.children(boundary.id)
            )
        )


def strongly_connected(nodes: Sequence[str], edges: Mapping[str, Sequence[str]]) -> list[list[str]]:
    """The strongly connected components of a directed graph (Kosaraju, iterative so a deep graph
    cannot exhaust the stack), each sorted, in sorted order: deterministic for equal inputs."""
    order: list[str] = []
    seen: set[str] = set()
    for start in nodes:
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(edges.get(start, ())))]
        while stack:
            node, successors = stack[-1]
            nxt = next((s for s in successors if s not in seen), None)
            if nxt is None:
                stack.pop()
                order.append(node)
            else:
                seen.add(nxt)
                stack.append((nxt, iter(edges.get(nxt, ()))))
    reverse: dict[str, list[str]] = {}
    for source in nodes:
        for target in edges.get(source, ()):
            reverse.setdefault(target, []).append(source)
    assigned: set[str] = set()
    groups: list[list[str]] = []
    for start in reversed(order):
        if start in assigned:
            continue
        assigned.add(start)
        group, frontier = [], [start]
        while frontier:
            node = frontier.pop()
            group.append(node)
            for previous in reverse.get(node, ()):
                if previous not in assigned:
                    assigned.add(previous)
                    frontier.append(previous)
        groups.append(sorted(group))
    return sorted(groups)


@dataclass(frozen=True, slots=True)
class SynchronousCycles:
    meta = RuleMeta(
        "structure.synchronous-cycle",
        1,
        "Synchronous request cycles",
        "No group of components calls itself back synchronously: each would wait on the others.",
        Category.STRUCTURE,
        Severity.HIGH,
        profiles=ALL_PROFILES,
        parameters={
            "include_unstated": ParamSpec(
                ParamType.BOOLEAN,
                "Treat requests whose interaction is not stated as synchronous (a request "
                "expects a response).",
                default=True,
            )
        },
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        waiting: set[Interaction | None] = {Interaction.SYNCHRONOUS}
        if parameters["include_unstated"]:
            waiting.add(None)
        calls = [
            c for c in context.ir.connections if c.kind is ConnectionKind.REQUEST and c.interaction in waiting
        ]
        edges: dict[str, list[str]] = {}
        for c in calls:
            edges.setdefault(c.source_id, []).append(c.target_id)
        nodes = sorted({c.source_id for c in calls} | {c.target_id for c in calls})
        return Outcome(
            tuple(
                self._cycle(group, [c for c in calls if c.source_id in group and c.target_id in group])
                for group in strongly_connected(nodes, edges)
                if len(group) > 1
            )
        )

    def _cycle(self, group: list[str], connections: list[Connection]) -> Finding:
        shown = ", ".join(group[:10]) + (", …" if len(group) > 10 else "")
        return finding(
            self.meta,
            "synchronous_cycle",
            title=f"{len(group)} components call each other in a synchronous cycle",
            explanation=(
                "These components reach each other through synchronous requests only. A slow or "
                "failed call anywhere in the cycle can hold every one of them, and a request can "
                "end up waiting on itself."
            ),
            remediation=(
                "Break the cycle: make one of the calls asynchronous (an event or a queue), move "
                "the shared logic into one component, or invert one dependency."
            ),
            entity_ids=capped([*group, *(c.id for c in connections)]),
            expected="no synchronous request cycle",
            actual=f"a cycle through {shown}",
            evidence=[("components", str(len(group))), ("requests", str(len(connections)))],
        )


@dataclass(frozen=True, slots=True)
class DeprecatedConnections:
    meta = RuleMeta(
        "structure.deprecated-dependency",
        1,
        "Connections involving deprecated components",
        "Components that are staying do not depend on components being retired.",
        Category.STRUCTURE,
        Severity.MEDIUM,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        topology = context.topology
        findings: list[Finding] = []
        for connection in context.ir.connections:
            source = topology.node(connection.source_id)
            target = topology.node(connection.target_id)
            if source is None or target is None:  # impossible in a valid IR
                continue
            source_old = source.lifecycle is Lifecycle.DEPRECATED
            target_old = target.lifecycle is Lifecycle.DEPRECATED
            ids = [connection.id, source.id, target.id]
            if target_old and not source_old:
                findings.append(
                    finding(
                        self.meta,
                        "depends_on_deprecated",
                        title=f"{source.name} depends on the deprecated {target.name}",
                        explanation=(
                            f"{target.name!r} is deprecated (being replaced or retired), but "
                            f"{source.name!r}, which is staying, still relies on it through "
                            f"{connection.id!r}."
                        ),
                        remediation=(
                            "Move the connection to the replacement, or plan the migration before "
                            "the deprecated component is retired."
                        ),
                        entity_ids=ids,
                        field_paths=["target_id"],
                        expected="a target that is not deprecated",
                        actual="deprecated",
                    )
                )
            elif source_old and not target_old:
                findings.append(
                    finding(
                        self.meta,
                        "deprecated_source",
                        title=f"The deprecated {source.name} still uses {target.name}",
                        explanation=(
                            f"{source.name!r} is deprecated but still connects to {target.name!r} "
                            f"through {connection.id!r}; the connection goes when it is retired."
                        ),
                        remediation="Check that nothing still needs this traffic once it is retired.",
                        entity_ids=ids,
                        field_paths=["source_id"],
                        severity=Severity.LOW,
                    )
                )
        return Outcome(tuple(findings))


@dataclass(frozen=True, slots=True)
class SchemaVersion:
    """The IR refuses documents from a schema this code does not know; what is left to report is
    a revision stored in an older schema and upgraded when read."""

    meta = RuleMeta(
        "structure.schema-version",
        1,
        "Schema version",
        "The revision is stored in the current IR schema version.",
        Category.STRUCTURE,
        Severity.INFO,
        profiles=ALL_PROFILES,
        mandatory=True,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        stored = context.revision.schema_version
        if stored >= IR_SCHEMA_VERSION:
            return Outcome()
        upgraded = finding(
            self.meta,
            "upgraded_on_read",
            title="This revision uses an older schema version",
            explanation=(
                f"The revision was stored in IR schema version {stored} and upgraded to version "
                f"{IR_SCHEMA_VERSION} to be validated. Upgrades never change what the architecture "
                "says; values they had to introduce are marked as such."
            ),
            remediation="Save a new revision to store it in the current schema version.",
            field_paths=["schema_version"],
            expected=str(IR_SCHEMA_VERSION),
            actual=str(stored),
        )
        return Outcome((upgraded,))


RULES = (
    DisconnectedComponents(),
    EmptyBoundaries(),
    SynchronousCycles(),
    DeprecatedConnections(),
    SchemaVersion(),
)
