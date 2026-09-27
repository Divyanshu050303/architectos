"""Criticality: which components the architecture declares critical. The observability analyzers
ask more of critical components (logs, metrics, traces, health checks, alerting); a component whose
criticality is not declared is neither treated as critical nor as standard. One finding lists every
such component, so that what cannot be judged is visible.
"""

from core.architecture_ir.node import Node
from core.domain.capacity.results import Certainty
from core.domain.observability.results import FindingCategory, FindingType
from core.domain.validation.results import Severity

from .context import ObservabilityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress, names
from .support import finding

T = FindingType


def critical(context: ObservabilityContext, node: Node) -> bool | None:
    """Whether a component is declared critical (True), standard (False), or not said (None)."""
    return context.facts[node.id].critical


class Criticality:
    meta = AnalyzerMeta(
        id="criticality",
        version=1,
        name="Criticality",
        description="Components whose criticality is not declared, so the critical-component checks "
        "cannot be applied to them.",
        category=FindingCategory.CRITICALITY,
        finding_types=(T.CRITICALITY_NOT_MODELED,),
        inputs=("components",),
        properties=("criticality",),
        rules=("A component without a declared criticality is neither critical nor standard: reported.",),
        unsupported=("Criticality inferred from names, traffic or dependencies (never done).",),
    )

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        unknown = [n.id for n in context.components if critical(context, n) is None]
        if not unknown:
            return AnalyzerOutput()
        return AnalyzerOutput(
            (
                finding(
                    self.meta,
                    T.CRITICALITY_NOT_MODELED,
                    Severity.LOW,
                    Certainty.MODELED,
                    title=f"The criticality of {names(unknown)} is not declared",
                    explanation="Critical components are held to more observability (logs, metrics, traces, "
                    "health checks, alerting). These components declare neither critical nor standard, so "
                    "those checks are not applied to them; nothing is assumed.",
                    recommendation="State the criticality of each component (critical or standard).",
                    node_ids=tuple(unknown),
                    missing=tuple(f"{n}.configuration.criticality" for n in unknown),
                ),
            )
        )
