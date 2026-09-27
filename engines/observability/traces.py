"""Distributed tracing and context propagation, as the architecture models them.

Only what is declared counts: ``traces`` (a component emits spans), ``trace_context`` (it
``propagate``s or ``terminate``s an incoming trace context), the ``trace_propagation`` and
``telemetry`` of its connections, and ``criticality``. Tracing on one component says nothing about
its neighbours; no sampling rate or trace coverage is invented, and nothing here claims end-to-end
trace completeness.

- A component declared **critical** that declares ``traces: false`` is a modeled gap
  (``traces_absent``); one that does not declare tracing cannot be evaluated (``traces_not_modeled``).
- Declared traces that reach no observability component by connections declaring traces telemetry
  are ``telemetry_not_collected`` (dimension tracing).
- **Propagation** is judged on request flows only — ``request``, ``publish`` and ``consume``
  connections (not data access, replication or dependencies) — between two components that both
  declare traces: a flow declaring ``trace_propagation: false`` breaks the trace there
  (``propagation_broken``); a flow that does not declare it cannot be evaluated
  (``propagation_not_modeled``). A component declaring ``trace_context: terminate`` with traced flows
  both in and out ends its callers' traces (``propagation_broken``, naming its outgoing flows).
"""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.observability.results import FindingCategory, FindingType, ObservabilityFinding
from core.domain.observability.values import Dimension
from core.domain.validation.results import Severity

from .context import ObservabilityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress, names
from .support import DECLARED_NOT_VERIFIED, certainty, evidence, finding

T = FindingType
FACTS = ("traces", "criticality")
# The flows a trace context travels with: calls and messages (not data access, replication or
# dependencies, which are not request flows).
REQUEST_FLOWS = frozenset({ConnectionKind.REQUEST, ConnectionKind.PUBLISH, ConnectionKind.CONSUME})


class Traces:
    meta = AnalyzerMeta(
        id="traces",
        version=1,
        name="Tracing and context propagation",
        description="Whether components model tracing, whether modeled traces are collected, and whether "
        "the trace context is modeled as propagated along request flows between traced components.",
        category=FindingCategory.TRACING,
        finding_types=(
            T.TRACES_ABSENT,
            T.TRACES_NOT_MODELED,
            T.TELEMETRY_NOT_COLLECTED,
            T.PROPAGATION_BROKEN,
            T.PROPAGATION_NOT_MODELED,
        ),
        inputs=("components", "connections"),
        properties=("traces", "trace_context", "criticality", "trace_propagation", "telemetry"),
        rules=(
            "A critical component declaring traces false is a gap; one not declaring tracing cannot be "
            "evaluated.",
            "Declared traces that reach no observability component by connections declaring traces "
            "telemetry are not modeled as collected.",
            "On request, publish and consume flows between two traced components: trace_propagation false "
            "breaks the trace; not declared cannot be evaluated.",
            "A component terminating the trace context, with traced flows in and out, ends its callers' "
            "traces.",
        ),
        unsupported=(
            "Sampling rates and trace coverage (never invented; checked only where a policy requires them).",
            "End-to-end trace completeness (never claimed).",
            "Data access, replication and dependency connections (not request flows).",
        ),
        limitations=(DECLARED_NOT_VERIFIED,),
    )

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        findings: list[ObservabilityFinding] = []
        for node in context.components:
            if node.kind is not NodeKind.EXTERNAL:
                findings += self._component(context, node)
        findings += self._propagation(context)
        return AnalyzerOutput(tuple(findings))

    def _component(self, context: ObservabilityContext, node: Node) -> list[ObservabilityFinding]:
        facts = context.facts[node.id]
        traces, critical = facts.known("traces"), facts.critical
        used = (facts, FACTS)
        common = {"node_ids": (node.id,), "dimension": Dimension.TRACING, "evidence": evidence(facts, FACTS)}
        if critical is True and traces is False:
            return [
                finding(
                    self.meta,
                    T.TRACES_ABSENT,
                    Severity.HIGH,
                    certainty(used),
                    title=f"The critical {node.id} emits no traces",
                    explanation=f"{node.id} is declared critical and declares traces false: a request's path "
                    "through it cannot be followed.",
                    recommendation=f"Review whether {node.id} should emit trace spans.",
                    **common,
                )
            ]
        if critical is True and traces is None:
            return [
                finding(
                    self.meta,
                    T.TRACES_NOT_MODELED,
                    Severity.MEDIUM,
                    certainty(used),
                    title=f"Whether the critical {node.id} emits traces is not modeled",
                    explanation=f"{node.id} is declared critical, but the architecture does not say whether "
                    "it emits traces.",
                    recommendation=f"State traces for {node.id}.",
                    missing=(f"{node.id}.configuration.traces",),
                    **common,
                )
            ]
        if traces is not True or node.id in context.collected("traces"):
            return []
        return [
            finding(
                self.meta,
                T.TELEMETRY_NOT_COLLECTED,
                Severity.MEDIUM if critical is True else Severity.LOW,
                certainty(used),
                title=f"The traces of {node.id} reach no observability component in the model",
                explanation=f"{node.id} declares traces, but no connection declaring traces telemetry leads "
                "from it to an observability component: their collection is not modeled.",
                recommendation=f"Model how the traces of {node.id} are exported to an observability "
                "component.",
                assumptions=(DECLARED_NOT_VERIFIED,),
                **common,
            )
        ]

    def _propagation(self, context: ObservabilityContext) -> list[ObservabilityFinding]:
        flows = [
            c
            for c in context.connections
            if c.kind in REQUEST_FLOWS
            and context.declares_signal(c.source_id, "traces")
            and context.declares_signal(c.target_id, "traces")
        ]
        findings = [f for c in flows if (f := self._flow(context, c))]
        findings += self._terminators(context, flows)
        return findings

    def _critical(self, context: ObservabilityContext, *node_ids: str) -> bool:
        return any(context.facts[n].critical is True for n in node_ids)

    def _flow(self, context: ObservabilityContext, connection: Connection) -> ObservabilityFinding | None:
        link = context.connection_facts[connection.id]
        propagated = link.known("trace_propagation")
        if propagated is True:
            return None
        ends = (connection.source_id, connection.target_id)
        critical = self._critical(context, *ends)
        label = f"{connection.id} ({connection.source_id} → {connection.target_id})"
        common = {
            "node_ids": ends,
            "connection_ids": (connection.id,),
            "dimension": Dimension.TRACING,
            "evidence": evidence(link, ("trace_propagation",))
            + tuple(e for n in ends for e in evidence(context.facts[n], ("traces", "criticality"))),
        }
        used = [(link, ("trace_propagation",)), *((context.facts[n], FACTS) for n in ends)]
        if propagated is False:
            return finding(
                self.meta,
                T.PROPAGATION_BROKEN,
                Severity.HIGH if critical else Severity.MEDIUM,
                certainty(*used),
                title=f"The trace context is not propagated across {label}",
                explanation=f"{connection.source_id} and {connection.target_id} both emit traces, but "
                f"{label} declares trace_propagation false: a request's trace breaks here into two "
                "unrelated traces.",
                recommendation="Review propagating the trace context (e.g. W3C traceparent) across "
                f"{connection.id}.",
                **common,
            )
        return finding(
            self.meta,
            T.PROPAGATION_NOT_MODELED,
            Severity.MEDIUM if critical else Severity.LOW,
            certainty(*used),
            title=f"Whether the trace context crosses {label} is not modeled",
            explanation=f"{connection.source_id} and {connection.target_id} both emit traces, but {label} "
            "does not declare whether the trace context is propagated: tracing on both ends does not "
            "establish it.",
            recommendation=f"State trace_propagation for {connection.id}.",
            missing=(f"{connection.id}.configuration.trace_propagation",),
            **common,
        )

    def _terminators(
        self, context: ObservabilityContext, flows: list[Connection]
    ) -> list[ObservabilityFinding]:
        findings = []
        for node in context.components:
            facts = context.facts[node.id]
            if facts.known("trace_context") != "terminate":
                continue
            incoming = [c.id for c in flows if c.target_id == node.id]
            outgoing = sorted(c.id for c in flows if c.source_id == node.id)
            if not incoming or not outgoing:
                continue
            findings.append(
                finding(
                    self.meta,
                    T.PROPAGATION_BROKEN,
                    Severity.HIGH if facts.critical is True else Severity.MEDIUM,
                    certainty((facts, ("trace_context", "traces"))),
                    title=f"{node.id} ends the traces of its callers",
                    explanation=f"{node.id} declares trace_context terminate: traces arriving over "
                    f"{names(sorted(incoming))} do not continue over {names(outgoing)}.",
                    recommendation=f"Review whether {node.id} should propagate the incoming trace context.",
                    node_ids=(node.id,),
                    connection_ids=tuple(outgoing),
                    dimension=Dimension.TRACING,
                    evidence=evidence(facts, ("trace_context", "traces")),
                )
            )
        return findings
