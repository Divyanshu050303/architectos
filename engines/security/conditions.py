"""The machine-checkable security conditions a policy rule or a requirement is checked as, and how
each is judged from what the architecture declares — shared by the policy and requirement analyzers,
so a rule and a requirement asking the same thing get the same verdict.

For every element a condition concerns, what is declared is ``ok``, ``bad`` or ``unknown`` (with the
properties missing). The verdict follows validation's semantics:

- ``violated`` when any concerned element is ``bad`` (a known violation stands even when other
  elements are unknown);
- ``not_verifiable`` when none is bad but any is unknown — missing evidence is never success;
- ``not_applicable`` when nothing is concerned;
- ``satisfied`` only when every concerned element declares what the condition asks.

What a condition concerns is itself declared: a component whose exposure, sensitive operations,
management interface or secrets are not declared is unknown for the conditions that depend on them.

- ``encryption_at_rest``: data stores (the sensitive ones, when asked) declare
  ``encryption_at_rest: true``;
- ``encryption_in_transit``: communicating connections (the sensitive ones, when asked) declare
  ``tls: true`` or use an encrypted protocol;
- ``authentication_on_public``: components declared public declare an authentication mechanism;
- ``authorization_on_sensitive``: components declaring sensitive operations declare an
  authorization model;
- ``no_public_management_interface``: components declaring a management interface are declared
  internal or private;
- ``approved_secret_source``: components declaring they need secrets declare an approved source;
- ``secret_rotation``: components declaring they need secrets declare ``secret_rotation: true``;
- ``audit_logging``: components (those with sensitive operations, when asked) declare
  ``audit_logging: true``;
- ``data_classification``: components declare a data classification (its absence is the
  violation: the condition is that it is modeled).

Third parties (external components) are concerned only by the data-classification condition and,
when they declare one, by the no-public-management-interface condition.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.capacity.results import Certainty
from core.domain.checks import Checked, Judged, State, Subject, check_of, conclude, outcome, state_of
from core.domain.facts import ElementFacts
from core.domain.security.inputs import STORES, ComponentSecurity
from core.domain.security.results import CheckResult, Condition, FindingType, SecurityFinding

from .context import SecurityContext
from .engine import AnalyzerMeta
from .support import authenticated, evidence, finding, protocol_evidence, transport_protected

DATA_FLOWS = frozenset(
    {ConnectionKind.DATA_ACCESS, ConnectionKind.REPLICATION, ConnectionKind.PUBLISH, ConnectionKind.CONSUME}
)


@dataclass(frozen=True, slots=True)
class Ask:
    """What to check: a condition, on which components (None: every component in scope), for
    sensitive data or operations only, and the approved secret sources."""

    condition: Condition
    nodes: tuple[Node, ...] | None = None
    sensitive_only: bool = False
    approved: frozenset[str] = frozenset()


WHAT: dict[Condition, tuple[str, str]] = {  # (what is concerned, what they must declare)
    Condition.ENCRYPTION_AT_REST: ("data stores", "encryption at rest"),
    Condition.ENCRYPTION_IN_TRANSIT: ("connections", "encryption in transit"),
    Condition.AUTHENTICATION_ON_PUBLIC: ("public components", "an authentication mechanism"),
    Condition.AUTHORIZATION_ON_SENSITIVE: ("components with sensitive operations", "an authorization model"),
    Condition.NO_PUBLIC_MANAGEMENT_INTERFACE: ("management interfaces", "internal or private exposure"),
    Condition.APPROVED_SECRET_SOURCE: ("components needing secrets", "an approved secret source"),
    Condition.SECRET_ROTATION: ("components needing secrets", "secret rotation"),
    Condition.AUDIT_LOGGING: ("components", "audit logging"),
    Condition.DATA_CLASSIFICATION: ("components", "a data classification"),
}


def _node(facts: ElementFacts, state: State, shown: Sequence[str], missing: Iterable[str] = ()) -> Checked:
    return Checked(
        facts.element_id,
        False,
        state,
        evidence(facts, shown),
        tuple(f"{facts.element_id}.configuration.{m}" for m in missing),
    )


def _flag(facts: ElementFacts, name: str, shown: Sequence[str]) -> Checked:
    """A component judged on one boolean it must declare true."""
    value = facts.known(name)
    return _node(
        facts, state_of(None if value is None else value is True), shown, [name] if value is None else []
    )


def _gated(facts: ElementFacts, name: str, shown: Sequence[str]) -> Checked | bool:
    """Whether a component is concerned by what it declares about ``name`` (True or False), or an
    unknown Checked when it does not say."""
    value = facts.known(name)
    if value is None:
        return _node(facts, State.UNKNOWN, shown, [name])
    return value is True


def _at_rest(facts: ComponentSecurity, ask: Ask) -> Checked | None:
    shown = ("encryption_at_rest", "data_classification", "personal_data")
    if ask.sensitive_only and facts.sensitive is not True:
        return (
            _node(facts, State.UNKNOWN, shown, ["data_classification"]) if facts.sensitive is None else None
        )
    return _flag(facts, "encryption_at_rest", shown)


def _public(facts: ComponentSecurity) -> Checked | None:
    shown = ("exposure", "authentication")
    exposure = facts.known("exposure")
    if exposure is None:
        return _node(facts, State.UNKNOWN, shown, ["exposure"])
    if exposure != "public":
        return None
    auth = authenticated(facts)
    return _node(facts, state_of(auth), shown, ["authentication"] if auth is None else [])


def _authorized(facts: ComponentSecurity) -> Checked | None:
    shown = ("sensitive_operations", "authorization")
    gate = _gated(facts, "sensitive_operations", shown)
    if not isinstance(gate, bool):
        return gate
    if not gate:
        return None
    model = facts.known("authorization")
    state = State.UNKNOWN if model is None else State.BAD if model == "none" else State.OK
    return _node(facts, state, shown, ["authorization"] if model is None else [])


def _management(facts: ComponentSecurity) -> Checked | None:
    shown = ("management_interface", "exposure")
    gate = _gated(facts, "management_interface", shown)
    if not isinstance(gate, bool):
        return gate
    if not gate:
        return None
    exposure = facts.known("exposure")
    state = State.UNKNOWN if exposure is None else State.BAD if exposure == "public" else State.OK
    return _node(facts, state, shown, ["exposure"] if exposure is None else [])


def _secrets(facts: ComponentSecurity, ask: Ask) -> Checked | None:
    rotation = ask.condition is Condition.SECRET_ROTATION
    name = "secret_rotation" if rotation else "secret_source"
    shown = ("secrets_required", name)
    gate = _gated(facts, "secrets_required", shown)
    if not isinstance(gate, bool):
        return gate
    if not gate:
        return None
    value = facts.known(name)
    if value is None:
        return _node(facts, State.UNKNOWN, shown, [name])
    return _node(facts, state_of(value is True if rotation else value in ask.approved), shown)


def _audit(facts: ComponentSecurity, ask: Ask) -> Checked | None:
    shown = ("sensitive_operations", "audit_logging")
    if ask.sensitive_only:
        gate = _gated(facts, "sensitive_operations", shown)
        if not isinstance(gate, bool):
            return gate
        if not gate:
            return None
    return _flag(facts, "audit_logging", shown)


def _classified(facts: ComponentSecurity) -> Checked:
    declared = facts.known("data_classification") is not None
    return _node(
        facts, state_of(declared), ("data_classification",), [] if declared else ["data_classification"]
    )


_JUDGES: dict[Condition, Callable[[ComponentSecurity, Ask], Checked | None]] = {
    Condition.AUTHENTICATION_ON_PUBLIC: lambda facts, _: _public(facts),
    Condition.AUTHORIZATION_ON_SENSITIVE: lambda facts, _: _authorized(facts),
    Condition.NO_PUBLIC_MANAGEMENT_INTERFACE: lambda facts, _: _management(facts),
    Condition.APPROVED_SECRET_SOURCE: _secrets,
    Condition.SECRET_ROTATION: _secrets,
    Condition.AUDIT_LOGGING: _audit,
}


def _component(node: Node, facts: ComponentSecurity, ask: Ask) -> Checked | None:
    """One component under one condition: its state, or None when it is not concerned."""
    condition = ask.condition
    if condition is Condition.DATA_CLASSIFICATION:
        return _classified(facts)
    if node.kind is NodeKind.EXTERNAL and condition is not Condition.NO_PUBLIC_MANAGEMENT_INTERFACE:
        return None  # a third party's own controls are not ours to model
    if condition is Condition.ENCRYPTION_AT_REST:
        return _at_rest(facts, ask) if node.kind in STORES else None
    judge_one = _JUDGES.get(condition)
    return judge_one(facts, ask) if judge_one is not None else None


def _sensitive_flow(context: SecurityContext, connection: Connection) -> bool | None:
    """Whether a flow carries sensitive data: as it declares, else as the stores it moves data of
    declare; None when that is not established."""
    declared = context.connection_facts[connection.id].sensitive
    if declared is not None:
        return declared
    ends = [context.facts[connection.source_id], context.facts[connection.target_id]]
    if connection.kind in DATA_FLOWS and any(e.sensitive is True for e in ends):
        return True
    if all(e.sensitive is False for e in ends):
        return False
    return None


def _connections(context: SecurityContext, ask: Ask) -> list[Checked]:
    ids = None if ask.nodes is None else {n.id for n in ask.nodes}
    checked = []
    for connection in context.connections:
        if not connection.kind.communicates:
            continue
        if ids is not None and connection.source_id not in ids and connection.target_id not in ids:
            continue
        link = context.connection_facts[connection.id]
        shown = protocol_evidence(connection) + evidence(
            link, ("tls", "data_classification", "personal_data")
        )
        if ask.sensitive_only:
            sensitive = _sensitive_flow(context, connection)
            if sensitive is None:
                unclassified = (f"{connection.id}.configuration.data_classification",)
                checked.append(Checked(connection.id, True, State.UNKNOWN, shown, unclassified))
                continue
            if not sensitive:
                continue
        protected = transport_protected(connection, link)
        missing = (f"{connection.id}.configuration.tls",) if protected is None else ()
        checked.append(Checked(connection.id, True, state_of(protected), shown, missing))
    return checked


def judge(context: SecurityContext, ask: Ask) -> Judged:
    """The verdict for one condition, from what the architecture declares."""
    if ask.condition is Condition.ENCRYPTION_IN_TRANSIT:
        checked = _connections(context, ask)
    else:
        nodes = context.components if ask.nodes is None else ask.nodes
        checked = [c for n in nodes if (c := _component(n, context.facts[n.id], ask)) is not None]
    concerned, must = WHAT[ask.condition]
    return conclude(checked, concerned, must)


def report(
    meta: AnalyzerMeta, judged: Judged, condition: Condition, subject: Subject
) -> tuple[CheckResult, SecurityFinding | None]:
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
        requirement_id=subject.requirement_id,
        policy_rule=subject.policy_rule,
        check_key=subject.key,
    )
