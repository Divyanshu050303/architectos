"""Health checks, as the architecture models them: whether components expose one, and whether
anything is modeled as checking it.

Only what is declared counts: ``health_check`` on a component (it exposes a health check) and on a
connection (its source checks the target's health: a load balancer, an orchestrator, a monitor),
and ``criticality``. A health check's presence says nothing about whether it is correct or
effective; nothing here claims either.

- A component declared **critical** that declares ``health_check: false`` is a modeled gap
  (``health_check_absent``); one that does not declare it cannot be evaluated
  (``health_check_not_modeled``).
- A declared health check that no connection is modeled as checking is
  ``health_check_unconsumed`` (not evaluable): nothing in the model acts on it.
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
FACTS = ("health_check", "criticality")


class HealthChecks:
    meta = AnalyzerMeta(
        id="health-checks",
        version=1,
        name="Health checks",
        description="Whether components model a health check, and whether anything is modeled as "
        "checking it.",
        category=FindingCategory.HEALTH_CHECKS,
        finding_types=(T.HEALTH_CHECK_ABSENT, T.HEALTH_CHECK_NOT_MODELED, T.HEALTH_CHECK_UNCONSUMED),
        inputs=("components", "connections"),
        properties=("health_check", "criticality"),
        rules=(
            "A critical component declaring health_check false is a gap; one not declaring it cannot be "
            "evaluated.",
            "A declared health check that no connection declaring health_check targets is not modeled as "
            "consumed.",
        ),
        unsupported=("Whether a health check is correct or effective (never claimed).",),
        limitations=(DECLARED_NOT_VERIFIED,),
    )

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        findings = [
            f
            for n in context.components
            if n.kind is not NodeKind.EXTERNAL
            if (f := self._component(context, n))
        ]
        return AnalyzerOutput(tuple(findings))

    def _component(self, context: ObservabilityContext, node: Node) -> ObservabilityFinding | None:
        facts = context.facts[node.id]
        declared, critical = facts.known("health_check"), facts.critical
        used = (facts, FACTS)
        common = {
            "node_ids": (node.id,),
            "dimension": Dimension.HEALTH_CHECKS,
            "evidence": evidence(facts, FACTS),
        }
        if critical is True and declared is False:
            return finding(
                self.meta,
                T.HEALTH_CHECK_ABSENT,
                Severity.HIGH,
                certainty(used),
                title=f"The critical {node.id} exposes no health check",
                explanation=f"{node.id} is declared critical and declares health_check false: nothing can "
                "tell from outside whether it is able to serve.",
                recommendation=f"Review whether {node.id} should expose a health check (liveness, "
                "readiness).",
                **common,
            )
        if critical is True and declared is None:
            return finding(
                self.meta,
                T.HEALTH_CHECK_NOT_MODELED,
                Severity.MEDIUM,
                certainty(used),
                title=f"Whether the critical {node.id} exposes a health check is not modeled",
                explanation=f"{node.id} is declared critical, but the architecture does not say whether it "
                "exposes a health check.",
                recommendation=f"State health_check for {node.id}.",
                missing=(f"{node.id}.configuration.health_check",),
                **common,
            )
        if declared is not True or node.id in context.health_consumers:
            return None
        return finding(
            self.meta,
            T.HEALTH_CHECK_UNCONSUMED,
            Severity.MEDIUM if critical is True else Severity.LOW,
            certainty(used),
            title=f"Nothing in the model checks the health of {node.id}",
            explanation=f"{node.id} declares a health check, but no connection declaring health_check "
            "targets it: what acts on it (a load balancer, an orchestrator, a monitor) is not modeled.",
            recommendation=f"Model what checks the health of {node.id} (a connection with health_check "
            "true).",
            assumptions=(DECLARED_NOT_VERIFIED,),
            **common,
        )
