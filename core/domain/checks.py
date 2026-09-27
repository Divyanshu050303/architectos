"""A requirement or policy rule checked as one fixed, machine-checkable condition — shared by the
engines that judge declared configuration (security, observability). Each engine names its own
conditions (a ``StrEnum`` with an ``unsupported`` member) and its own malformed-result error.

A check is ``satisfied`` or ``violated`` only by modeled evidence; ``not_verifiable`` when anything
deciding it is not declared, or when the requirement maps to no supported condition (never a pass);
``not_applicable`` when nothing it concerns exists. Its evidence never shows a secret's value.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Self

from core.architecture_ir.model import MAX_CONNECTIONS, MAX_NODES
from core.domain.engine_results import MAX_ID, Evidence, InvalidEngineResult, read_evidence, text_problem
from core.domain.redaction import shows_a_secret
from core.domain.validation.results import Verdict

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
