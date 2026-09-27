"""Exposure analysis: what the architecture declares reachable from the internet, and what that
reaches. Public reachability is only what is declared (``exposure: public``): never inferred from a
name, a technology, a cloud provider's conventions or an undocumented assumption.

- A component declaring ``management_interface: true`` and ``exposure: public`` exposes its
  administration to the internet (``public_management_interface``, a control gap); with its exposure
  not declared, it cannot be evaluated (``exposure_not_modeled``).
- From every public entry point, the modeled flows are followed (from the side that initiates to the
  side it reaches; both ways for bidirectional flows; dependencies carry no traffic). A component that
  is not itself public but holds sensitive data, performs sensitive operations or exposes a
  management interface, and that such a path reaches, is a potential risk
  (``sensitive_component_reachable_from_public``): the finding names the exact path from the nearest
  public entry. Reachability says a path exists in the model, not that it can be exploited: the
  controls on the path are the other analyzers'.
- A component a client calls directly whose exposure is not declared cannot be evaluated: whether it
  faces the internet is unknown (``exposure_not_modeled``).

The search is one breadth-first traversal over the revision's connections (bounded by the IR's
size), computed once per analysis.
"""

from collections import deque
from collections.abc import Mapping
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.engine_results import Evidence
from core.domain.security.inputs import ComponentSecurity
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress, names
from .support import certainty, evidence, finding

T = FindingType
TARGET_FACTS = (
    "exposure",
    "data_classification",
    "personal_data",
    "sensitive_operations",
    "management_interface",
)

type Step = tuple[str | None, str | None]  # (previous node, connection) on the way from an entry


def reach(context: SecurityContext) -> Mapping[str, Step]:
    """Every node reachable from a declared public component, with the step it was first reached by
    (breadth first from all public entries at once, in id order: the nearest entry, deterministically).
    Entries map to (None, None)."""
    key = ("exposure", "reach")
    if key not in context.memo:
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for connection in sorted(context.ir.connections, key=lambda c: c.id):
            if not connection.kind.communicates:
                continue
            adjacency.setdefault(connection.source_id, []).append((connection.target_id, connection.id))
            if connection.bidirectional:
                adjacency.setdefault(connection.target_id, []).append((connection.source_id, connection.id))
        entries = sorted(n for n, facts in context.facts.items() if facts.known("exposure") == "public")
        steps: dict[str, Step] = {entry: (None, None) for entry in entries}
        queue = deque(entries)
        while queue:
            current = queue.popleft()
            for target, connection_id in adjacency.get(current, ()):
                if target not in steps:
                    steps[target] = (current, connection_id)
                    queue.append(target)
        context.memo[key] = steps
    result: Mapping[str, Step] = context.memo[key]
    return result


def path_to(steps: Mapping[str, Step], node_id: str) -> tuple[list[str], list[str]]:
    """The nodes (entry first) and connections of the path by which ``node_id`` was reached."""
    nodes, connections = [node_id], []
    previous, connection = steps[node_id]
    while previous is not None and connection is not None:
        nodes.append(previous)
        connections.append(connection)
        previous, connection = steps[previous]
    return nodes[::-1], connections[::-1]


def _what(facts: ComponentSecurity) -> list[str]:
    reasons = []
    if facts.sensitive is True:
        reasons.append("holds sensitive data")
    if facts.known("sensitive_operations") is True:
        reasons.append("performs sensitive operations")
    if facts.known("management_interface") is True:
        reasons.append("exposes a management interface")
    return reasons


class Exposure:
    meta = AnalyzerMeta(
        id="exposure",
        version=1,
        name="Exposure",
        description="Declared public components, management interfaces, and sensitive components the "
        "modeled flows reach from a public entry point.",
        category=FindingCategory.EXPOSURE,
        finding_types=(
            T.PUBLIC_MANAGEMENT_INTERFACE,
            T.SENSITIVE_COMPONENT_REACHABLE_FROM_PUBLIC,
            T.EXPOSURE_NOT_MODELED,
        ),
        inputs=("components", "connections"),
        properties=TARGET_FACTS,
        rules=(
            "A management interface declared public is a gap; with its exposure not declared it cannot "
            "be evaluated.",
            "A component that is not public but is sensitive or exposes a management interface, reached "
            "by a modeled path from a public component, is a potential risk (the path is named).",
            "A component a client calls directly whose exposure is not declared cannot be evaluated.",
        ),
        unsupported=(
            "Reachability that is not modeled (no inference from names, networks or providers).",
            "Whether a path can be exploited (its controls are the other analyzers').",
        ),
        limitations=("Reachability is a path in the model, not a demonstrated attack.",),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        steps = reach(context)
        called_by_clients = {
            c.target_id
            for c in context.ir.connections
            if c.kind.communicates
            and (source := context.topology.node(c.source_id)) is not None
            and source.kind is NodeKind.CLIENT
        }
        findings: list[SecurityFinding] = []
        for node in context.components:
            facts = context.facts[node.id]
            exposure = facts.known("exposure")
            management = facts.known("management_interface") is True
            if management and exposure == "public":
                findings.append(self._public_management(node, facts))
            elif exposure is None and (management or node.id in called_by_clients):
                findings.append(self._not_modeled(node, facts, management))
            if exposure != "public" and node.id in steps and _what(facts):
                findings.append(self._reachable(context, node, facts, steps))
        return AnalyzerOutput(tuple(findings))

    def _public_management(self, node: Node, facts: ComponentSecurity) -> SecurityFinding:
        props = ("exposure", "management_interface", "authentication")
        return finding(
            self.meta,
            T.PUBLIC_MANAGEMENT_INTERFACE,
            Severity.HIGH,
            certainty((facts, props)),
            title=f"{node.id} exposes a management interface to the internet",
            explanation=f"{node.id} declares a management interface and public exposure: its "
            "administration is reachable by anyone on the internet, whatever protects it.",
            recommendation=f"Review whether the management interface of {node.id} should be reachable "
            "only from an internal network, a bastion or a VPN.",
            node_ids=(node.id,),
            evidence=evidence(facts, props),
        )

    def _not_modeled(self, node: Node, facts: ComponentSecurity, management: bool) -> SecurityFinding:
        why = "exposes a management interface" if management else "is called directly by a client"
        return finding(
            self.meta,
            T.EXPOSURE_NOT_MODELED,
            Severity.MEDIUM if management else Severity.LOW,
            certainty((facts, ("exposure", "management_interface"))),
            title=f"Whether {node.id} is reachable from the internet is not modeled",
            explanation=f"{node.id} {why}, but its exposure is not declared: whether it faces the "
            "internet is unknown — neither assumed public nor assumed private.",
            recommendation=f"State the exposure of {node.id} (public, internal or private).",
            node_ids=(node.id,),
            evidence=evidence(facts, ("exposure", "management_interface")),
            missing=(f"{node.id}.configuration.exposure",),
        )

    def _reachable(
        self, context: SecurityContext, node: Node, facts: ComponentSecurity, steps: Mapping[str, Step]
    ) -> SecurityFinding:
        nodes, connections = path_to(steps, node.id)
        entry = context.facts[nodes[0]]
        what = " and ".join(_what(facts))
        route = " → ".join(nodes) if len(nodes) <= 20 else f"{' → '.join(nodes[:10])} → … → {nodes[-1]}"
        hops = f"{len(connections)} hop{'s' if len(connections) != 1 else ''}"
        fields: dict[str, Any] = {
            "node_ids": tuple(nodes),
            "connection_ids": tuple(connections),
            "evidence": (
                Evidence(f"{node.id}.reachable_from", nodes[0]),
                Evidence(f"{node.id}.path", route),
                *evidence(entry, ("exposure",)),
                *evidence(facts, TARGET_FACTS),
            ),
        }
        return finding(
            self.meta,
            T.SENSITIVE_COMPONENT_REACHABLE_FROM_PUBLIC,
            Severity.MEDIUM,
            certainty((entry, ("exposure",)), (facts, TARGET_FACTS)),
            title=f"{node.id} {what} and is reachable from the public {nodes[0]}",
            explanation=f"{node.id} is not declared public, but it {what}, and the modeled flows reach it "
            f"from the public {nodes[0]} in {hops} ({names(connections)}). A path in the model is not a "
            "demonstrated attack: the controls on it decide.",
            recommendation=f"Review the controls on the path from {nodes[0]} to {node.id}, and whether "
            "the path is needed.",
            **fields,
        )
