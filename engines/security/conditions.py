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
from enum import StrEnum
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence
from core.domain.facts import ElementFacts
from core.domain.security.inputs import STORES, ComponentSecurity
from core.domain.security.results import (
    MAX_ELEMENTS,
    CheckResult,
    CheckSource,
    Condition,
    FindingType,
    SecurityFinding,
)
from core.domain.validation.results import Severity, Verdict

from .context import SecurityContext
from .engine import AnalyzerMeta, names
from .support import authenticated, evidence, finding, protocol_evidence, transport_protected

SHOWN = 100  # evidence items shown on a verdict (every element concerned is listed in its ids)
DATA_FLOWS = frozenset(
    {ConnectionKind.DATA_ACCESS, ConnectionKind.REPLICATION, ConnectionKind.PUBLISH, ConnectionKind.CONSUME}
)


class State(StrEnum):
    OK = "ok"
    BAD = "bad"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Checked:
    """One concerned element: its state, the declared facts it was judged on, what is missing."""

    element_id: str
    connection: bool
    state: State
    evidence: tuple[Evidence, ...] = ()
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Judged:
    verdict: Verdict
    explanation: str
    node_ids: tuple[str, ...] = ()  # every concerned element
    connection_ids: tuple[str, ...] = ()
    actual: tuple[Evidence, ...] = ()  # the facts of the elements that decide the verdict
    missing: tuple[str, ...] = ()
    offending_nodes: tuple[str, ...] = ()  # bad (violated) or unknown (not verifiable)
    offending_connections: tuple[str, ...] = ()


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


def _state(value: bool | None) -> State:
    return State.UNKNOWN if value is None else State.OK if value else State.BAD


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
        facts, _state(None if value is None else value is True), shown, [name] if value is None else []
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
    return _node(facts, _state(auth), shown, ["authentication"] if auth is None else [])


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
    return _node(facts, _state(value is True if rotation else value in ask.approved), shown)


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
        facts, _state(declared), ("data_classification",), [] if declared else ["data_classification"]
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
        checked.append(Checked(connection.id, True, _state(protected), shown, missing))
    return checked


def judge(context: SecurityContext, ask: Ask) -> Judged:
    """The verdict for one condition, from what the architecture declares."""
    if ask.condition is Condition.ENCRYPTION_IN_TRANSIT:
        checked = _connections(context, ask)
    else:
        nodes = context.components if ask.nodes is None else ask.nodes
        checked = [c for n in nodes if (c := _component(n, context.facts[n.id], ask)) is not None]
    concerned, must = WHAT[ask.condition]
    if not checked:
        return Judged(Verdict.NOT_APPLICABLE, f"No {concerned} in scope.")
    bad = [c for c in checked if c.state is State.BAD]
    unknown = [c for c in checked if c.state is State.UNKNOWN]
    deciding = bad or unknown
    fields: dict[str, Any] = {
        "node_ids": tuple(c.element_id for c in checked if not c.connection)[:MAX_ELEMENTS],
        "connection_ids": tuple(c.element_id for c in checked if c.connection)[:MAX_ELEMENTS],
        "actual": tuple(e for c in (deciding or checked) for e in c.evidence)[:SHOWN],
        "missing": tuple(m for c in unknown for m in c.missing)[:MAX_ELEMENTS],
        "offending_nodes": tuple(c.element_id for c in deciding if not c.connection),
        "offending_connections": tuple(c.element_id for c in deciding if c.connection),
    }
    if bad:
        ids = [c.element_id for c in bad]
        verb = "does" if len(ids) == 1 else "do"
        return Judged(Verdict.VIOLATED, f"{names(ids)} {verb} not declare {must}.", **fields)
    if unknown:
        ids = [c.element_id for c in unknown]
        verb = "does" if len(ids) == 1 else "do"
        return Judged(
            Verdict.NOT_VERIFIABLE,
            f"{names(ids)} {verb} not declare enough to decide (missing evidence is never success).",
            **fields,
        )
    return Judged(Verdict.SATISFIED, f"All {len(checked)} {concerned} concerned declare {must}.", **fields)


@dataclass(frozen=True, slots=True)
class Subject:
    """What a verdict is about: its check key, how it is named, and its requirement (with the words
    that mapped it) or its policy rule."""

    key: str
    title: str
    severity: Severity  # of a violation
    requirement_id: str | None = None
    policy_rule: str | None = None
    mapping: str | None = None


def report(
    meta: AnalyzerMeta, judged: Judged, condition: Condition, subject: Subject
) -> tuple[CheckResult, SecurityFinding | None]:
    """The check for one verdict, and a finding when it needs a look: a violation (at the subject's
    severity) or a verdict that cannot be reached (one step lower, at least low)."""
    requirement = subject.requirement_id is not None
    source = CheckSource.REQUIREMENT if requirement else CheckSource.POLICY
    check = CheckResult(
        subject.key,
        source,
        condition,
        judged.verdict,
        judged.explanation,
        judged.node_ids,
        judged.connection_ids,
        judged.actual,
        judged.missing,
        subject.requirement_id,
        subject.policy_rule,
        subject.mapping,
    )
    if judged.verdict not in {Verdict.VIOLATED, Verdict.NOT_VERIFIABLE}:
        return check, None
    violated = judged.verdict is Verdict.VIOLATED
    types = {
        (True, True): FindingType.REQUIREMENT_VIOLATED,
        (True, False): FindingType.REQUIREMENT_NOT_EVALUABLE,
        (False, True): FindingType.POLICY_VIOLATED,
        (False, False): FindingType.POLICY_NOT_EVALUABLE,
    }
    order = list(Severity)
    lower = order[min(order.index(subject.severity) + 1, order.index(Severity.LOW))]
    what = WHAT[condition][1]
    return check, finding(
        meta,
        types[(requirement, violated)],
        subject.severity if violated else lower,
        Certainty.MODELED,
        title=f"{subject.title} is {'violated' if violated else 'not verifiable'}: {what}",
        explanation=judged.explanation
        + ("" if violated else " It is not reported as met: what it needs is not declared."),
        recommendation=f"Review the elements named against it ({what})."
        if violated
        else f"Declare {what} (or what decides whether it applies) where it is missing.",
        node_ids=judged.offending_nodes,
        connection_ids=judged.offending_connections,
        evidence=judged.actual,
        missing=judged.missing,
        requirement_id=subject.requirement_id,
        policy_rule=subject.policy_rule,
        check_key=subject.key,
    )
