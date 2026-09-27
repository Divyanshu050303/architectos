"""Candidate impact analysis: each candidate evaluated, dimension by dimension, by the engines that
already model that dimension — no calculation of its own.

- **Capacity, reliability, cost**: the Simulation Engine evaluates the candidate's overlay as a
  scenario next to the unchanged baseline, in one run, with the same model versions and inputs: the
  workload and entries of the current capacity analysis, the pricing snapshot of the current cost
  analysis. Its runs and deltas are the impact; a run it could not make stays ``unsupported`` with
  its reason (no workload, no pricing, not concerned), never estimated.
- **Security, observability**: the engine analyzes the baseline and the overlay with the same
  request, policy and requirements; the findings the candidate resolves and introduces (by their
  stable ids) and the finding counts by severity are the impact.
- Only the dimensions the candidate's rule declares are evaluated. A candidate validation found
  invalid or unsupported is not evaluated (each dimension ``not_evaluated``, with the reason).

Each impact names its engine, model version, the baseline and candidate result fingerprints and the
stored analyses whose inputs it reused. Dimensions are never combined into one figure.
"""

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal

from core.architecture_ir.model import ArchitectureIR
from core.domain.engine_results import Unsupported
from core.domain.evolution.candidates import Candidate, EvidenceRef, Impact, ImpactChange, ImpactDelta
from core.domain.evolution.overlays import CandidateOverlay, apply_candidate
from core.domain.evolution.ports import ImpactInputs
from core.domain.evolution.values import EvidenceSource, ValidationState
from core.domain.observability.analyses import ObservabilityAnalysisRequest
from core.domain.observability.ports import ObservabilityEngine
from core.domain.observability.results import ObservabilityFinding, ObservabilityResult
from core.domain.requirements.value_objects import decimal_to_str
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.ports import SecurityEngine
from core.domain.security.results import SecurityFinding, SecurityResult
from core.domain.simulations.entities import SimulationRequest
from core.domain.simulations.errors import InvalidSimulationRequest
from core.domain.simulations.ports import SimulationEngine
from core.domain.simulations.results import Delta, SimulationResult
from core.domain.simulations.values import AnalysisKind
from core.domain.validation.options import RevisionInfo

from .rules import Registry

S = EvidenceSource
SIMULATED = {
    S.CAPACITY: AnalysisKind.CAPACITY,
    S.RELIABILITY: AnalysisKind.RELIABILITY,
    S.COST: AnalysisKind.COST,
}
NOT_ASSESSED = frozenset({ValidationState.INVALID, ValidationState.UNSUPPORTED})
type Findings = SecurityFinding | ObservabilityFinding


@dataclass(frozen=True, slots=True)
class ImpactEngines:
    simulation: SimulationEngine
    security: SecurityEngine
    observability: ObservabilityEngine


def _number(value: Decimal | None) -> str | None:
    return decimal_to_str(value) if value is not None else None


def _delta(delta: Delta) -> ImpactDelta:
    return ImpactDelta(
        delta.element_id,
        delta.metric,
        delta.unit,
        _number(delta.baseline),
        _number(delta.scenario),
        _number(delta.difference),
        _number(delta.percentage),
        delta.comparable,
        delta.note,
    )


def _missing(unsupported: Iterable[Unsupported], analysis: AnalysisKind) -> tuple[str, ...]:
    """What the analysis could not calculate on the candidate, and what would decide it."""
    return tuple(
        sorted(
            {
                m
                for u in unsupported
                if u.code.startswith(analysis.value)
                for m in (u.missing or (f"{u.element_id}: {u.code}",))
            }
        )
    )


class Assessor:
    """Evaluates candidates against one baseline; the baseline's own security and observability
    results are computed once."""

    def __init__(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        architecture_id: uuid.UUID,
        inputs: ImpactInputs,
        engines: ImpactEngines,
        registry: Registry,
    ) -> None:
        self._ir, self._revision, self._architecture_id = ir, revision, architecture_id
        self._inputs, self._engines = inputs, engines
        self._rules = {r.meta.id: r.meta for r in registry.rules()}
        self._security: SecurityResult | None = None
        self._observability: ObservabilityResult | None = None

    def assess(self, candidate: Candidate) -> Candidate:
        meta = self._rules.get(candidate.rule.id)
        dimensions = tuple(sorted(set(meta.analyses), key=lambda d: d.value)) if meta else ()
        if candidate.validation in NOT_ASSESSED:
            reason = f"candidate_{candidate.validation.value}"
            return replace(
                candidate, impacts=tuple(Impact(d, "not_evaluated", d, reason) for d in dimensions)
            )
        overlay = apply_candidate(self._ir, candidate)
        impacts: list[Impact] = []
        simulated = [d for d in dimensions if d in SIMULATED]
        if simulated:
            impacts += self._simulate(overlay, simulated)
        if S.SECURITY in dimensions:
            impacts.append(self._security_impact(overlay))
        if S.OBSERVABILITY in dimensions:
            impacts.append(self._observability_impact(overlay))
        return replace(candidate, impacts=tuple(impacts))

    # --- capacity, reliability, cost: the Simulation Engine ---------------------------------------

    def _inputs_of(self, dimension: EvidenceSource) -> tuple[EvidenceRef, ...]:
        refs = {
            S.CAPACITY: (self._inputs.capacity,),
            S.RELIABILITY: (),
            S.COST: (self._inputs.capacity, self._inputs.cost),
        }[dimension]
        return tuple(r for r in refs if r is not None)

    def _simulate(self, overlay: CandidateOverlay, dimensions: list[EvidenceSource]) -> list[Impact]:
        inputs = self._inputs
        request = SimulationRequest(
            self._architecture_id,
            self._revision.number,
            overlay.scenario(),
            analyses=tuple(SIMULATED[d] for d in dimensions),
            workload=inputs.workload,
            entries=inputs.entries,
            pricing=inputs.pricing,
        )
        try:
            output = self._engines.simulation.simulate(
                self._ir,
                self._revision,
                request,
                inputs.requirements,
                inputs.snapshot,
                inputs.provider,
                inputs.currency,
            )
        except InvalidSimulationRequest as error:  # e.g. a property no simulated engine reads
            reason = str(error.details.get("reason") or "not_simulated")
            return [
                Impact(d, "unsupported", S.SIMULATION, reason, inputs=self._inputs_of(d)) for d in dimensions
            ]
        return [self._run(output.result, d) for d in dimensions]

    def _run(self, result: SimulationResult, dimension: EvidenceSource) -> Impact:
        analysis = SIMULATED[dimension]
        run = next(r for r in result.runs if r.analysis is analysis)
        missing = _missing(result.unsupported, analysis)
        if run.reason == "no_workload":
            missing += ("the workload of a current capacity analysis",)
        if run.reason in ("no_pricing", "no_currency"):
            missing += ("a current cost analysis (its pricing snapshot) and the project's currency",)
        return Impact(
            dimension,
            run.state.value,
            S.SIMULATION,
            run.reason,
            run.baseline_fingerprint,
            run.scenario_fingerprint,
            run.model_set.version if run.model_set else None,
            self._inputs_of(dimension),
            tuple(_delta(d) for d in result.deltas if d.analysis is analysis),
            (),
            tuple(a for a in result.assumptions if a.label.startswith(f"{analysis.value}.")),
            missing,
        )

    # --- security, observability: the engine on both sides ---------------------------------------

    def _security_impact(self, overlay: CandidateOverlay) -> Impact:
        request = SecurityAnalysisRequest(self._architecture_id, self._revision.number)
        inputs = self._inputs
        if self._security is None:
            self._security = self._engines.security.analyze(
                self._ir, self._revision, request, inputs.policy, inputs.requirements
            )
        proposed = self._engines.security.analyze(
            overlay.architecture,
            self._candidate_revision(overlay),
            request,
            inputs.policy,
            inputs.requirements,
        )
        return _findings_impact(
            S.SECURITY,
            proposed.status.value,
            (self._security.fingerprint, proposed.fingerprint),
            proposed.analyzer_set.version,
            self._security.findings,
            proposed.findings,
        )

    def _observability_impact(self, overlay: CandidateOverlay) -> Impact:
        request = ObservabilityAnalysisRequest(self._architecture_id, self._revision.number)
        inputs = self._inputs
        if self._observability is None:
            self._observability = self._engines.observability.analyze(
                self._ir, self._revision, request, inputs.policy, inputs.requirements
            )
        proposed = self._engines.observability.analyze(
            overlay.architecture,
            self._candidate_revision(overlay),
            request,
            inputs.policy,
            inputs.requirements,
        )
        return _findings_impact(
            S.OBSERVABILITY,
            proposed.status.value,
            (self._observability.fingerprint, proposed.fingerprint),
            proposed.analyzer_set.version,
            self._observability.findings,
            proposed.findings,
        )

    def _candidate_revision(self, overlay: CandidateOverlay) -> RevisionInfo:
        return replace(self._revision, content_hash=overlay.content_hash)


def _findings_impact(
    dimension: EvidenceSource,
    state: str,
    fingerprints: tuple[str, str],
    model_version: str,
    before: Iterable[Findings],
    after: Iterable[Findings],
) -> Impact:
    old = {f.id: f for f in before}
    new = {f.id: f for f in after}

    def change(kind: str, f: Findings) -> ImpactChange:
        return ImpactChange(kind, f.id, f.type.value, (*f.node_ids, *f.connection_ids), f.severity.value)

    changes = [change("resolved", old[i]) for i in old.keys() - new.keys()]
    changes += [change("introduced", new[i]) for i in new.keys() - old.keys()]
    counts = Counter(f.severity.value for f in old.values()), Counter(f.severity.value for f in new.values())
    deltas = tuple(
        ImpactDelta(
            "system",
            f"findings.{severity}",
            "findings",
            str(counts[0][severity]),
            str(counts[1][severity]),
            str(counts[1][severity] - counts[0][severity]),
        )
        for severity in sorted(set(counts[0]) | set(counts[1]))
    )
    return Impact(
        dimension,
        state,
        dimension,
        None,
        fingerprints[0],
        fingerprints[1],
        model_version,
        deltas=deltas,
        changes=tuple(changes),
    )
