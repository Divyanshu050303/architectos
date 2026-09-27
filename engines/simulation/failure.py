"""The reliability evaluator: failure and resilience scenarios, judged with the Reliability Engine.

**Resilience changes** (replicas, redundancy, failover, independence, placement…) are evaluated by the
Reliability Engine itself, through its port, on the baseline and on the scenario's in-memory copy:
each entry point's path availability, baseline and scenario, as the engine estimates it (a ratio,
or unknown with what it lacks). The engine's model set and both result fingerprints are recorded.

**Failures** are judged with the Reliability Engine's dependency semantics (``dependency.role`` and
``closure``: required versus optional connections) and its declared redundancy (redundancy groups and
their minimum healthy members, failover modes). For each entry point, and for each component that
requires a failed element:

- ``unaffected``: nothing it requires or reaches optionally is unavailable;
- ``degraded``: only optional dependencies (asynchronous, publish/consume, ``critical: false``) are lost;
- ``tolerated``: every lost required element is covered — by another route the path still has, by
  declared alternatives in its redundancy group (still reachable, at least the group's declared minimum
  healthy, every member declaring ``failover_mode: automatic``), or by the declared placement that
  keeps a component running when one of its zones is lost;
- ``interrupted``: a lost required element nothing declared covers (no alternative, too few, or a
  ``manual`` or ``none`` failover);
- ``unknown``: whether it is covered is not declared (the group's minimum, a failover mode, the zone or
  region of a component) — the missing properties are named.

A failed component is wholly unavailable (all its replicas). Nothing is assumed: no failure
probability, no duration, no recovery time, no independence, no automatic failover unless declared,
and no outage prediction. Where the lost load goes is not modeled.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from core.architecture_ir.component import NodeKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.topology import Topology
from core.domain.engine_results import Evidence
from core.domain.reliability.analyses import ReliabilityAnalysisRequest
from core.domain.reliability.inputs import ComponentReliability
from core.domain.reliability.ports import ReliabilityEngine
from core.domain.reliability.results import ReliabilityResult, ReliabilityStatus
from core.domain.simulations.overlay import Overlay
from core.domain.simulations.results import AnalysisRun, Delta, EntryImpact
from core.domain.simulations.values import AnalysisKind, Impact, RunState
from core.domain.validation.options import RevisionInfo
from engines.reliability.dependency import closure
from engines.reliability.engine import OUT_OF_SCOPE

from .context import SimulationContext
from .engine import Evaluation, EvaluatorMeta
from .validation import ScenarioPlan

A = AnalysisKind
NOT_CALCULATED = {ReliabilityStatus.UNSUPPORTED, ReliabilityStatus.FAILED}
SHOWN = 20
AVAILABILITY_NOTE = (
    "Path availability reflects the scenario's configuration changes; its failures are judged per entry "
    "point, not folded into an availability figure."
)


def _names(ids: list[str]) -> str:
    listed = sorted(ids)
    head = ", ".join(listed[:SHOWN])
    return head if len(listed) <= SHOWN else f"{head} and {len(listed) - SHOWN} more"


@dataclass
class _Judgement:
    interrupted: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)

    def impact(self) -> Impact:
        if self.interrupted:
            return Impact.INTERRUPTED
        if self.unknown:
            return Impact.UNKNOWN
        if self.covered:
            return Impact.TOLERATED
        if self.optional:
            return Impact.DEGRADED
        return Impact.UNAFFECTED

    def explanation(self, entry: str) -> str:
        match self.impact():
            case Impact.INTERRUPTED:
                lost = _names(self.interrupted)
                return f"{entry} requires {lost}, unavailable with nothing declared to cover it."
            case Impact.UNKNOWN:
                return f"Whether the loss of {_names(self.unknown)} is covered for {entry} is not declared."
            case Impact.TOLERATED:
                return f"The loss of {_names(self.covered)} is covered for {entry} by what is declared."
            case Impact.DEGRADED:
                return f"{entry} loses only optional dependencies ({_names(self.optional)})."
            case _:
                return f"Nothing {entry} requires or reaches is unavailable."


class Failures:
    """The scenario's failures judged on one revision, with the Reliability Engine's semantics."""

    def __init__(self, ir: ArchitectureIR, overlay: Overlay) -> None:
        self.topology = Topology(ir)
        self.nodes = frozenset(overlay.unavailable_nodes)
        self.connections = frozenset(overlay.unavailable_connections)
        self.undetermined = frozenset(overlay.undetermined_nodes)
        self.zone_losses = frozenset(n for n, _ in overlay.zone_losses)
        self.facts = {n.id: ComponentReliability.of(n) for n in ir.nodes if n.kind is not NodeKind.BOUNDARY}
        groups: dict[str, list[str]] = defaultdict(list)
        for node_id, facts in self.facts.items():
            group = facts.known("redundancy_group")
            if isinstance(group, str):
                groups[group].append(node_id)
        self.groups = {g: tuple(sorted(ids)) for g, ids in groups.items()}

    @property
    def any(self) -> bool:
        return bool(self.nodes or self.connections or self.undetermined or self.zone_losses)

    def _cover(self, lost: str, reachable: frozenset[str], judgement: _Judgement, subject: str) -> None:
        """Whether declared alternatives of ``lost`` cover it for the path (``subject`` is reported)."""
        facts = self.facts.get(lost)
        group = facts.known("redundancy_group") if facts else None
        members = self.groups.get(group, ()) if isinstance(group, str) else ()
        alternatives = [m for m in members if m != lost and m not in self.nodes and m in reachable]
        if facts is None or not alternatives:
            judgement.interrupted.append(subject)
            return
        needed = facts.known("redundancy_group_min_healthy")
        if not isinstance(needed, int) or isinstance(needed, bool):
            judgement.unknown.append(subject)
            judgement.missing.append(f"{lost}.configuration.redundancy_group_min_healthy")
            return
        if len(alternatives) < needed:
            judgement.interrupted.append(subject)
            return
        modes = {m: self.facts[m].known("failover_mode") for m in (lost, *alternatives)}
        if any(mode in {"none", "manual"} for mode in modes.values()):
            judgement.interrupted.append(subject)
        elif any(mode is None for mode in modes.values()):
            judgement.unknown.append(subject)
            judgement.missing += [
                f"{m}.configuration.failover_mode" for m, mode in modes.items() if mode is None
            ]
        else:
            judgement.covered.append(subject)

    def judge(self, entry: str) -> EntryImpact:
        full = closure(self.topology, entry)
        cut = closure(self.topology, entry, self.nodes, self.connections)
        reachable = frozenset(cut.node_ids)
        judgement = _Judgement()
        for node_id in full.node_ids:
            if node_id == entry:
                continue
            if node_id in self.nodes:
                self._cover(node_id, reachable, judgement, node_id)
            elif node_id in self.undetermined:
                judgement.unknown.append(node_id)
                judgement.missing += [
                    f"{node_id}.configuration.availability_zones",
                    f"{node_id}.configuration.region",
                ]
            elif node_id in self.zone_losses:
                judgement.covered.append(node_id)  # keeps running in its other declared zones
        for connection_id in full.required:
            connection = self.topology.connection(connection_id)
            if (
                connection_id not in self.connections
                or connection is None
                or connection.target_id in self.nodes
            ):
                continue  # available, or judged with its unavailable target
            if connection.target_id in reachable:
                judgement.covered.append(connection_id)  # another required route still reaches it
            else:
                self._cover(connection.target_id, reachable, judgement, connection_id)
        for connection_id in full.optional:
            connection = self.topology.connection(connection_id)
            if connection_id in self.connections or (connection and connection.target_id in self.nodes):
                judgement.optional.append(connection_id)
        through = {*judgement.interrupted, *judgement.covered, *judgement.unknown, *judgement.optional}
        return EntryImpact(
            entry, judgement.impact(), judgement.explanation(entry), tuple(through), tuple(judgement.missing)
        )

    def components(self) -> dict[str, Impact]:
        """Each component that requires or reaches a failed element, and its impact."""
        found: dict[str, Impact] = {}
        for node in self.topology.ir.nodes:
            if node.kind in OUT_OF_SCOPE or node.id in self.nodes:
                continue
            impact = self.judge(node.id).impact
            if impact is not Impact.UNAFFECTED:
                found[node.id] = impact
        return found


def _entries(ir: ArchitectureIR, requested: tuple[str, ...] | None) -> tuple[str, ...]:
    if requested is not None:
        return requested
    return tuple(n.id for n in ir.nodes if n.kind is NodeKind.CLIENT)


def _availability(result: ReliabilityResult) -> dict[str, Decimal | None]:
    return {
        p.entry_id: p.availability.quantity.canonical if p.availability.quantity is not None else None
        for p in result.paths
    }


class ReliabilityEvaluator:
    meta = EvaluatorMeta(
        analysis=A.RELIABILITY,
        version=1,
        name="Reliability",
        description="Resilience changes evaluated by the Reliability Engine on the baseline and the "
        "scenario (path availability per entry point), and failures judged with its dependency and "
        "redundancy semantics (per entry point and component: unaffected, degraded, tolerated, "
        "interrupted or unknown).",
        unsupported=(
            "Failure probabilities, durations, recovery times and outage predictions.",
            "Where the load of an unavailable element goes.",
            "Latency, error rates, retries and timeouts during a failure.",
        ),
    )

    def __init__(self, engine: ReliabilityEngine) -> None:
        self._engine = engine

    def evaluate(
        self,
        context: SimulationContext,
        overlay: Overlay,
        plan: ScenarioPlan,
        earlier: Mapping[AnalysisKind, Evaluation],
    ) -> Evaluation:
        request = context.request
        asked = ReliabilityAnalysisRequest(
            request.architecture_id, request.revision_number, entries=request.entries
        )
        revision = context.revision
        scenario_revision = RevisionInfo(
            revision.architecture_id, revision.number, overlay.scenario_hash, revision.schema_version
        )
        baseline = self._engine.analyze(context.ir, revision, asked, context.requirements)
        projected = self._engine.analyze(overlay.architecture, scenario_revision, asked, context.requirements)
        before, after = _availability(baseline), _availability(projected)
        deltas = tuple(
            Delta(A.RELIABILITY, entry, "path.availability", "ratio", before.get(entry), after.get(entry))
            for entry in sorted(set(before) | set(after))
        )
        failures = Failures(context.ir, overlay)
        entries: tuple[EntryImpact, ...] = ()
        impacts: dict[str, Impact] = {}
        if failures.any:
            entries = tuple(failures.judge(e) for e in _entries(context.ir, request.entries))
            impacts = failures.components()
        calculated = baseline.status not in NOT_CALCULATED or projected.status not in NOT_CALCULATED
        if not calculated and not failures.any:
            message = "No reliability model applies to the components in scope."
            run = AnalysisRun(
                A.RELIABILITY, RunState.UNSUPPORTED, reason="no_reliability_model", message=message
            )
            return Evaluation(run)
        complete = (
            baseline.status is ReliabilityStatus.COMPLETED
            and projected.status is ReliabilityStatus.COMPLETED
            and not any(e.impact is Impact.UNKNOWN for e in entries)
        )
        run = AnalysisRun(
            A.RELIABILITY,
            RunState.COMPLETED if complete else RunState.PARTIAL,
            baseline.model_set,
            baseline.fingerprint,
            projected.fingerprint,
            message=f"Baseline {baseline.status.value}, scenario {projected.status.value}.",
        )
        trace = (
            Evidence("reliability.scenario_revision", scenario_revision.content_hash),
            *(Evidence(f"reliability.entry.{e.entry_id}", e.impact.value) for e in entries),
        )
        if failures.any and deltas:
            trace += (Evidence("reliability.availability", AVAILABILITY_NOTE),)
        return Evaluation(
            run,
            deltas=deltas,
            entries=entries,
            impacts=impacts,
            unsupported=tuple(projected.unsupported),
            trace=trace,
        )
