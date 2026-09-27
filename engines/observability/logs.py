"""Logging coverage: whether the architecture models the logs of its components, and where they go.

Only what is declared counts: ``logs`` (a component emits logs), the ``telemetry`` of its connections
(which signals reach an observability component), ``criticality``, and the data classification the
security properties declare. A technology that can log is not taken as logging; retention, access
and redaction are never inferred; no log content is read or shown.

- A component declared **critical** that declares ``logs: false`` is a modeled gap
  (``logs_absent``); one that does not declare logging cannot be evaluated (``logs_not_modeled``).
- A component that declares logs whose logs reach no observability component by a modeled path is
  ``telemetry_not_collected`` (dimension logging): its logs may exist but are not modeled as
  collected.
- A component that declares logs and is declared sensitive (``confidential``/``restricted`` data or
  personal data) is a potential risk (``sensitive_data_in_logs``): the architecture does not say what
  its logs contain or how they are redacted. Data protection itself is the security engine's.

Structured logs, correlation identifiers and log retention are checked where a policy requires them
(the policy analyzer).
"""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.observability.results import FindingCategory, FindingType, ObservabilityFinding
from core.domain.observability.values import Dimension
from core.domain.security.inputs import ComponentSecurity
from core.domain.validation.results import Severity

from .context import ObservabilityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress
from .support import DECLARED_NOT_VERIFIED, certainty, evidence, finding

T = FindingType
CLASSIFICATION = ("data_classification", "personal_data")


class Logs:
    meta = AnalyzerMeta(
        id="logs",
        version=1,
        name="Logging",
        description="Whether components model their logs, and whether modeled logs reach an "
        "observability component.",
        category=FindingCategory.LOGGING,
        finding_types=(
            T.LOGS_ABSENT,
            T.LOGS_NOT_MODELED,
            T.TELEMETRY_NOT_COLLECTED,
            T.SENSITIVE_DATA_IN_LOGS,
        ),
        inputs=("components", "connections"),
        properties=("logs", "criticality", "telemetry", "data_classification", "personal_data"),
        rules=(
            "A critical component declaring logs false is a gap; one not declaring logging cannot be "
            "evaluated.",
            "Declared logs that reach no observability component by connections declaring logs telemetry "
            "are not modeled as collected.",
            "Declared logs on a component declared sensitive are a potential risk (content and redaction "
            "are not modeled).",
        ),
        unsupported=(
            "Log content, retention, access and redaction (never inferred).",
            "Third parties' logs.",
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
        logs, critical = facts.known("logs"), facts.critical
        used = (facts, ("logs", "criticality"))
        shown = evidence(facts, ("logs", "criticality"))
        common = {"node_ids": (node.id,), "dimension": Dimension.LOGGING}
        if critical is True and logs is not True:
            if logs is False:
                return [
                    finding(
                        self.meta,
                        T.LOGS_ABSENT,
                        Severity.HIGH,
                        certainty(used),
                        title=f"The critical {node.id} emits no logs",
                        explanation=f"{node.id} is declared critical and declares logs false: what it does, "
                        "and why it fails, leaves no record to investigate.",
                        recommendation=f"Review whether {node.id} should emit logs to an observability "
                        "component.",
                        evidence=shown,
                        **common,
                    )
                ]
            return [
                finding(
                    self.meta,
                    T.LOGS_NOT_MODELED,
                    Severity.MEDIUM,
                    certainty(used),
                    title=f"Whether the critical {node.id} emits logs is not modeled",
                    explanation=f"{node.id} is declared critical, but the architecture does not say whether "
                    "it logs: neither shown observable nor shown blind.",
                    recommendation=f"State logs for {node.id}.",
                    evidence=shown,
                    missing=(f"{node.id}.configuration.logs",),
                    **common,
                )
            ]
        if logs is not True:
            return []
        found = []
        if node.id not in context.collected("logs"):
            found.append(
                finding(
                    self.meta,
                    T.TELEMETRY_NOT_COLLECTED,
                    Severity.MEDIUM if critical is True else Severity.LOW,
                    certainty(used),
                    title=f"The logs of {node.id} reach no observability component in the model",
                    explanation=f"{node.id} declares logs, but no connection declaring logs telemetry leads "
                    "from it to an observability component: where they go is not modeled.",
                    recommendation=f"Model how the logs of {node.id} are collected (a connection with "
                    "telemetry logs to a collector or observability component).",
                    evidence=shown,
                    assumptions=(DECLARED_NOT_VERIFIED,),
                    **common,
                )
            )
        security = ComponentSecurity.of(node)
        if security.sensitive is True:
            found.append(
                finding(
                    self.meta,
                    T.SENSITIVE_DATA_IN_LOGS,
                    Severity.MEDIUM,
                    certainty(used, (security, CLASSIFICATION)),
                    title=f"{node.id} handles sensitive data and emits logs",
                    explanation=f"{node.id} is declared to hold or handle sensitive data and declares logs; "
                    "the architecture does not say what its logs contain or how they are redacted. Data "
                    "protection itself is analyzed by the security engine.",
                    recommendation=f"Review what the logs of {node.id} may contain, and their redaction and "
                    "access.",
                    evidence=shown + evidence(security, CLASSIFICATION),
                    **common,
                )
            )
        return found
