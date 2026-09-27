"""Alerting paths, as the architecture models them: which signals alert rules watch for a component,
whether the component emits them, and whether an alert can leave the observability system.

Only what is declared counts: ``alerts`` (the signals alert rules watch: a metric kind, ``health`` or
``logs``; an empty list declares none), the signals the component declares (``metrics``,
``health_check``, ``logs``), the collection of its metrics or logs, the ``alert_delivery`` of the
observability component they reach, and ``criticality``. No threshold is invented, and nothing here
claims an alert fires, is delivered or is acted on.

- A component declared **critical** that declares ``alerts: []`` is a modeled gap
  (``alerts_absent``); one that does not declare its alerting cannot be evaluated
  (``alerts_not_modeled``).
- An alert rule watching a signal the component declares it does not emit has no source
  (``alert_without_signal``, a gap); when the component does not declare that signal at all, no gap
  is claimed.
- Alert rules for a component whose metrics or logs reach no observability component declaring an
  alert delivery (other than ``none``) have no modeled way out (``alert_delivery_not_modeled``).

Ownership and escalation are checked only where a policy requires them (the policy analyzer).
"""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.observability.results import FindingCategory, FindingType, ObservabilityFinding
from core.domain.observability.values import Dimension
from core.domain.validation.results import Severity

from .context import ObservabilityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress
from .support import DECLARED_NOT_VERIFIED, certainty, evidence, finding

T = FindingType
FACTS = ("alerts", "criticality")


def _source(context: ObservabilityContext, node: Node, signal: str) -> str:
    """The property that decides whether ``node`` emits the watched ``signal``."""
    return {"logs": "logs", "health": "health_check"}.get(signal, "metrics")


class Alerts:
    meta = AnalyzerMeta(
        id="alerts",
        version=1,
        name="Alerting",
        description="Whether components model alert rules, whether those rules watch a signal the "
        "component emits, and whether alerts have a modeled delivery path.",
        category=FindingCategory.ALERTING,
        finding_types=(
            T.ALERTS_ABSENT,
            T.ALERTS_NOT_MODELED,
            T.ALERT_WITHOUT_SIGNAL,
            T.ALERT_DELIVERY_NOT_MODELED,
        ),
        inputs=("components", "connections"),
        properties=(
            "alerts",
            "criticality",
            "metrics",
            "health_check",
            "logs",
            "alert_delivery",
            "telemetry",
        ),
        rules=(
            "A critical component declaring no alert rules is a gap; one not declaring alerting cannot be "
            "evaluated.",
            "An alert rule on a signal the component declares it does not emit has no source.",
            "Alert rules whose component's metrics or logs reach no observability component declaring an "
            "alert delivery have no modeled way out.",
        ),
        unsupported=(
            "Alert thresholds (never invented).",
            "Whether an alert fires, is delivered or is acted on (never claimed).",
            "Incident management and escalation (only ownership, where a policy requires it).",
        ),
        limitations=(DECLARED_NOT_VERIFIED,),
    )

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        findings: list[ObservabilityFinding] = []
        for node in context.components:
            if node.kind is not NodeKind.EXTERNAL:
                findings += self._component(context, node)
        return AnalyzerOutput(tuple(findings))

    def _component(self, context: ObservabilityContext, node: Node) -> list[ObservabilityFinding]:
        facts = context.facts[node.id]
        watched, critical = facts.alert_signals, facts.critical
        used = (facts, FACTS)
        common = {"node_ids": (node.id,), "dimension": Dimension.ALERTING}
        if critical is True and not watched:
            if watched == ():
                return [
                    finding(
                        self.meta,
                        T.ALERTS_ABSENT,
                        Severity.HIGH,
                        certainty(used),
                        title=f"No alert rule watches the critical {node.id}",
                        explanation=f"{node.id} is declared critical and declares no alert rules: its "
                        "failure is noticed only if someone looks.",
                        recommendation=f"Review which signals of {node.id} should raise an alert.",
                        evidence=evidence(facts, FACTS),
                        **common,
                    )
                ]
            return [
                finding(
                    self.meta,
                    T.ALERTS_NOT_MODELED,
                    Severity.MEDIUM,
                    certainty(used),
                    title=f"Whether alert rules watch the critical {node.id} is not modeled",
                    explanation=f"{node.id} is declared critical, but the architecture does not say which "
                    "signals, if any, raise an alert.",
                    recommendation=f"State the alerts of {node.id} (the signals watched, or none).",
                    evidence=evidence(facts, FACTS),
                    missing=(f"{node.id}.configuration.alerts",),
                    **common,
                )
            ]
        if not watched:
            return []
        found = []
        unsourced = [
            s
            for s in watched
            if facts.known(_source(context, node, s)) is not None and not context.emits(node, s)
        ]
        if unsourced:
            sources = sorted({_source(context, node, s) for s in unsourced})
            found.append(
                finding(
                    self.meta,
                    T.ALERT_WITHOUT_SIGNAL,
                    Severity.MEDIUM,
                    certainty(used, (facts, tuple(sources))),
                    title=f"Alert rules on {node.id} watch signals it does not emit ({', '.join(unsourced)})",
                    explanation=f"{node.id} declares alert rules on {', '.join(unsourced)}, but declares "
                    "that it does not emit them: those rules have no signal to watch.",
                    recommendation=f"Review the alert rules of {node.id} against the signals it emits.",
                    evidence=evidence(facts, ("alerts", *sources)),
                    **common,
                )
            )
        if not context.alert_delivery(node.id):
            backends = context.reached_backends(node.id)
            found.append(
                finding(
                    self.meta,
                    T.ALERT_DELIVERY_NOT_MODELED,
                    Severity.MEDIUM if critical is True else Severity.LOW,
                    certainty(used),
                    title=f"How the alerts of {node.id} are delivered is not modeled",
                    explanation=f"{node.id} declares alert rules, but its metrics and logs reach no "
                    "observability component declaring an alert delivery (email, chat, paging): nothing in "
                    "the model says an alert leaves the monitoring system.",
                    recommendation="State the alert_delivery of the observability component its signals "
                    f"reach, or model how the signals of {node.id} are collected.",
                    evidence=evidence(facts, FACTS)
                    + tuple(e for b in backends for e in evidence(context.facts[b], ("alert_delivery",))),
                    missing=tuple(f"{b}.configuration.alert_delivery" for b in backends)
                    or (f"{node.id}.collection",),
                    node_ids=(node.id, *backends),
                    dimension=Dimension.ALERTING,
                )
            )
        return found
