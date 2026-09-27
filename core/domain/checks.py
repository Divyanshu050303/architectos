"""A requirement or policy rule checked as one fixed, machine-checkable condition — shared by the
engines that judge declared configuration (security, observability). Each engine names its own
conditions (a ``StrEnum`` with an ``unsupported`` member) and its own malformed-result error.

A check is ``satisfied`` or ``violated`` only by modeled evidence; ``not_verifiable`` when anything
deciding it is not declared, or when the requirement maps to no supported condition (never a pass);
``not_applicable`` when nothing it concerns exists. Its evidence never shows a secret's value.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Self

from core.architecture_ir.model import MAX_CONNECTIONS, MAX_NODES
from core.domain.engine_results import MAX_ID, Evidence, InvalidEngineResult, read_evidence, text_problem
from core.domain.redaction import shows_a_secret
from core.domain.validation.results import Severity, Verdict

UNSUPPORTED = "unsupported"  # the member every engine's conditions have: no supported condition
MAX_ELEMENTS = MAX_NODES + MAX_CONNECTIONS  # a check can concern every element of the architecture


class CheckSource(StrEnum):
    REQUIREMENT = "requirement"
    POLICY = "policy"


def ids_problem(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ELEMENTS:
        return name
    return None if all(isinstance(v, str) and 0 < len(v) <= MAX_ID for v in values) else name


def evidence_problem(values: object, name: str = "evidence") -> str | None:
    """Well-formed evidence, bounded, and never showing a secret's value."""
    if not isinstance(values, tuple) or len(values) > MAX_ELEMENTS:
        return name
    ok = all(
        isinstance(e, Evidence) and isinstance(e.label, str) and isinstance(e.value, str) for e in values
    )
    return None if ok and not shows_a_secret(values) else name


@dataclass(frozen=True, slots=True)
class Check:
    """The verdict for one requirement or policy rule, from modeled evidence only. Subclasses set
    ``CONDITIONS`` (the engine's conditions) and ``ERROR`` (its malformed-result error)."""

    CONDITIONS: ClassVar[type[StrEnum]]
    ERROR: ClassVar[type[InvalidEngineResult]]

    key: str  # "requirement.<ref>[.<condition>]" or "policy.<field>"
    source: CheckSource
    condition: StrEnum
    verdict: Verdict
    explanation: str
    node_ids: tuple[str, ...] = ()  # what it was checked on
    connection_ids: tuple[str, ...] = ()
    actual: tuple[Evidence, ...] = ()  # the modeled values it was judged on
    missing: tuple[str, ...] = ()
    requirement_id: str | None = None
    policy_rule: str | None = None
    mapping: str | None = None  # how a requirement's words became the condition

    def __post_init__(self) -> None:
        for name in ("node_ids", "connection_ids", "missing"):
            value = getattr(self, name)
            if isinstance(value, tuple):
                object.__setattr__(self, name, tuple(sorted(set(value))))
        requirement = self.source is CheckSource.REQUIREMENT
        unsupported = self.condition == UNSUPPORTED
        problems = [
            text_problem(self.key, "key"),
            None if isinstance(self.source, CheckSource) else "source",
            None if isinstance(self.condition, self.CONDITIONS) else "condition",
            None if isinstance(self.verdict, Verdict) else "verdict",
            text_problem(self.explanation, "explanation"),
            ids_problem(self.node_ids, "node_ids"),
            ids_problem(self.connection_ids, "connection_ids"),
            evidence_problem(self.actual, "actual"),
            ids_problem(self.missing, "missing"),
            # missing evidence, or an unsupported condition, is never success
            None if self.verdict is not Verdict.SATISFIED or not (self.missing or unsupported) else "verdict",
            None if not unsupported or self.verdict is Verdict.NOT_VERIFIABLE else "verdict",
            None if (self.requirement_id is not None) == requirement else "requirement_id",
            None if (self.policy_rule is not None) == (not requirement) else "policy_rule",
            text_problem(self.requirement_id, "requirement_id", required=False),
            text_problem(self.policy_rule, "policy_rule", required=False),
            text_problem(self.mapping, "mapping", required=False),
        ]
        found = [p for p in problems if p]
        if found:
            raise self.ERROR(details={"fields": found})

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source.value,
            "condition": self.condition.value,
            "verdict": self.verdict.value,
            "explanation": self.explanation,
            "node_ids": list(self.node_ids),
            "connection_ids": list(self.connection_ids),
            "actual": [e.to_dict() for e in self.actual],
            "missing": list(self.missing),
            "requirement_id": self.requirement_id,
            "policy_rule": self.policy_rule,
            "mapping": self.mapping,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(
                data["key"],
                CheckSource(data["source"]),
                cls.CONDITIONS(data["condition"]),
                Verdict(data["verdict"]),
                data["explanation"],
                tuple(data.get("node_ids") or ()),
                tuple(data.get("connection_ids") or ()),
                read_evidence(data.get("actual")),
                tuple(data.get("missing") or ()),
                data.get("requirement_id"),
                data.get("policy_rule"),
                data.get("mapping"),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise cls.ERROR(details={"fields": [type(error).__name__]}) from None


# --- judging a condition element by element ----------------------------------------------------

SHOWN = 100  # evidence items shown on a verdict (every element concerned is listed in its ids)


class State(StrEnum):
    """What one concerned element declares, for one condition."""

    OK = "ok"
    BAD = "bad"
    UNKNOWN = "unknown"


def state_of(value: bool | None) -> State:
    return State.UNKNOWN if value is None else State.OK if value else State.BAD


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
class Subject:
    """What a verdict is about: its check key, how it is named, the severity of a violation, and its
    requirement (with the words that mapped it) or its policy rule."""

    key: str
    title: str
    severity: Severity
    requirement_id: str | None = None
    policy_rule: str | None = None
    mapping: str | None = None


def _names(ids: Iterable[str], shown: int = 20) -> str:
    listed = list(ids)
    head = ", ".join(listed[:shown])
    return head if len(listed) <= shown else f"{head} and {len(listed) - shown} more"


def conclude(checked: list[Checked], concerned: str, must: str) -> Judged:
    """The verdict over every concerned element: violated when any is bad (a known violation stands
    even when others are unknown); not verifiable when none is bad but any is unknown (missing
    evidence is never success); not applicable when nothing is concerned; satisfied only when every
    concerned element declares what is asked."""
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
        return Judged(Verdict.VIOLATED, f"{_names(ids)} {verb} not declare {must}.", **fields)
    if unknown:
        ids = [c.element_id for c in unknown]
        verb = "does" if len(ids) == 1 else "do"
        return Judged(
            Verdict.NOT_VERIFIABLE,
            f"{_names(ids)} {verb} not declare enough to decide (missing evidence is never success).",
            **fields,
        )
    return Judged(Verdict.SATISFIED, f"All {len(checked)} {concerned} concerned declare {must}.", **fields)


def check_of[C: Check](cls: type[C], judged: Judged, condition: StrEnum, subject: Subject) -> C:
    """The engine's check for one verdict about ``subject``."""
    requirement = subject.requirement_id is not None
    return cls(
        subject.key,
        CheckSource.REQUIREMENT if requirement else CheckSource.POLICY,
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


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a verdict that needs a look is reported as: the finding type's name (``REQUIREMENT_`` or
    ``POLICY_`` + ``VIOLATED`` or ``NOT_EVALUABLE``, every engine's names), its severity and words."""

    type_name: str
    violated: bool
    severity: Severity
    title: str
    explanation: str
    recommendation: str


def outcome(judged: Judged, subject: Subject, what: str) -> Outcome | None:
    """None when the verdict needs no finding; else a violation (at the subject's severity) or a
    verdict that cannot be reached (one step lower, at least low) — never reported as met."""
    if judged.verdict not in {Verdict.VIOLATED, Verdict.NOT_VERIFIABLE}:
        return None
    violated = judged.verdict is Verdict.VIOLATED
    source = "REQUIREMENT" if subject.requirement_id is not None else "POLICY"
    order = list(Severity)
    lower = order[min(order.index(subject.severity) + 1, order.index(Severity.LOW))]
    return Outcome(
        f"{source}_{'VIOLATED' if violated else 'NOT_EVALUABLE'}",
        violated,
        subject.severity if violated else lower,
        f"{subject.title} is {'violated' if violated else 'not verifiable'}: {what}",
        judged.explanation
        + ("" if violated else " It is not reported as met: what it needs is not declared."),
        f"Review the elements named against it ({what})."
        if violated
        else f"Declare {what} (or what decides whether it applies) where it is missing.",
    )
