"""Engine integration: the other engines' stored analyses brought into a migration plan through their
own contracts — never recomputed, never invented.

Each analysis is matched to the plan by the revision and content hash it analyzed: of the source,
of the target (a later revision), or of neither — stale, reported and never used. For each engine
and side the plan states its coverage (current, stale, missing, or unsupported: simulation compares
scenarios of one revision, and a candidate's overlay is analyzed by no engine — the candidate's own
evidence is cited instead).

From current analyses only:

- the target's **validation, capacity, security and observability checkpoints** are evaluated (pass,
  warning or fail, with the analysis as evidence and what it states as the actual condition); a
  partial analysis never passes, it warns with what it did not model; with only stale analyses a
  checkpoint cannot be evaluated; with none it stays not run;
- security and observability findings of the target are compared with the source's: a finding the
  source did not have is a regression — never judged without the source's analysis;
- the target's **reliability** is checked against its requirements, and modeled availability
  compared with the source's, entry by entry;
- **capacity prerequisites** are the target analysis's scaling options and unscaled bottlenecks;
- a **cost** change is stated only when both sides were priced with the same snapshot and currency
  and both totals are complete — otherwise the comparison is withheld, with the reason.

Every imported item names its analysis; a dimension without current target evidence is a
``missing_evidence`` finding (never blocking: the plan states what it could not check).
"""

import dataclasses
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.domain.evolution.candidates import Candidate, EvidenceRef
from core.domain.evolution.evidence import EvidenceItem, StoredAnalysis
from core.domain.evolution.triggers import TriggerKind
from core.domain.evolution.values import EvidenceSource, EvidenceState
from core.domain.migrations.evidence import (
    DIMENSIONS,
    AnalysisEvidence,
    CoverageState,
    EvidenceCoverage,
    Side,
)
from core.domain.migrations.plans import MigrationProposal, PlanFinding
from core.domain.migrations.steps import Checkpoint, Risk, Trace
from core.domain.migrations.values import (
    CheckpointBasis,
    CheckpointStatus,
    FindingType,
    RiskCategory,
    RiskStatus,
    TargetKind,
    TraceKind,
)

E, CS, P = EvidenceSource, CoverageState, CheckpointStatus
SIMULATION_NOTE = (
    "A simulation compares scenarios of one revision; a source and a target revision are not a "
    "supported scenario."
)
CANDIDATE_NOTE = (
    "The target is an evolution candidate's overlay, which no engine analyzes; the candidate's own "
    "evidence is cited."
)
STALE_NOTE = "Only analyses of other revisions or content exist: reported, never used."
SEVERE = ("critical", "high")
MAX_LISTED = 10


def _listed(values: Sequence[str]) -> str:
    shown = ", ".join(values[:MAX_LISTED])
    return shown + (f" and {len(values) - MAX_LISTED} more" if len(values) > MAX_LISTED else "")


def _trace(stored: StoredAnalysis, item: str | None = None) -> Trace:
    return Trace(TraceKind.EVIDENCE, f"{stored.source.value}:{stored.analysis_id}", item)


def _decimal(value: str | None) -> Decimal:
    try:
        return Decimal(value) if value is not None else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


@dataclass(frozen=True, slots=True)
class _Matched:
    """The analyses of one engine: the current one of each side, and the stale ones."""

    source: AnalysisEvidence | None
    target: AnalysisEvidence | None
    stale: tuple[AnalysisEvidence, ...]

    def new_findings(self) -> list[EvidenceItem]:
        """The target's evaluable findings the source's analysis does not have."""
        if self.source is None or self.target is None:
            return []
        before = {(i.code, i.element_id) for i in self.source.stored.items if i.evaluable}
        return [i for i in self.target.stored.items if i.evaluable and (i.code, i.element_id) not in before]

    def lower_availability(self) -> list[str]:
        if self.source is None or self.target is None:
            return []
        before = {f.label: f.value for f in self.source.stored.facts if f.label.startswith("availability.")}
        return [
            f"{f.label.removeprefix('availability.')} {before[f.label]} to {f.value}"
            for f in self.target.stored.facts
            if f.label in before and _decimal(f.value) < _decimal(before[f.label])
        ]


def _evaluated(
    checkpoint: Checkpoint, status: CheckpointStatus, actual: str, *used: AnalysisEvidence | None
) -> Checkpoint:
    found = [a.stored for a in used if a is not None]
    partial = sorted({m for s in found for m in s.missing})
    if status is P.PASS and partial:
        status, actual = P.WARNING, f"{actual} Partial: not modeled — {_listed(partial)}."
    return dataclasses.replace(
        checkpoint,
        status=status,
        basis=CheckpointBasis.MODELED,
        actual=actual,
        evidence=tuple(s.ref(EvidenceState.CURRENT) for s in found),
        traces=(*checkpoint.traces, *(_trace(s) for s in found)),
    )


def _validation(checkpoint: Checkpoint, target: AnalysisEvidence, matched: _Matched) -> Checkpoint:
    by_severity = target.summary.get("by_severity") or {}
    severe = sum(int(by_severity.get(s, 0)) for s in SEVERE)
    blocking = int(target.summary.get("blocking") or 0)
    total = int(target.summary.get("total") or len(target.stored.items))
    if blocking or severe:
        actual = f"{blocking} blocking and {severe} critical or high finding(s)."
        return _evaluated(checkpoint, P.FAIL, actual, target)
    if total:
        return _evaluated(checkpoint, P.WARNING, f"{total} finding(s), none blocking or high.", target)
    return _evaluated(checkpoint, P.PASS, "No validation finding.", target)


def _capacity(checkpoint: Checkpoint, target: AnalysisEvidence, matched: _Matched) -> Checkpoint:
    items = target.stored.items
    unscaled = [f"{i.element_id} {i.code}" for i in items if i.kind is TriggerKind.SCALING_UNSUPPORTED]
    scaling = [f"{i.element_id} {i.code}" for i in items if i.kind is TriggerKind.SCALING_OPTION]
    if unscaled:
        actual = f"Over capacity with no modeled scaling: {_listed(unscaled)}."
        return _evaluated(checkpoint, P.FAIL, actual, target)
    if scaling:
        return _evaluated(checkpoint, P.WARNING, f"Needs scaling as modeled: {_listed(scaling)}.", target)
    return _evaluated(checkpoint, P.PASS, "No component over its modeled capacity.", target)


def _regressions(checkpoint: Checkpoint, target: AnalysisEvidence, matched: _Matched) -> Checkpoint:
    found = [i for i in target.stored.items if i.evaluable]
    if matched.source is None:
        actual = f"{len(found)} finding(s) in the target; no current analysis of the source to compare with."
        return _evaluated(checkpoint, P.WARNING if found else P.PASS, actual, target)
    new = matched.new_findings()
    if new:
        listed = _listed([f"{i.code} on {i.element_id}" for i in new])
        actual = f"Finding(s) the source did not have: {listed}."
        return _evaluated(checkpoint, P.FAIL, actual, target, matched.source)
    return _evaluated(checkpoint, P.PASS, "No finding the source did not have.", target, matched.source)


EVALUATORS = {
    "validation": _validation,
    "capacity": _capacity,
    "security": _regressions,
    "observability": _regressions,
}


class _Integration:
    def __init__(self, proposal: MigrationProposal, analyses: Sequence[AnalysisEvidence]) -> None:
        self.proposal = proposal
        self.candidate = proposal.target.kind is TargetKind.CANDIDATE
        usable = [a for a in analyses if a.stored.status != "failed"]  # a failed analysis is never evidence
        self.matched = {s: self.match([a for a in usable if a.stored.source is s]) for s in DIMENSIONS}
        self.notes: dict[tuple[EvidenceSource, Side], str] = {}

    def sides(self, stored: StoredAnalysis) -> set[Side]:
        source, target = self.proposal.source, self.proposal.target
        analyzed = (stored.revision_number, stored.content_hash)
        found = set()
        if analyzed == (source.revision_number, source.content_hash):
            found.add(Side.SOURCE)
        if not self.candidate and analyzed == (target.revision_number, target.content_hash):
            found.add(Side.TARGET)
        return found

    def match(self, analyses: list[AnalysisEvidence]) -> _Matched:
        source = next((a for a in analyses if Side.SOURCE in self.sides(a.stored)), None)
        target = next((a for a in analyses if Side.TARGET in self.sides(a.stored)), None)
        return _Matched(source, target, tuple(a for a in analyses if not self.sides(a.stored)))

    # --- evidence and coverage ------------------------------------------------------------------

    def evidence(self, candidate: Candidate | None) -> tuple[EvidenceRef, ...]:
        refs = []
        for matched in self.matched.values():
            refs += [a.stored.ref(EvidenceState.CURRENT) for a in (matched.source, matched.target) if a]
            refs += [a.stored.ref(EvidenceState.STALE) for a in matched.stale]
        if candidate is not None:  # the candidate's own evidence, as it cites it
            refs += list(candidate.evidence)
        return tuple(refs)

    def coverage(self) -> Iterator[EvidenceCoverage]:
        for source, matched in self.matched.items():
            stale = tuple(sorted(str(a.stored.analysis_id) for a in matched.stale))
            for side, found in ((Side.SOURCE, matched.source), (Side.TARGET, matched.target)):
                if source is E.SIMULATION:
                    yield EvidenceCoverage(source, side, CS.UNSUPPORTED, note=SIMULATION_NOTE)
                elif side is Side.TARGET and self.candidate:
                    yield EvidenceCoverage(source, side, CS.UNSUPPORTED, note=CANDIDATE_NOTE)
                elif found is not None:
                    analysis = (str(found.stored.analysis_id),)
                    yield EvidenceCoverage(source, side, CS.CURRENT, analysis, self.notes.get((source, side)))
                elif stale:
                    yield EvidenceCoverage(source, side, CS.STALE, stale, STALE_NOTE)
                else:
                    yield EvidenceCoverage(source, side, CS.MISSING)

    def missing(self) -> Iterator[PlanFinding]:
        if self.candidate or not self.proposal.steps:
            return
        revision = self.proposal.target.revision_number
        for source, matched in self.matched.items():
            if source is E.SIMULATION or matched.target is not None:
                continue
            state = "only stale analyses exist" if matched.stale else "none exists"
            yield PlanFinding(
                FindingType.MISSING_EVIDENCE,
                f"evidence:{source.value}",
                f"No current {source.value} analysis of the target revision ({state}): what it would "
                "check is not checked.",
                missing=(f"A {source.value} analysis of target revision {revision}.",),
            )

    # --- checkpoints ----------------------------------------------------------------------------

    def checkpoint(self, checkpoint: Checkpoint) -> Checkpoint:
        engine, _, side = checkpoint.key.partition(":")
        if side != "target" or engine not in EVALUATORS:
            return checkpoint
        matched = self.matched[E(engine)]
        if matched.target is not None:
            return EVALUATORS[engine](checkpoint, matched.target, matched)
        if self.candidate:
            return dataclasses.replace(checkpoint, status=P.CANNOT_EVALUATE, actual=CANDIDATE_NOTE)
        if matched.stale:
            stale = tuple(a.stored.ref(EvidenceState.STALE) for a in matched.stale)
            return dataclasses.replace(
                checkpoint, status=P.CANNOT_EVALUATE, actual=STALE_NOTE, evidence=stale
            )
        return checkpoint  # not run: no analysis of the target exists

    def reliability(self) -> Iterator[Checkpoint]:
        matched = self.matched[E.RELIABILITY]
        target = matched.target
        if target is None:
            return
        base = Checkpoint(
            "reliability:target",
            "The target's reliability under its modeled redundancy and recovery.",
            "No reliability requirement is violated, and no entry's modeled availability is lower than "
            "the source's.",
            P.NOT_RUN,
            CheckpointBasis.MODELED,
            (_trace(target.stored),),
            blocking=False,
        )
        violated = [c.requirement_id for c in target.stored.checks if c.verdict == "violated"]
        lower = matched.lower_availability()
        if violated:
            yield _evaluated(base, P.FAIL, f"Violated requirement(s): {_listed(violated)}.", target)
        elif lower:
            actual = f"Lower modeled availability: {_listed(lower)}."
            yield _evaluated(base, P.WARNING, actual, target, matched.source)
        else:
            compared = " than the source's" if matched.source else " (no source analysis to compare with)"
            actual = f"No requirement violated; no lower modeled availability{compared}."
            yield _evaluated(base, P.PASS, actual, target, matched.source)

    # --- risks ----------------------------------------------------------------------------------

    def risks(self) -> Iterator[Risk]:
        yield from self.capacity_risks()
        yield from self.cost_risk()
        for source, category in (
            (E.SECURITY, RiskCategory.SECURITY_REGRESSION),
            (E.OBSERVABILITY, RiskCategory.OBSERVABILITY_GAP),
        ):
            matched = self.matched[source]
            if matched.target is None:
                continue
            for item in matched.new_findings():
                yield Risk(
                    f"{source.value}:finding:{item.item}"[:128],
                    category,
                    RiskStatus.CONFIRMED,
                    f"The target has a {source.value} finding the source does not: {item.code} on "
                    f"{item.element_id}.",
                    item.message or f"The {source.value} analysis of the target reports {item.code}.",
                    (_trace(matched.target.stored, item.item),),
                    (item.element_id,),
                    mitigation=item.message or "Review the finding before approval.",
                )

    def capacity_risks(self) -> Iterator[Risk]:
        target = self.matched[E.CAPACITY].target
        if target is None:
            return
        for item in target.stored.items:
            if item.kind not in {TriggerKind.SCALING_OPTION, TriggerKind.SCALING_UNSUPPORTED}:
                continue
            facts = {f.label: f.value for f in item.facts}
            modeled = (
                f" (current {facts['current']}, required {facts['required']} {facts.get('unit', '')})"
                if "current" in facts and "required" in facts
                else ""
            )
            unscaled = item.kind is TriggerKind.SCALING_UNSUPPORTED
            fallback = (
                "No modeled scaling covers it: review the target."
                if unscaled
                else "Scale it as the capacity analysis models, before the cutover."
            )
            yield Risk(
                f"capacity:target:{item.element_id}:{item.code}"[:128],
                RiskCategory.CAPACITY_EXHAUSTION,
                RiskStatus.CONFIRMED,
                f"The target's {item.code} of {item.element_id} is over its modeled capacity"
                f"{modeled.rstrip()}.",
                f"{item.element_id} is overloaded under the modeled workload after the migration.",
                (_trace(target.stored, item.item),),
                (item.element_id,),
                mitigation=item.message or fallback,
            )

    def cost_risk(self) -> Iterator[Risk]:
        matched = self.matched[E.COST]
        before, after = matched.source, matched.target
        if before is None or after is None:
            return
        reason = _incomparable(before, after)
        if reason:
            self.notes[(E.COST, Side.TARGET)] = f"Not compared with the source: {reason}."
            return
        old, new = before.stored.fact("monthly"), after.stored.fact("monthly")
        if _decimal(new) > _decimal(old):
            yield Risk(
                "cost:target",
                RiskCategory.COST_INCREASE,
                RiskStatus.CONFIRMED,
                f"The modeled monthly cost rises from {old} to {new} {after.stored.fact('currency')}, with "
                "the same pricing snapshot.",
                "Operating cost rises after the migration, as modeled.",
                (_trace(before.stored), _trace(after.stored)),
                mitigation="Review the cost analyses of the source and target before approval.",
            )


def _incomparable(before: AnalysisEvidence, after: AnalysisEvidence) -> str | None:
    """Why two cost analyses cannot be compared, or None: never across assumptions."""
    if before.inputs.get("snapshot_id") != after.inputs.get("snapshot_id"):
        return "they were priced with different pricing snapshots"
    if before.stored.fact("currency") != after.stored.fact("currency"):
        return "they are in different currencies"
    if before.stored.fact("complete") != "true" or after.stored.fact("complete") != "true":
        return "a total is incomplete"
    if before.stored.fact("monthly") is None or after.stored.fact("monthly") is None:
        return "a total is unknown"
    return None


def integrate(
    proposal: MigrationProposal,
    analyses: Sequence[AnalysisEvidence],
    candidate: Candidate | None = None,
) -> MigrationProposal:
    """The proposal with the engines' stored analyses as evidence: coverage, evaluated checkpoints,
    evidenced risks and the dimensions it could not check."""
    found = _Integration(proposal, analyses)
    checkpoints = (*(found.checkpoint(c) for c in proposal.checkpoints), *found.reliability())
    risks = (*proposal.risks, *found.risks())  # before the coverage: a withheld comparison notes why
    coverage = tuple(found.coverage())
    return dataclasses.replace(
        proposal,
        evidence=(*proposal.evidence, *found.evidence(candidate)),
        coverage=coverage,
        checkpoints=checkpoints,
        risks=risks,
        findings=(*proposal.findings, *found.missing()),
    )
