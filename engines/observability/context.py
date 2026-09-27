"""The immutable inputs of one observability analysis: the IR revision, the request (scope,
analyzers, requirement ids, assumptions), the project's architecture policy and requirements, each
element's declared observability facts, the collection paths, and the shared graph index. Analyzers
read it and never change it. ``fingerprint`` identifies every input besides the architecture's
content (which the revision's content hash identifies).

**Collection.** A component's logs, metrics or traces are *collected* in the model when a path of
connections declaring that signal in ``telemetry`` leads from it to an observability component (the
path may pass through collectors or agents); an observability component collects its own. Computed
once per signal, by one reverse search from the observability components.

**Coverage** per component and dimension (see ``core/domain/observability/values.py``): declared on
and collected (or consumed, or delivered) → ``modeled``; declared on but incomplete → ``partial``;
declared off → ``absent``; not declared → ``unknown``; a third party → ``unsupported``.
"""

import hashlib
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.topology import Topology
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.inputs import ComponentObservability, ConnectionObservability
from core.domain.observability.values import SIGNAL_OF, CoverageState, Dimension
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.validation.options import RevisionInfo

NOT_COMPONENTS = frozenset({NodeKind.CLIENT, NodeKind.BOUNDARY})
S = CoverageState
# The dimension's declaring property (the analyzers' and coverage's "is it modeled?").
PROPERTY_OF: dict[Dimension, str] = {
    Dimension.LOGGING: "logs",
    Dimension.METRICS: "metrics",
    Dimension.TRACING: "traces",
    Dimension.HEALTH_CHECKS: "health_check",
    Dimension.ALERTING: "alerts",
}


@dataclass(frozen=True)
class ObservabilityContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    request: ObservabilityAnalysisRequest
    policy: ArchitecturePolicy = field(default_factory=ArchitecturePolicy)  # the project's, at the analysis
    requirements: tuple[Requirement, ...] = ()  # the project's in-force requirements

    @cached_property
    def evaluated_requirements(self) -> tuple[Requirement, ...]:
        """The in-force requirements to evaluate: the request's, if it names some, else all."""
        named = self.request.requirement_ids
        if named is None:
            return self.requirements
        wanted = set(named)
        return tuple(r for r in self.requirements if r.id in wanted)

    @cached_property
    def topology(self) -> Topology:
        return Topology(self.ir)

    @cached_property
    def memo(self) -> dict[Any, Any]:
        """Work shared by the analyzers of this one analysis; never shared between analyses."""
        return {}

    @cached_property
    def components(self) -> tuple[Node, ...]:
        """The components in scope (the request's scope, else every component), in id order."""
        scope = set(self.request.scope)
        return tuple(
            n for n in self.ir.nodes if n.kind not in NOT_COMPONENTS and (not scope or n.id in scope)
        )

    @cached_property
    def component_ids(self) -> frozenset[str]:
        return frozenset(n.id for n in self.components)

    @cached_property
    def connections(self) -> tuple[Connection, ...]:
        """The connections touching a component in scope (all of them without a scope)."""
        ids, scoped = self.component_ids, bool(self.request.scope)
        return tuple(c for c in self.ir.connections if not scoped or c.source_id in ids or c.target_id in ids)

    @cached_property
    def connection_ids(self) -> frozenset[str]:
        return frozenset(c.id for c in self.connections)

    @cached_property
    def facts(self) -> Mapping[str, ComponentObservability]:
        return {n.id: ComponentObservability.of(n) for n in self.ir.nodes if n.kind is not NodeKind.BOUNDARY}

    @cached_property
    def connection_facts(self) -> Mapping[str, ConnectionObservability]:
        return {c.id: ConnectionObservability.of(c) for c in self.ir.connections}

    @cached_property
    def observability_ids(self) -> frozenset[str]:
        """The observability components (collectors, backends) of the revision."""
        return frozenset(n.id for n in self.ir.nodes if n.kind is NodeKind.OBSERVABILITY)

    def collected(self, signal: str) -> Mapping[str, tuple[str, ...]]:
        """For ``signal`` (logs, metrics, traces): every node whose telemetry reaches an observability
        component, with the connections of the path (the first found, breadth first in id order);
        an observability component maps to ()."""
        key = ("collected", signal)
        if key not in self.memo:
            incoming: dict[str, list[Connection]] = {}
            for c in sorted(self.ir.connections, key=lambda c: c.id):
                signals = self.connection_facts[c.id].signals or ()
                if signal in signals:
                    incoming.setdefault(c.target_id, []).append(c)
            paths: dict[str, tuple[str, ...]] = {o: () for o in sorted(self.observability_ids)}
            queue = deque(sorted(self.observability_ids))
            while queue:
                current = queue.popleft()
                for c in incoming.get(current, ()):
                    if c.source_id not in paths:
                        paths[c.source_id] = (c.id, *paths[current])
                        queue.append(c.source_id)
            self.memo[key] = paths
        result: Mapping[str, tuple[str, ...]] = self.memo[key]
        return result

    @cached_property
    def health_consumers(self) -> Mapping[str, tuple[str, ...]]:
        """For each node, the connections over which a component checks its health."""
        consumers: dict[str, list[str]] = {}
        for c in sorted(self.ir.connections, key=lambda c: c.id):
            if self.connection_facts[c.id].known("health_check") is True:
                consumers.setdefault(c.target_id, []).append(c.id)
        return {k: tuple(v) for k, v in consumers.items()}

    def alert_delivery(self, node_id: str) -> tuple[str, ...]:
        """The observability components that receive this node's metrics or logs and declare an
        alert delivery other than none (where its alert rules can fire from)."""
        found = []
        for backend in sorted(self.observability_ids):
            delivery = self.facts[backend].known("alert_delivery")
            if delivery is None or delivery == "none":
                continue
            reached = any(
                node_id in self.collected(s) and self._ends_at(node_id, s, backend)
                for s in ("metrics", "logs")
            )
            if reached or node_id == backend:
                found.append(backend)
        return tuple(found)

    def _ends_at(self, node_id: str, signal: str, backend: str) -> bool:
        """Whether ``node_id``'s path for ``signal`` ends at ``backend``."""
        path = self.collected(signal).get(node_id)
        if path is None:
            return False
        if not path:
            return node_id == backend
        last = self.topology.connection(path[-1])
        return last is not None and last.target_id == backend

    def coverage(self, node: Node) -> dict[Dimension, CoverageState]:
        """The node's coverage state per dimension, from what it declares (see the module doc)."""
        facts = self.facts[node.id]
        if node.kind is NodeKind.EXTERNAL:
            return dict.fromkeys(Dimension, S.UNSUPPORTED)  # a third party's internals are not ours
        states: dict[Dimension, CoverageState] = {}
        for dimension, name in PROPERTY_OF.items():
            value = facts.known(name)
            if value is None:
                states[dimension] = S.UNKNOWN
            elif value is False or value == ():
                states[dimension] = S.ABSENT
            elif dimension in SIGNAL_OF:
                states[dimension] = (
                    S.MODELED if node.id in self.collected(SIGNAL_OF[dimension]) else S.PARTIAL
                )
            elif dimension is Dimension.HEALTH_CHECKS:
                states[dimension] = S.MODELED if node.id in self.health_consumers else S.PARTIAL
            else:  # alerting: every alerted signal emitted, and a delivery path
                states[dimension] = S.MODELED if self._alerting_complete(node) else S.PARTIAL
        return states

    def _alerting_complete(self, node: Node) -> bool:
        facts = self.facts[node.id]
        watched = facts.alert_signals or ()
        return all(self.emits(node, s) for s in watched) and bool(self.alert_delivery(node.id))

    def declares_signal(self, node_id: str, signal: str) -> bool:
        """Whether the node declares it emits a telemetry signal: logs or traces true, or a metric."""
        facts = self.facts.get(node_id)
        if facts is None:
            return False
        if signal == "metrics":
            return bool(facts.metric_kinds)
        return facts.known(signal) is True

    def emits(self, node: Node, signal: str) -> bool:
        """Whether the node declares the signal an alert rule watches (a metric kind, health or logs)."""
        facts = self.facts[node.id]
        if signal == "logs":
            return facts.known("logs") is True
        if signal == "health":
            return facts.known("health_check") is True
        return signal in (facts.metric_kinds or ())

    @property
    def fingerprint(self) -> str:
        document = {
            "revision": [
                self.revision.architecture_id,
                self.revision.number,
                self.revision.content_hash,
                self.revision.schema_version,
            ],
            "request": self.request.inputs(),
            "policy": self.policy.to_dict(),
            "requirements": sorted([str(r.id), r.version, r.content.status.value] for r in self.requirements),
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
