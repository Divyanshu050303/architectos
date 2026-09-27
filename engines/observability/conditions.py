"""The machine-checkable observability conditions a policy rule or a requirement is checked as, and
how each is judged from what the architecture declares — shared by the policy and requirement
analyzers, so a rule and a requirement asking the same thing get the same verdict. The verdict
semantics are the shared ones (``core/domain/checks.py``): violated when any concerned element is
bad, not verifiable when none is bad but any is unknown (missing evidence is never success), not
applicable when nothing is concerned, satisfied only when every concerned element declares what is
asked.

What is concerned (third parties — external components — never are: their internals are not ours):

- the ``*_on_critical`` conditions, ``ownership``, ``telemetry_collected`` and the objective
  conditions: the components declared critical (a component whose criticality is not declared is
  unknown); a requirement naming its components concerns those instead;
- ``logs_on_critical``, ``traces_on_critical``, ``health_checks_on_critical``: declare it true;
- ``metrics_on_critical``: declare metrics (of every required kind, when kinds are required);
- ``alerting_on_critical``: declare alert rules;
- ``propagation_on_critical``: request, publish and consume flows between two components declaring
  traces, touching a concerned component, declare ``trace_propagation: true``;
- ``structured_logs``, ``correlation_ids``: components declaring logs declare them true;
- ``ownership``: declare an owner (its absence is the violation: the condition is that it is modeled);
- ``telemetry_retention``: observability components declare ``retention_seconds`` of at least the
  minimum;
- ``telemetry_collected``: the logs, metrics and traces a concerned component declares reach an
  observability component by a modeled path (the condition is that the path is modeled);
- ``objective_measurable`` (for an objective's metric kinds): a concerned component declares a metric
  of one of the kinds, and it reaches an observability component (a path that is not modeled leaves
  it unknown);
- ``objective_alerted``: a concerned component declares an alert rule on one of the kinds (or on
  ``health``, for availability), and an observability component receiving its telemetry declares an
  alert delivery (a delivery that is not modeled leaves it unknown).

A satisfied condition states what the architecture models, never that an objective is met or that
telemetry flows in production.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.capacity.results import Certainty
from core.domain.checks import Checked, Judged, State, Subject, check_of, conclude, outcome, state_of
from core.domain.facts import ElementFacts
from core.domain.observability.inputs import ComponentObservability
from core.domain.observability.results import CheckResult, Condition, FindingType, ObservabilityFinding
from core.domain.observability.values import TELEMETRY_SIGNALS, Dimension

from .context import ObservabilityContext
from .engine import AnalyzerMeta
from .support import evidence, finding
from .traces import REQUEST_FLOWS

C = Condition
SIGNALS = tuple(sorted(TELEMETRY_SIGNALS))
CRITICAL_ONLY = frozenset(
    {
        C.LOGS_ON_CRITICAL,
        C.METRICS_ON_CRITICAL,
        C.TRACES_ON_CRITICAL,
        C.HEALTH_CHECKS_ON_CRITICAL,
        C.ALERTING_ON_CRITICAL,
        C.OWNERSHIP,
        C.TELEMETRY_COLLECTED,
        C.OBJECTIVE_MEASURABLE,
        C.OBJECTIVE_ALERTED,
    }
)


@dataclass(frozen=True, slots=True)
class Ask:
    """What to check: a condition, on which components (None: the critical components in scope, or
    every one for the other conditions), the metric kinds it needs, and the minimum retention."""

    condition: Condition
    nodes: tuple[Node, ...] | None = None
    kinds: frozenset[str] = frozenset()
    min_retention: int | None = None


WHAT: dict[Condition, tuple[str, str]] = {  # (what is concerned, what they must declare)
    C.LOGS_ON_CRITICAL: ("critical components", "logs"),
    C.METRICS_ON_CRITICAL: ("critical components", "metrics"),
    C.TRACES_ON_CRITICAL: ("critical components", "traces"),
    C.PROPAGATION_ON_CRITICAL: ("traced request flows", "trace context propagation"),
    C.HEALTH_CHECKS_ON_CRITICAL: ("critical components", "a health check"),
    C.ALERTING_ON_CRITICAL: ("critical components", "alert rules"),
    C.STRUCTURED_LOGS: ("components emitting logs", "structured logs"),
    C.CORRELATION_IDS: ("components emitting logs", "correlation ids"),
    C.OWNERSHIP: ("critical components", "an owner"),
    C.TELEMETRY_RETENTION: ("observability components", "the minimum telemetry retention"),
    C.TELEMETRY_COLLECTED: ("critical components", "a collection path for their telemetry"),
    C.OBJECTIVE_MEASURABLE: ("components", "a collected metric measuring the objective"),
    C.OBJECTIVE_ALERTED: ("components", "an alert rule on the objective's signal, with a delivery path"),
}
DIMENSION_OF: dict[Condition, Dimension] = {
    C.LOGS_ON_CRITICAL: Dimension.LOGGING,
    C.STRUCTURED_LOGS: Dimension.LOGGING,
    C.CORRELATION_IDS: Dimension.LOGGING,
    C.METRICS_ON_CRITICAL: Dimension.METRICS,
    C.OBJECTIVE_MEASURABLE: Dimension.METRICS,
    C.TRACES_ON_CRITICAL: Dimension.TRACING,
    C.PROPAGATION_ON_CRITICAL: Dimension.TRACING,
    C.HEALTH_CHECKS_ON_CRITICAL: Dimension.HEALTH_CHECKS,
    C.ALERTING_ON_CRITICAL: Dimension.ALERTING,
    C.OBJECTIVE_ALERTED: Dimension.ALERTING,
}


def _node(
    facts: ElementFacts,
    state: State,
    shown: Sequence[str],
    missing: Iterable[str] = (),
    gaps: Iterable[str] = (),
) -> Checked:
    """One component's state: ``missing`` names configuration properties, ``gaps`` other things the
    model would need (a collection or delivery path)."""
    return Checked(
        facts.element_id,
        False,
        state,
        evidence(facts, shown),
        tuple(f"{facts.element_id}.configuration.{m}" for m in missing) + tuple(gaps),
    )


def _flag(facts: ElementFacts, name: str, shown: Sequence[str]) -> Checked:
    """A component judged on one boolean it must declare true."""
    value = facts.known(name)
    state = state_of(None if value is None else value is True)
    return _node(facts, state, shown, [name] if value is None else [])


def _metrics(facts: ComponentObservability, ask: Ask) -> Checked:
    shown = ("criticality", "metrics")
    kinds = facts.metric_kinds
    if kinds is None:
        return _node(facts, State.UNKNOWN, shown, ["metrics"])
    return _node(facts, state_of(ask.kinds <= set(kinds) if ask.kinds else bool(kinds)), shown)


def _alerting(facts: ComponentObservability) -> Checked:
    shown = ("criticality", "alerts")
    signals = facts.alert_signals
    if signals is None:
        return _node(facts, State.UNKNOWN, shown, ["alerts"])
    return _node(facts, state_of(bool(signals)), shown)


def _logged(facts: ComponentObservability, name: str) -> Checked | None:
    shown = ("logs", name)
    logs = facts.known("logs")
    if logs is None:
        return _node(facts, State.UNKNOWN, shown, ["logs"])
    return _flag(facts, name, shown) if logs is True else None


def _owned(facts: ComponentObservability) -> Checked:
    declared = facts.known("owner") is not None
    return _node(facts, state_of(declared), ("criticality", "owner"), [] if declared else ["owner"])


def _collected(context: ObservabilityContext, node: Node, facts: ComponentObservability) -> Checked | None:
    shown = ("criticality", *SIGNALS)
    declared = [s for s in SIGNALS if context.declares_signal(node.id, s)]
    if not declared:
        undeclared = [s for s in SIGNALS if facts.known(s) is None]
        return _node(facts, State.UNKNOWN, shown, undeclared) if undeclared else None
    return _node(facts, state_of(all(node.id in context.collected(s) for s in declared)), shown)


def _measurable(
    context: ObservabilityContext, node: Node, facts: ComponentObservability, ask: Ask
) -> Checked:
    shown = ("metrics",)
    kinds = facts.metric_kinds
    if kinds is None:
        return _node(facts, State.UNKNOWN, shown, ["metrics"])
    if not ask.kinds & set(kinds):
        return _node(facts, State.BAD, shown)
    if node.id in context.collected("metrics"):
        return _node(facts, State.OK, shown)
    return _node(facts, State.UNKNOWN, shown, gaps=[f"{node.id}.collection"])


def _alerted(context: ObservabilityContext, node: Node, facts: ComponentObservability, ask: Ask) -> Checked:
    shown = ("alerts",)
    signals = facts.alert_signals
    if signals is None:
        return _node(facts, State.UNKNOWN, shown, ["alerts"])
    watched = set(ask.kinds) | ({"health"} if "availability" in ask.kinds else set())
    if not watched & set(signals):
        return _node(facts, State.BAD, shown)
    if context.alert_delivery(node.id):
        return _node(facts, State.OK, shown)
    return _node(facts, State.UNKNOWN, shown, gaps=[f"{node.id}.alert_delivery"])


def _retained(facts: ComponentObservability, ask: Ask) -> Checked:
    shown = ("retention_seconds",)
    retention = facts.known("retention_seconds")
    if retention is None or ask.min_retention is None:
        return _node(facts, State.UNKNOWN, shown, ["retention_seconds"])
    return _node(facts, state_of(isinstance(retention, int) and retention >= ask.min_retention), shown)


Judge = Callable[[ObservabilityContext, Node, ComponentObservability, Ask], Checked | None]
_JUDGES: dict[Condition, Judge] = {
    C.LOGS_ON_CRITICAL: lambda _c, _n, f, _a: _flag(f, "logs", ("criticality", "logs")),
    C.TRACES_ON_CRITICAL: lambda _c, _n, f, _a: _flag(f, "traces", ("criticality", "traces")),
    C.HEALTH_CHECKS_ON_CRITICAL: lambda _c, _n, f, _a: _flag(
        f, "health_check", ("criticality", "health_check")
    ),
    C.METRICS_ON_CRITICAL: lambda _c, _n, f, a: _metrics(f, a),
    C.ALERTING_ON_CRITICAL: lambda _c, _n, f, _a: _alerting(f),
    C.STRUCTURED_LOGS: lambda _c, _n, f, _a: _logged(f, "structured_logs"),
    C.CORRELATION_IDS: lambda _c, _n, f, _a: _logged(f, "correlation_ids"),
    C.OWNERSHIP: lambda _c, _n, f, _a: _owned(f),
    C.TELEMETRY_COLLECTED: lambda c, n, f, _a: _collected(c, n, f),
    C.TELEMETRY_RETENTION: lambda _c, _n, f, a: _retained(f, a),
    C.OBJECTIVE_MEASURABLE: _measurable,
    C.OBJECTIVE_ALERTED: _alerted,
}


def _component(context: ObservabilityContext, node: Node, ask: Ask) -> Checked | None:
    """One component under one condition: its state, or None when it is not concerned."""
    if node.kind is NodeKind.EXTERNAL:
        return None  # a third party's internals are not ours to model
    retention = ask.condition is C.TELEMETRY_RETENTION
    if retention != (node.kind is NodeKind.OBSERVABILITY):
        return None
    facts = context.facts[node.id]
    if ask.nodes is None and ask.condition in CRITICAL_ONLY:
        critical = facts.critical
        if critical is None:
            return _node(facts, State.UNKNOWN, ("criticality",), ["criticality"])
        if not critical:
            return None
    return _JUDGES[ask.condition](context, node, facts, ask)


def _flows(context: ObservabilityContext, ask: Ask) -> list[Checked]:
    """Traced request flows touching a concerned component (a critical one, unless named)."""
    named = None if ask.nodes is None else {n.id for n in ask.nodes}
    checked = []
    for connection in context.connections:
        ends = (connection.source_id, connection.target_id)
        if connection.kind not in REQUEST_FLOWS or not all(
            context.declares_signal(e, "traces") for e in ends
        ):
            continue
        link = context.connection_facts[connection.id]
        shown = evidence(link, ("trace_propagation",))
        if named is not None and not named & set(ends):
            continue
        if named is None:
            undeclared = tuple(
                f"{e}.configuration.criticality" for e in ends if context.facts[e].critical is None
            )
            if not any(context.facts[e].critical for e in ends):
                if undeclared:  # whether it is concerned is not established
                    checked.append(Checked(connection.id, True, State.UNKNOWN, shown, undeclared))
                continue
        propagated = link.known("trace_propagation")
        missing = (f"{connection.id}.configuration.trace_propagation",) if propagated is None else ()
        state = state_of(None if propagated is None else propagated is True)
        checked.append(Checked(connection.id, True, state, shown, missing))
    return checked


def judge(context: ObservabilityContext, ask: Ask) -> Judged:
    """The verdict for one condition, from what the architecture declares."""
    if ask.condition is C.PROPAGATION_ON_CRITICAL:
        checked = _flows(context, ask)
    else:
        nodes = context.components if ask.nodes is None else ask.nodes
        checked = [c for n in nodes if (c := _component(context, n, ask)) is not None]
    concerned, must = WHAT[ask.condition]
    return conclude(checked, concerned, must)


def report(
    meta: AnalyzerMeta, judged: Judged, condition: Condition, subject: Subject
) -> tuple[CheckResult, ObservabilityFinding | None]:
    """The check for one verdict, and a finding when it needs a look (``core.domain.checks.outcome``)."""
    check = check_of(CheckResult, judged, condition, subject)
    found = outcome(judged, subject, WHAT[condition][1])
    if found is None:
        return check, None
    return check, finding(
        meta,
        FindingType[found.type_name],
        found.severity,
        Certainty.MODELED,
        title=found.title,
        explanation=found.explanation,
        recommendation=found.recommendation,
        node_ids=judged.offending_nodes,
        connection_ids=judged.offending_connections,
        evidence=judged.actual,
        missing=judged.missing,
        dimension=DIMENSION_OF.get(condition),
        requirement_id=subject.requirement_id,
        policy_rule=subject.policy_rule,
        check_key=subject.key,
    )
