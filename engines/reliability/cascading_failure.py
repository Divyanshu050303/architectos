"""How far a failure reaches, and what the paths could not establish.

- **Blast radius** (``affected``): every component that requires a node, directly or through other
  required connections (the reverse of a path's closure). Single-point and critical-dependency
  findings list it as evidence: what the failure can interrupt, not a prediction that it will.
- **Availability not evaluable**: one finding per request path whose availability is unknown,
  naming the components and inputs it lacks (e.g. ``db.availability``,
  ``eu.failure_independence``).
- **Unverified reliability data**: reliability inputs on a request path that the architecture
  marks as inferred or as proposed by a language model, i.e. not declared by a person nor read
  from a system; the estimates use them, and say so.

Stale data is not judged: that needs a reference date, and an analysis is deterministic (it reads
no clock); the provenance of every input is in the evidence.
"""

from collections import deque

from core.architecture_ir.topology import Topology
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.reliability.results import FindingType, ReliabilityFinding
from core.domain.validation.results import Severity

from .context import ReliabilityContext
from .dependency import Role, role
from .engine import OUT_OF_SCOPE, Progress, StepMeta, StepOutput


def affected(topology: Topology, node_id: str) -> tuple[str, ...]:
    """Every component requiring ``node_id``, directly or not (itself excluded), in id order."""
    seen = {node_id}
    queue = deque([node_id])
    while queue:
        for connection in topology.incoming(queue.popleft()):
            if role(connection) is Role.REQUIRED and connection.source_id not in seen:
                seen.add(connection.source_id)
                queue.append(connection.source_id)
    seen.discard(node_id)
    return tuple(n.id for n in topology.ir.nodes if n.id in seen)


def _elements(missing: tuple[str, ...], topology: Topology) -> tuple[str, ...]:
    """The nodes a missing input belongs to (``db.availability`` -> ``db``)."""
    return tuple(
        sorted({m.split(".", 1)[0] for m in missing if topology.node(m.split(".", 1)[0]) is not None})
    )


def _in_scope(topology: Topology, node_id: str) -> bool:
    node = topology.node(node_id)
    return node is not None and node.kind not in OUT_OF_SCOPE


class PathFindings:
    meta = StepMeta(
        id="path-findings",
        version=1,
        name="Path findings",
        description="Paths whose availability cannot be evaluated, and unverified reliability data on paths.",
        produces=("findings",),
        limitations=("Stale data is not judged: it needs a reference date the analysis does not read.",),
    )

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        topology = context.topology
        findings: list[ReliabilityFinding] = []
        on_paths: set[str] = set()
        for path in progress.paths:
            on_paths.update(
                n for n in path.node_ids if (node := topology.node(n)) and node.kind not in OUT_OF_SCOPE
            )
            estimate = path.availability
            if estimate.known:
                continue
            nodes = _elements(estimate.missing, topology) or (path.entry_id,)
            findings.append(
                ReliabilityFinding(
                    FindingType.AVAILABILITY_NOT_EVALUABLE,
                    Severity.LOW,
                    Certainty.MODELED,
                    f"The availability of requests from {path.entry_id} cannot be estimated",
                    f"The path from {path.entry_id} requires {len(path.node_ids) - 1} components; its "
                    f"availability needs inputs that are not declared: {', '.join(estimate.missing)}.",
                    "Declare the missing inputs (availability, or MTBF and MTTR with replicas; for "
                    "alternatives, independence, failover and the group minimum), or review whether each "
                    "dependency is required.",
                    node_ids=nodes,
                    evidence=(Evidence("entry", path.entry_id),),
                    missing=estimate.missing,
                    model_id=self.meta.id,
                    model_version=self.meta.version,
                )
            )
        for node_id in sorted(on_paths):
            proposed = [
                f for f in context.facts[node_id].facts.values() if f.proposed and f.value is not None
            ]
            if proposed:
                findings.append(
                    ReliabilityFinding(
                        FindingType.UNVERIFIED_RELIABILITY_DATA,
                        Severity.LOW,
                        Certainty.CANDIDATE,
                        f"{node_id}'s reliability data is not confirmed",
                        f"{', '.join(sorted(f.path for f in proposed))} on {node_id} are inferred or "
                        "proposed by a language model, not declared by a person or read from a system; the "
                        "estimates use them.",
                        "Confirm the values from a system or its owner, and record their provenance.",
                        node_ids=(node_id,),
                        evidence=tuple(f.evidence() for f in sorted(proposed, key=lambda f: f.name)),
                        model_id=self.meta.id,
                        model_version=self.meta.version,
                    )
                )
        return StepOutput(findings=tuple(findings))
