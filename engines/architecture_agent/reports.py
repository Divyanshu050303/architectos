"""What the deterministic engines said about a candidate, as review-package reports.

Every report is the engine's own: its findings (rule or finding id, severity, its title and
explanation, the elements concerned), its own counts (``summary``, never a score), the versions of
what ran, and its limitations. Nothing here judges, scores or rewrites a finding.

Engines a candidate cannot be analyzed by are reported as **not evaluated**, with why — never as
passing, never as zero:

- **capacity** needs a workload profile (traffic, payloads, ratios), stated per analysis;
- **cost** needs a pricing snapshot and a capacity analysis;
- **simulation** needs a workload and scenarios.

They are run on the architecture once it is accepted, from its revision.
"""

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from core.domain.architecture_agent.results import MAX_FINDINGS, AgentFinding, EngineReport
from core.domain.architecture_agent.values import EngineStatus
from core.domain.engine_results import ModelSet
from core.domain.validation.results import ValidationResult

ENGINE_ERROR = "engine_error"
NOT_EVALUATED = {
    "capacity": (
        "Capacity needs a workload profile (traffic, payloads, ratios), which a candidate does not have: "
        "analyze capacity on the accepted architecture with a stated workload."
    ),
    "cost": (
        "Cost needs a pricing snapshot and a capacity analysis: estimate cost on the accepted "
        "architecture. No price is assumed."
    ),
    "simulation": "Simulation needs a workload and scenarios: simulate the accepted architecture.",
}


class _Analysis(Protocol):
    """Reliability, security and observability results: findings of the same shape."""

    @property
    def findings(self) -> Sequence[Any]: ...
    @property
    def limitations(self) -> Sequence[Any]: ...
    def summary(self) -> dict[str, Any]: ...


def _message(title: str, explanation: str) -> str:
    return f"{title}: {explanation}"[:2000]


def _bounded(findings: list[AgentFinding], limitations: list[str]) -> tuple[AgentFinding, ...]:
    if len(findings) > MAX_FINDINGS:
        limitations.append(f"{len(findings) - MAX_FINDINGS} more finding(s) are not listed here.")
    return tuple(findings[:MAX_FINDINGS])


def validation_report(result: ValidationResult) -> EngineReport:
    findings = [
        AgentFinding(
            "validation",
            f"{f.rule_id}:{f.code}"[:128],
            f.severity.value,
            _message(f.title, f.explanation),
            tuple(f.entity_ids),
        )
        for f in result.findings
    ]
    limitations = [x.message for x in result.limitations]
    limitations += [f"Rule {f.rule_id} could not run." for f in result.failures]
    return EngineReport(
        "validation",
        EngineStatus.EVALUATED,
        {"rule_set": result.rule_set.to_dict()},
        _bounded(findings, limitations),
        result.summary.to_dict(),
        tuple(limitations),
    )


def analysis_report(engine: str, result: _Analysis, models: ModelSet) -> EngineReport:
    findings = [
        AgentFinding(
            engine,
            str(f.id)[:128],
            f.severity.value,
            _message(f.title, f.explanation),
            (*f.node_ids, *f.connection_ids),
        )
        for f in result.findings
    ]
    limitations = [x.message for x in result.limitations]
    return EngineReport(
        engine,
        EngineStatus.EVALUATED,
        {"models": models.to_dict()},
        _bounded(findings, limitations),
        result.summary(),
        tuple(limitations),
    )


def failed_report(engine: str) -> EngineReport:
    """The engine could not run on the candidate: said, with no findings (never 'none found')."""
    reason = f"The {engine} engine could not analyze this candidate; its absence of findings means nothing."
    return EngineReport(engine, EngineStatus.FAILED, limitations=(reason,), error=ENGINE_ERROR)


def not_evaluated_reports() -> tuple[EngineReport, ...]:
    return tuple(
        EngineReport(engine, EngineStatus.NOT_EVALUATED, limitations=(reason,))
        for engine, reason in NOT_EVALUATED.items()
    )


def validation_blocking(reports: Iterable[EngineReport]) -> int | None:
    """The validation engine's blocking findings; None when validation did not evaluate (which is
    never read as 'none')."""
    for report in reports:
        if report.engine == "validation" and report.status is EngineStatus.EVALUATED:
            value = report.summary.get("blocking")
            return value if isinstance(value, int) and not isinstance(value, bool) else None
    return None
