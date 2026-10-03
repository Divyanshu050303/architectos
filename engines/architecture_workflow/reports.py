"""Capacity, cost and simulation results as review-package reports (validation, reliability, security
and observability use the architecture agent's ``validation_report`` and ``analysis_report``).

Every value is the engine's own; nothing here scores or estimates.

- **Capacity** findings are its bottlenecks, by ``node:resource``. Their severity is a fixed reading of
  the engine's condition, and only a *modeled* bottleneck (demand and capacity both known) is ``high``
  or ``critical`` — so only modeled bottlenecks can trigger an improvement:

  ============================  =========  =========================
  condition                     modeled    not modeled (a candidate)
  ============================  =========  =========================
  exceeds_capacity, no_capacity critical   info
  at_capacity                   high       info
  above_target                  medium     info
  unknown_capacity              info       info
  ============================  =========  =========================

- **Cost** has no findings (its unsupported items have no stable id): its summary is its status and,
  only when the candidate could be priced, its known monthly total — a lower bound when partial.
- **Simulation** has no findings: its summary counts what it evaluated; its deltas are its own.
"""

import uuid
from decimal import Decimal

from core.domain.architecture_agent.results import MAX_FINDINGS, AgentFinding, EngineReport
from core.domain.architecture_agent.values import EngineStatus
from core.domain.capacity.results import BottleneckCondition, CapacityResult, Certainty
from core.domain.cost.aggregation import summarize
from core.domain.cost.ports import CostEngineOutput
from core.domain.cost.results import CostStatus
from core.domain.simulations.ports import SimulationOutput

C = BottleneckCondition
SEVERITY = {
    C.EXCEEDS_CAPACITY: "critical",
    C.NO_CAPACITY: "critical",
    C.AT_CAPACITY: "high",
    C.ABOVE_TARGET: "medium",
    C.UNKNOWN_CAPACITY: "info",
}
PRICED = frozenset({CostStatus.COMPLETED, CostStatus.PARTIAL})


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def capacity_report(result: CapacityResult, analysis_id: uuid.UUID) -> EngineReport:
    findings = [
        AgentFinding(
            "capacity",
            f"{b.node_id}:{b.resource}"[:128],
            SEVERITY[b.condition] if b.certainty is Certainty.MODELED else "info",
            f"{b.condition.value}: {b.explanation}"[:2000],
            (b.node_id,),
        )
        for b in result.bottlenecks
    ]
    limitations = [f"Analyzed with the workload of capacity analysis {analysis_id}."]
    limitations += [x.message for x in result.limitations]
    if len(findings) > MAX_FINDINGS:
        limitations.append(f"{len(findings) - MAX_FINDINGS} more bottleneck(s) are not listed here.")
    return EngineReport(
        "capacity",
        EngineStatus.EVALUATED,
        {"models": result.model_set.to_dict()},
        tuple(findings[:MAX_FINDINGS]),
        result.summary.to_dict(),
        tuple(limitations),
    )


def cost_report(output: CostEngineOutput, analysis_id: uuid.UUID) -> EngineReport:
    status = output.summary.status
    totals = summarize(output.result).totals
    complete = totals.unknown_items == 0 and totals.unsupported == 0
    summary: dict[str, object] = {"status": status.value, "currency": totals.currency}
    limitations = [
        f"Priced with the inputs of cost analysis {analysis_id}, at the candidate's declared resources."
    ]
    if status in PRICED:  # a 0 from "nothing could be priced" is not a cost
        summary["known_monthly_total"] = _decimal(totals.monthly.amount)
        summary["complete"] = complete
        if not complete:
            limitations.append(
                "The known monthly total is a lower bound: some lines are unknown or unsupported."
            )
    else:
        limitations.append("No monthly total: the candidate could not be priced.")
    versions = {"models": output.result.model_set.to_dict(), "snapshot_hash": output.result.snapshot_hash}
    return EngineReport("cost", EngineStatus.EVALUATED, versions, (), summary, tuple(limitations))


def simulation_report(output: SimulationOutput) -> EngineReport:
    result = output.result
    summary = {
        "components": len(result.components),
        "entries": len(result.entries),
        "deltas": len(result.deltas),
        "unsupported": len(result.unsupported),
    }
    limitations = tuple(x.message for x in result.limitations)
    return EngineReport(
        "simulation",
        EngineStatus.EVALUATED,
        {"engine": result.engine_set.to_dict()},
        (),
        summary,
        limitations,
    )
