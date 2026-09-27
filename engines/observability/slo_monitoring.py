"""SLO and monitoring requirement traceability: whether the architecture models what it would take
to *measure* and *alert on* each in-force objective — never whether the objective is met.
Attainment needs runtime measurements this engine never collects or invents.

**Objectives** are in-force ``availability``, ``reliability`` and ``performance`` requirements with a
structured constraint. Its metric maps, by the fixed table ``INDICATORS``, to the metric kinds that
measure it:

- ``availability`` → ``availability`` or ``errors``;
- ``latency`` → ``latency``;
- ``requests_per_second``, ``orders_per_second`` → ``throughput``;
- anything else (``durability``, ``rpo``, ``rto``, …) → unsupported (``not_verifiable``, never passed).

Each objective gets two checks: ``requirement.<ref>.objective_measurable`` (a concerned component
declares a metric of those kinds, collected by a modeled path) and ``….objective_alerted`` (an alert
rule on those kinds — or on ``health`` for availability — with a modeled delivery path).

**Monitoring requirements** (``operational`` requirements of category ``monitoring``) are stated in
words; they map to fixed conditions by the documented keyword table ``MONITORING`` (a requirement
collects every entry it matches), never by a language model. The entry used is recorded with the
verdict (``mapping``). Words matching no entry: ``unsupported``, ``not_verifiable``.

On what: the components that reference the requirement, else those of its scope (``service``,
``api``, ``database``, ``queue``, ``data``), else (``system``) the components declared critical;
``user`` and ``region`` scopes are not modeled and are unsupported. A violation takes the
requirement's priority as severity (validation's convention).
"""

import re
from dataclasses import dataclass

from core.architecture_ir.node import Node
from core.domain.checks import CheckSource, Subject
from core.domain.observability.results import (
    CheckResult,
    Condition,
    FindingCategory,
    FindingType,
    ObservabilityFinding,
)
from core.domain.requirements.entities import Requirement
from core.domain.requirements.enums import RequirementScope, RequirementType
from core.domain.validation.results import Verdict
from engines.validation.rules.requirements import SCOPE_KINDS, SEVERITY_BY_PRIORITY

from .conditions import Ask, judge, report
from .context import ObservabilityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress

C = Condition
OBJECTIVE_TYPES = frozenset(
    {RequirementType.AVAILABILITY, RequirementType.RELIABILITY, RequirementType.PERFORMANCE}
)
# An objective's metric -> the metric kinds that measure it.
INDICATORS: dict[str, frozenset[str]] = {
    "availability": frozenset({"availability", "errors"}),
    "latency": frozenset({"latency"}),
    "requests_per_second": frozenset({"throughput"}),
    "orders_per_second": frozenset({"throughput"}),
}
CRITICAL = "critical"  # a system requirement: the components declared critical


@dataclass(frozen=True, slots=True)
class Entry:
    pattern: re.Pattern[str]
    condition: Condition


def _entry(pattern: str, condition: Condition) -> Entry:
    return Entry(re.compile(pattern, re.IGNORECASE), condition)


# The documented mapping for monitoring requirements, in order.
MONITORING: tuple[Entry, ...] = (
    _entry(r"\bhealth[\s-]*checks?\b|\bhealth\s+endpoints?\b|\bprobes?\b", C.HEALTH_CHECKS_ON_CRITICAL),
    _entry(r"\balert\w*|\bpag(e|ed|ing)\b", C.ALERTING_ON_CRITICAL),
    _entry(r"\blog(s|ged|ging)?\b", C.LOGS_ON_CRITICAL),
    _entry(r"\b(structured|json)\s+log\w*", C.STRUCTURED_LOGS),
    _entry(r"\bcorrelat\w*|\brequest[\s-]+ids?\b", C.CORRELATION_IDS),
    _entry(r"\bmetrics?\b", C.METRICS_ON_CRITICAL),
    _entry(r"\btrac(e|es|ed|ing)\b", C.TRACES_ON_CRITICAL),
    _entry(r"\bpropagat\w*", C.PROPAGATION_ON_CRITICAL),
    _entry(r"\bown(er|ers|ership|ed)\b|\bon[\s-]*call\b", C.OWNERSHIP),
    _entry(r"\bcollect\w*|\bexport\w*|\bcentrali[sz]\w*|\baggregat\w*", C.TELEMETRY_COLLECTED),
)


def translate(requirement: Requirement) -> list[tuple[Condition, str]]:
    """The conditions a monitoring requirement's words match, with the words matched."""
    statement = requirement.content.statement
    return [(e.condition, m.group(0).lower()) for e in MONITORING if (m := e.pattern.search(statement))]


def _scope(context: ObservabilityContext, requirement: Requirement) -> tuple[Node, ...] | str | None:
    """The components it names (within the analysis scope), ``CRITICAL`` for a system requirement
    (the components declared critical), or None when its scope is not modeled."""
    components = context.components
    referencing = tuple(
        n for n in components if any(r.requirement_id == requirement.id for r in n.requirement_refs)
    )
    if referencing:
        return referencing
    scope = requirement.content.scope
    if scope is RequirementScope.SYSTEM:
        return CRITICAL
    kinds = SCOPE_KINDS.get(scope)
    return None if kinds is None else tuple(n for n in components if n.kind in kinds)


def _chosen(requirement: Requirement) -> bool:
    content = requirement.content
    if not content.in_force:
        return False
    if content.type is RequirementType.OPERATIONAL:
        return content.category == "monitoring"
    return content.type in OBJECTIVE_TYPES and content.constraint is not None


class SloMonitoring:
    meta = AnalyzerMeta(
        id="requirements",
        version=1,
        name="SLO and monitoring requirements",
        description="Whether the architecture models a measurable, alerted indicator for each in-force "
        "objective, and what monitoring requirements ask — never whether an objective is met.",
        category=FindingCategory.REQUIREMENT,
        finding_types=(FindingType.REQUIREMENT_VIOLATED, FindingType.REQUIREMENT_NOT_EVALUABLE),
        inputs=("components", "connections", "requirements"),
        properties=("criticality", "metrics", "alerts", "alert_delivery", "telemetry"),
        rules=(
            "availability / reliability / performance objective: availability → metric kinds availability "
            "or errors; latency → latency; requests or orders per second → throughput; others → unsupported",
            "objective_measurable: a concerned component declares a metric of those kinds, collected by a "
            "modeled path",
            "objective_alerted: an alert rule on those kinds (or health, for availability), with a modeled "
            "delivery path",
            "monitoring + health check / health endpoint / probe → health_checks_on_critical",
            "monitoring + alert / page → alerting_on_critical",
            "monitoring + log → logs_on_critical; structured or JSON log → structured_logs",
            "monitoring + correlation / request id → correlation_ids",
            "monitoring + metric → metrics_on_critical",
            "monitoring + trace → traces_on_critical; propagate → propagation_on_critical",
            "monitoring + owner / on-call → ownership",
            "monitoring + collect / export / centralize / aggregate → telemetry_collected",
            "anything else → unsupported (not_verifiable, never passed)",
        ),
        produces=("findings", "checks"),
        unsupported=(
            "Objective attainment (needs runtime measurements; never computed or claimed).",
            "Objectives on durability, RPO, RTO or sizing metrics (no metric kind measures them).",
            "Monitoring requirements whose words match no entry (e.g. dashboards, runbooks).",
            "Requirements scoped to users or regions.",
        ),
    )

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        checks: list[CheckResult] = []
        findings: list[ObservabilityFinding] = []
        for requirement in sorted(filter(_chosen, context.evaluated_requirements), key=lambda r: r.reference):
            found_checks, found = self._requirement(context, requirement)
            checks += found_checks
            findings += found
        return AnalyzerOutput(tuple(findings), tuple(checks))

    def _asks(self, requirement: Requirement) -> tuple[list[tuple[Condition, frozenset[str], str]], str]:
        """(condition, metric kinds, mapping) per check, and why there are none."""
        content = requirement.content
        if content.type is RequirementType.OPERATIONAL:
            matched = [
                (c, frozenset[str](), f"monitoring + '{words}'") for c, words in translate(requirement)
            ]
            return matched, "Its words match no supported condition of the documented mapping."
        metric = content.constraint.metric if content.constraint is not None else ""
        kinds = INDICATORS.get(metric)
        if kinds is None:
            return [], f"No metric kind measures its metric ({metric}): it is not traceable to an indicator."
        mapping = f"{content.type.value} objective on {metric} → metric kinds {', '.join(sorted(kinds))}"
        return [(C.OBJECTIVE_MEASURABLE, kinds, mapping), (C.OBJECTIVE_ALERTED, kinds, mapping)], ""

    def _requirement(
        self, context: ObservabilityContext, requirement: Requirement
    ) -> tuple[list[CheckResult], list[ObservabilityFinding]]:
        key = f"requirement.{requirement.reference.lower()}"
        content = requirement.content
        nodes = _scope(context, requirement)
        asks, why = self._asks(requirement)
        if nodes is None:
            why = f"Its scope ({content.scope.value}) is not modeled by the architecture."
        if not asks or nodes is None:
            return [self._unsupported(key, requirement, why)], []
        objective = content.type is not RequirementType.OPERATIONAL
        checks, findings = [], []
        for condition, kinds, mapping in asks:
            ask = Ask(condition, None if isinstance(nodes, str) else nodes, kinds=kinds)
            subject = Subject(
                f"{key}.{condition.value}" if objective or len(asks) > 1 else key,
                f"The requirement {requirement.reference}",
                SEVERITY_BY_PRIORITY[content.priority],
                requirement_id=str(requirement.id),
                mapping=mapping,
            )
            check, found = report(self.meta, judge(context, ask), condition, subject)
            checks.append(check)
            if found is not None:
                findings.append(found)
        return checks, findings

    def _unsupported(self, key: str, requirement: Requirement, why: str) -> CheckResult:
        return CheckResult(
            key,
            CheckSource.REQUIREMENT,
            C.UNSUPPORTED,
            Verdict.NOT_VERIFIABLE,
            f"{why} It is never reported as met.",
            requirement_id=str(requirement.id),
        )
