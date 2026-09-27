"""Metrics coverage: whether the architecture models the metrics of its components, and where they go.

Only what is declared counts: ``metrics`` (the kinds a component exposes: errors, latency,
throughput, saturation, resources, availability; an empty list declares none), the ``telemetry`` of
its connections, and ``criticality``. A listed kind says the architecture declares it — not that it
is emitted, scraped or correct. No metric value, rate, distribution or time series is ever computed:
latency, error rates, throughput and saturation are runtime facts, not architecture facts.

- A component declared **critical** that declares no metrics (``metrics: []``) is a modeled gap
  (``metrics_absent``); one that does not declare its metrics cannot be evaluated
  (``metrics_not_modeled``).
- Declared metrics that reach no observability component by connections declaring metrics telemetry
  are ``telemetry_not_collected`` (dimension metrics): missing collection in the model, which is not
  the same as missing runtime telemetry.

Which kinds are required is not a universal checklist: it comes from the project's policy or from an
objective that names them (the policy and requirement analyzers). Labels and dimensions are not
modeled.
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
FACTS = ("metrics", "criticality")


class Metrics:
    meta = AnalyzerMeta(
        id="metrics",
        version=1,
        name="Metrics",
        description="Whether components model their metrics, and whether modeled metrics reach an "
        "observability component.",
        category=FindingCategory.METRICS,
        finding_types=(T.METRICS_ABSENT, T.METRICS_NOT_MODELED, T.TELEMETRY_NOT_COLLECTED),
        inputs=("components", "connections"),
        properties=("metrics", "criticality", "telemetry"),
        rules=(
            "A critical component declaring no metrics is a gap; one not declaring its metrics cannot be "
            "evaluated.",
            "Declared metrics that reach no observability component by connections declaring metrics "
            "telemetry are not modeled as collected.",
        ),
        unsupported=(
            "Metric values, rates, distributions and time series (runtime facts, never computed).",
            "Metric labels and dimensions (not modeled).",
            "A universal list of required kinds (only a policy or an objective requires kinds).",
        ),
        limitations=(DECLARED_NOT_VERIFIED,),
    )

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        findings: list[ObservabilityFinding] = []
        for node in context.components:
            if node.kind is not NodeKind.EXTERNAL and (found := self._component(context, node)):
                findings.append(found)
        return AnalyzerOutput(tuple(findings))

    def _component(self, context: ObservabilityContext, node: Node) -> ObservabilityFinding | None:
        facts = context.facts[node.id]
        kinds, critical = facts.metric_kinds, facts.critical
        used = (facts, FACTS)
        common = {"node_ids": (node.id,), "dimension": Dimension.METRICS, "evidence": evidence(facts, FACTS)}
        if critical is True and not kinds:
            if kinds == ():
                return finding(
                    self.meta,
                    T.METRICS_ABSENT,
                    Severity.HIGH,
                    certainty(used),
                    title=f"The critical {node.id} exposes no metrics",
                    explanation=f"{node.id} is declared critical and declares no metrics: its errors, "
                    "latency and load cannot be measured from the architecture as modeled.",
                    recommendation=f"Review which metrics {node.id} should expose (e.g. errors, latency, "
                    "throughput, saturation).",
                    **common,
                )
            return finding(
                self.meta,
                T.METRICS_NOT_MODELED,
                Severity.MEDIUM,
                certainty(used),
                title=f"Which metrics the critical {node.id} exposes is not modeled",
                explanation=f"{node.id} is declared critical, but the architecture does not say which "
                "metrics it exposes: neither shown measurable nor shown unmeasured.",
                recommendation=f"State the metrics of {node.id} (the kinds it exposes, or none).",
                missing=(f"{node.id}.configuration.metrics",),
                **common,
            )
        if not kinds or node.id in context.collected("metrics"):
            return None
        return finding(
            self.meta,
            T.TELEMETRY_NOT_COLLECTED,
            Severity.MEDIUM if critical is True else Severity.LOW,
            certainty(used),
            title=f"The metrics of {node.id} reach no observability component in the model",
            explanation=f"{node.id} declares metrics ({', '.join(kinds)}), but no connection declaring "
            "metrics telemetry leads from it to an observability component: their collection is not "
            "modeled. This is about the model, not about what runs.",
            recommendation=f"Model how the metrics of {node.id} are collected (scraped or exported to an "
            "observability component).",
            assumptions=(DECLARED_NOT_VERIFIED,),
            **common,
        )
