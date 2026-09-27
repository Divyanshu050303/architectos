"""The immutable inputs of one security analysis: the IR revision, the request (scope, analyzers,
assumptions), the project's architecture policy and requirements, each element's declared security
facts, the trust zones and the shared graph index. Analyzers read it and never change it (the
architecture is never modified). ``fingerprint`` identifies every input besides the architecture's
content (which the revision's content hash identifies)."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.topology import Topology
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.requirements.entities import Requirement
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.inputs import BoundarySecurity, ComponentSecurity, ConnectionSecurity
from core.domain.security.results import TrustZone
from core.domain.validation.options import RevisionInfo

# Request sources and groupings are not components whose controls are analyzed.
NOT_COMPONENTS = frozenset({NodeKind.CLIENT, NodeKind.BOUNDARY})


@dataclass(frozen=True)
class SecurityContext:
    ir: ArchitectureIR
    revision: RevisionInfo
    request: SecurityAnalysisRequest
    policy: ArchitecturePolicy = field(default_factory=ArchitecturePolicy)  # the project's, at the analysis
    requirements: tuple[Requirement, ...] = ()  # the project's live requirements (any status)

    @cached_property
    def topology(self) -> Topology:
        return Topology(self.ir)

    @cached_property
    def memo(self) -> dict[Any, Any]:
        """Work shared by the analyzers of this one analysis (e.g. reachability): every input is
        immutable, so a result computed once holds for the whole analysis. Never shared between
        analyses."""
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
        ids = self.component_ids
        scoped = bool(self.request.scope)
        return tuple(c for c in self.ir.connections if not scoped or c.source_id in ids or c.target_id in ids)

    @cached_property
    def connection_ids(self) -> frozenset[str]:
        return frozenset(c.id for c in self.connections)

    @cached_property
    def scope_zone_ids(self) -> frozenset[str]:
        """The trust zones containing a component in scope."""
        return frozenset(z for n in self.component_ids for z in self.zones_of.get(n, ()))

    @cached_property
    def facts(self) -> Mapping[str, ComponentSecurity]:
        """Every non-boundary node's declared security facts, by node id (clients included: what
        they declare can matter to the components they call)."""
        return {n.id: ComponentSecurity.of(n) for n in self.ir.nodes if n.kind is not NodeKind.BOUNDARY}

    @cached_property
    def connection_facts(self) -> Mapping[str, ConnectionSecurity]:
        return {c.id: ConnectionSecurity.of(c) for c in self.ir.connections}

    @cached_property
    def boundary_facts(self) -> Mapping[str, BoundarySecurity]:
        return {n.id: BoundarySecurity.of(n) for n in self.ir.nodes if n.kind is NodeKind.BOUNDARY}

    @cached_property
    def zones_of(self) -> Mapping[str, tuple[str, ...]]:
        """The trust zones containing each non-boundary node, innermost first."""
        zone_ids = {b for b, facts in self.boundary_facts.items() if facts.trust_zone}
        return {
            n.id: tuple(a.id for a in self.topology.ancestors(n.id) if a.id in zone_ids)
            for n in self.ir.nodes
            if n.kind is not NodeKind.BOUNDARY
        }

    @cached_property
    def trust_zones(self) -> tuple[TrustZone, ...]:
        """Every boundary declared as a trust zone, with its trust level (None: not modeled) and the
        nodes inside it at any depth. Nothing is inferred from names."""
        members: dict[str, list[str]] = {
            b: [] for b, facts in self.boundary_facts.items() if facts.trust_zone
        }
        for node_id, containing in self.zones_of.items():
            for zone in containing:
                members[zone].append(node_id)
        zones: list[TrustZone] = []
        for boundary_id, inside in members.items():
            level = self.boundary_facts[boundary_id].known("trust_level")
            zones.append(TrustZone(boundary_id, level if isinstance(level, str) else None, tuple(inside)))
        return tuple(sorted(zones, key=lambda z: z.boundary_id))

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
