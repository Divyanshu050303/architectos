"""Topology findings: what the declared structure says about failure, for human review.

Every finding names its elements, the evidence (declared properties, the paths that need them), why
it matters, what is missing, and options to investigate; ``modeled`` when the declared facts
establish it, ``candidate`` when they are incomplete. Nothing is assumed: separate nodes are not
independent replicas, a configuration flag is not a working failover, and zones are not separate
failure domains unless declared.

- **Single point of failure**: a component on a request path without redundancy: one declared
  replica, every replica required (``min_healthy_replicas`` = ``replicas``), or no redundancy
  declared at all (candidate), and no alternative in a redundancy group. High when every path needs
  it, medium when some do; off every path it is ``no_redundancy`` (low).
- **Critical dependency without alternative**: a ``critical: true`` connection to such a component.
- **Redundancy without failure-domain separation**: replicas or group members that share their
  declared zones and regions (modeled), or whose placement is not declared (candidate).
- **Potential correlated failure**: redundancy whose ``failure_independence`` is ``correlated``
  (modeled) or not declared independent (candidate).
- **Inconsistent redundancy**: a group of one, members disagreeing on the group's minimum, or a
  member no request path reaches while another is needed (redundancy that is declared but not
  routed).
- **Missing failover**: redundancy with ``failover_mode: none`` (modeled) or no declared mode
  (candidate).
- **Missing recovery data**: a component on a path with neither ``mttr_seconds`` nor
  ``failover_seconds`` (info).
- **Unmodeled dependency**: a waiting connection whose interaction is not stated (treated as
  required, info).
- **Circular dependency**: components that require each other (a cycle of required connections).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from core.architecture_ir.topology import Topology
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.reliability.inputs import ComponentReliability
from core.domain.reliability.results import FindingType, ReliabilityFinding
from core.domain.validation.results import Severity

from .context import ReliabilityContext
from .dependency import Role, closure, entries, role, unstated
from .engine import OUT_OF_SCOPE, Progress, StepMeta, StepOutput

T = FindingType
PLACEMENT = ("region", "availability_zones", "multi_az")


@dataclass(frozen=True, slots=True)
class Redundancy:
    """What a component declares about its redundancy."""

    replicas: int | None
    min_healthy: int | None
    group: str | None
    members: tuple[str, ...]  # the group's members, itself included

    @property
    def tolerates_a_failure(self) -> bool | None:
        """True with an established spare, False with none, None when not declared."""
        if len(self.members) >= 2:
            return True
        if self.replicas is None:
            return None
        if self.replicas < 2:
            return False
        return self.min_healthy is None or self.min_healthy < self.replicas

    @property
    def redundant(self) -> bool:
        return self.tolerates_a_failure is True


def _int(facts: ComponentReliability, name: str) -> int | None:
    value = facts.known(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def redundancy(context: ReliabilityContext, node_id: str, groups: dict[str, tuple[str, ...]]) -> Redundancy:
    facts = context.facts[node_id]
    group = facts.known("redundancy_group")
    name = group if isinstance(group, str) else None
    return Redundancy(
        _int(facts, "replicas"),
        _int(facts, "min_healthy_replicas"),
        name,
        groups.get(name, (node_id,)) if name else (node_id,),
    )


def _groups(context: ReliabilityContext) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = defaultdict(list)
    for node in context.ir.nodes:
        group = context.facts[node.id].known("redundancy_group")
        if isinstance(group, str):
            found[group].append(node.id)
    return {g: tuple(sorted(ids)) for g, ids in found.items()}


def cycles(topology: Topology, nodes: Iterable[str]) -> list[tuple[str, ...]]:
    """Strongly connected sets of two or more nodes over required connections (Tarjan, iterative,
    in id order)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[tuple[str, ...]] = []
    counter = 0

    def targets(n: str) -> list[str]:
        return sorted({c.target_id for c in topology.outgoing(n) if role(c) is Role.REQUIRED})

    for start in sorted(nodes):
        if start in index:
            continue
        work = [(start, iter(targets(start)))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(targets(child))))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == node:
                        break
                if len(members) > 1:
                    found.append(tuple(sorted(members)))
    return sorted(found)


def _zones(facts: ComponentReliability) -> tuple[str, ...] | None:
    zones = facts.known("availability_zones")
    return zones if isinstance(zones, tuple) else None


class TopologyFindings:
    meta = StepMeta(
        id="topology-findings",
        version=1,
        name="Topology findings",
        description="Single points of failure, redundancy, failure domains and failover, as declared.",
        produces=("findings",),
        assumptions=("Only declared redundancy, placement, independence and failover count.",),
        limitations=("Findings are for review: none claims an outage will happen, none is a risk score.",),
    )

    def run(self, context: ReliabilityContext, progress: Progress) -> StepOutput:
        topology = context.topology
        closures = [closure(topology, entry) for entry in entries(context)]
        needed: dict[str, list[str]] = defaultdict(list)  # component -> entries whose paths need it
        for found in closures:
            for node_id in found.components(topology):
                needed[node_id].append(found.entry)
        groups = _groups(context)
        findings: list[ReliabilityFinding] = []
        in_scope = [n for n in context.ir.nodes if n.kind not in OUT_OF_SCOPE]
        weak: dict[str, Certainty] = {}  # components that are single points, and how sure
        for node in in_scope:
            facts = context.facts[node.id]
            spares = redundancy(context, node.id, groups)
            findings += self._redundancy(
                node.id, node.name, facts, spares, needed.get(node.id, []), len(closures), weak
            )
            if spares.redundant:
                findings += self._redundant(context, node.id, node.name, facts, spares)
            if (
                node.id in needed
                and facts.known("mttr_seconds") is None
                and facts.known("failover_seconds") is None
            ):
                findings.append(self._finding(
                    T.MISSING_RECOVERY_DATA, Severity.INFO, Certainty.CANDIDATE, (node.id,),
                    f"{node.name}'s recovery is not described",
                    f"{node.name} is on a request path, but neither how long it takes to repair nor to "
                    "fail over is declared, so recovery objectives cannot be evaluated for it.",
                    "Declare mttr_seconds (and failover_mode, failover_seconds where it fails over).",
                    missing=("configuration.mttr_seconds", "configuration.failover_seconds"),
                ))  # fmt: skip
        for connection in context.ir.connections:
            target = connection.target_id
            source = connection.source_id
            if connection.critical is True and target in weak:
                findings.append(self._finding(
                    T.CRITICAL_DEPENDENCY_WITHOUT_ALTERNATIVE, Severity.HIGH, weak[target], (source, target),
                    f"A critical dependency on {target} has no alternative",
                    f"{source} cannot do its job without {target} (critical), and {target} has no "
                    "declared redundancy or alternative.",
                    f"Review what {connection.source_id} can do while {target} is down; consider redundancy.",
                    connections=(connection.id,), evidence=(Evidence(f"{connection.id}.critical", "true"),),
                ))  # fmt: skip
            if unstated(connection) and any(connection.source_id in c.node_ids for c in closures):
                findings.append(self._finding(
                    T.UNMODELED_DEPENDENCY, Severity.INFO, Certainty.CANDIDATE, (source, target),
                    f"Whether {connection.source_id} waits for {target} is not stated",
                    f"The {connection.kind.value} connection {connection.id} states no interaction; it is "
                    "treated as waiting for its target, so the target's failure is counted against the path.",
                    "State interaction (synchronous or asynchronous), or critical: false if the source "
                    "copes.",
                    connections=(connection.id,), missing=(f"{connection.id}.interaction",),
                ))  # fmt: skip
        findings += self._groups(groups, needed, context)
        for members in cycles(topology, [n.id for n in in_scope]):
            findings.append(self._finding(
                T.CIRCULAR_DEPENDENCY, Severity.MEDIUM, Certainty.MODELED, members,
                f"{', '.join(members)} require each other",
                "These components depend on each other through required connections: a failure of any can "
                "keep the others from recovering, and none can start first.",
                "Review whether every dependency in the cycle is required, and how they start and recover.",
            ))  # fmt: skip
        return StepOutput(findings=tuple(findings))

    def _finding(  # noqa: PLR0913 -- one finding, stated in full
        self,
        kind: FindingType,
        severity: Severity,
        certainty: Certainty,
        nodes: tuple[str, ...],
        title: str,
        explanation: str,
        recommendation: str,
        *,
        connections: tuple[str, ...] = (),
        evidence: tuple[Evidence, ...] = (),
        missing: tuple[str, ...] = (),
    ) -> ReliabilityFinding:
        return ReliabilityFinding(
            kind, severity, certainty, title, explanation, recommendation, nodes, connections, evidence,
            self.meta.assumptions, missing, self.meta.id, self.meta.version,
        )  # fmt: skip

    def _redundancy(
        self,
        node_id: str,
        name: str,
        facts: ComponentReliability,
        spares: Redundancy,
        needed_by: list[str],
        paths: int,
        weak: dict[str, Certainty],
    ) -> list[ReliabilityFinding]:
        tolerant = spares.tolerates_a_failure
        if tolerant:
            return []
        certainty = Certainty.CANDIDATE if tolerant is None else Certainty.MODELED
        evidence = facts.evidence(["replicas", "min_healthy_replicas", "redundancy_group"])
        missing = ("configuration.replicas",) if tolerant is None else ()
        why = (
            "no redundancy is declared"
            if tolerant is None
            else "every replica is required"
            if spares.replicas and spares.replicas >= 2
            else "it runs one replica"
        )
        if not needed_by:
            return [self._finding(
                T.NO_REDUNDANCY, Severity.LOW, certainty, (node_id,), f"{name} has no redundancy",
                f"{name} is on no request path, and {why}.",
                "Review whether it needs redundancy when it becomes part of a request path.",
                evidence=evidence, missing=missing,
            )]  # fmt: skip
        weak[node_id] = certainty
        everywhere = len(needed_by) == paths
        severity = Severity.HIGH if everywhere else Severity.MEDIUM
        scope = (
            "every request path" if everywhere else f"the request paths from {', '.join(sorted(needed_by))}"
        )
        return [self._finding(
            T.SINGLE_POINT_OF_FAILURE, severity, certainty, (node_id,),
            f"{name} is a single point of failure",
            f"{scope.capitalize()} needs {name}, and {why}: its failure interrupts them.",
            "Consider replicas or an alternative (a redundancy group), in separate failure domains, "
            "with a declared failover; then review how requests move to them.",
            evidence=(*evidence, *(Evidence("needed_by", e) for e in sorted(needed_by))), missing=missing,
        )]  # fmt: skip

    def _redundant(
        self,
        context: ReliabilityContext,
        node_id: str,
        name: str,
        facts: ComponentReliability,
        spares: Redundancy,
    ) -> list[ReliabilityFinding]:
        found: list[ReliabilityFinding] = []
        members = spares.members if len(spares.members) > 1 else (node_id,)
        member_facts = [context.facts[m] for m in members]
        # failure domains: replicas share the node's zones; group members are compared with each other
        if len(members) > 1:
            places = [(f.known("region"), _zones(f)) for f in member_facts]
            undeclared = any(r is None and z is None for r, z in places)
            shared = not undeclared and len(set(places)) == 1
        else:
            zones, multi = _zones(facts), facts.known("multi_az")
            undeclared = zones is None and multi is None
            shared = not undeclared and (multi is False or (zones is not None and len(zones) < 2))
        if undeclared or shared:
            certainty = Certainty.MODELED if shared else Certainty.CANDIDATE
            found.append(self._finding(
                T.REDUNDANCY_WITHOUT_FAILURE_DOMAIN_SEPARATION, Severity.MEDIUM if shared else Severity.LOW,
                certainty, members, f"{name}'s redundancy may share a failure domain",
                ("Its replicas or alternatives are declared in the same zones and region: one zone or region "
                 "failure can take all of them." if shared else
                 "Where its replicas or alternatives run is not declared, so whether they can fail together "
                 "is not known."),
                "Declare availability_zones (or multi_az) and region, and review placement across "
                "failure domains.",
                evidence=tuple(e for f in member_facts for e in f.evidence(PLACEMENT)),
                missing=() if shared else ("configuration.availability_zones", "configuration.region"),
            ))  # fmt: skip
        independence = [f.known("failure_independence") for f in member_facts]
        if any(v != "independent" for v in independence):
            correlated = "correlated" in independence
            found.append(self._finding(
                T.POTENTIAL_CORRELATED_FAILURE, Severity.MEDIUM if correlated else Severity.LOW,
                Certainty.MODELED if correlated else Certainty.CANDIDATE, members,
                f"{name}'s redundancy may fail together",
                ("Its replicas or alternatives are declared to fail together (correlated): redundancy does "
                 "not protect against their shared causes." if correlated else
                 "Its replicas or alternatives are not declared to fail independently, so redundant "
                 "availability is not calculated for them."),
                "Review shared dependencies, deployments and hardware; declare failure_independence.",
                evidence=tuple(e for f in member_facts for e in f.evidence(["failure_independence"])),
                missing=() if correlated else ("configuration.failure_independence",),
            ))  # fmt: skip
        mode = facts.known("failover_mode")
        if mode in (None, "none"):
            found.append(self._finding(
                T.MISSING_FAILOVER, Severity.MEDIUM if mode == "none" else Severity.LOW,
                Certainty.MODELED if mode == "none" else Certainty.CANDIDATE, (node_id,),
                f"{name} has redundancy without a declared failover",
                ("It declares no failover: work on a failed replica or member fails until it is repaired."
                 if mode == "none" else
                 "How work moves off a failed replica or member is not declared."),
                "Declare failover_mode and failover_seconds, and review how failover is triggered and "
                "tested.",
                evidence=facts.evidence(["failover_mode"]),
                missing=() if mode == "none" else ("configuration.failover_mode",),
            ))  # fmt: skip
        return found

    def _groups(
        self, groups: dict[str, tuple[str, ...]], needed: dict[str, list[str]], context: ReliabilityContext
    ) -> list[ReliabilityFinding]:
        found: list[ReliabilityFinding] = []
        for group, members in sorted(groups.items()):
            label = f"redundancy group {group}"
            if len(members) == 1:
                found.append(self._finding(
                    T.INCONSISTENT_REDUNDANCY, Severity.LOW, Certainty.MODELED, members,
                    f"The {label} has one member",
                    f"Only {members[0]} declares the {label}: it has no alternative.",
                    "Add the alternative members, or remove the group.",
                ))  # fmt: skip
                continue
            minimums = {_int(context.facts[m], "redundancy_group_min_healthy") for m in members} - {None}
            if len(minimums) > 1:
                found.append(self._finding(
                    T.INCONSISTENT_REDUNDANCY, Severity.MEDIUM, Certainty.MODELED, members,
                    f"The {label}'s members disagree on its minimum",
                    f"The members of the {label} declare different redundancy_group_min_healthy values: "
                    "how many must stay healthy is not established.",
                    "Declare the same redundancy_group_min_healthy on every member.",
                ))  # fmt: skip
            routed = [m for m in members if m in needed]
            unrouted = [m for m in members if m not in needed]
            verb, pronoun = ("is", "it") if len(unrouted) == 1 else ("are", "them")
            if routed and unrouted:
                found.append(self._finding(
                    T.INCONSISTENT_REDUNDANCY, Severity.MEDIUM, Certainty.MODELED, tuple(unrouted),
                    f"Part of the {label} is not on any request path",
                    f"{', '.join(unrouted)} {verb} declared as alternatives to {', '.join(routed)}, but no "
                    f"request path reaches {pronoun}: the redundancy is declared, not routed.",
                    "Connect the alternatives the way requests would reach them (e.g. behind the same load "
                    "balancer), or review the group.",
                    evidence=tuple(Evidence("routed_member", m) for m in routed),
                ))  # fmt: skip
        return found
