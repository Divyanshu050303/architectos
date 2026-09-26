"""Result parts the deterministic engines share (capacity, cost): labelled evidence, what could not
be calculated and why, limitations of a whole analysis, and the identity of the model set that ran.
Each engine's own error for a malformed result subclasses ``InvalidEngineResult``."""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Self

from core.domain.errors import DomainError

CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_TEXT = 2000
MAX_ID = 256
MAX_ITEMS = 200


class InvalidEngineResult(DomainError):
    """A result part with malformed fields (an engine bug, or a corrupted record).
    ``details`` = {"fields": [...]}."""

    code = "invalid_engine_result"
    message = "An analysis result is malformed."


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidEngineResult(details={"fields": found})


def text_problem(value: object, name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    ok = isinstance(value, str) and (value.strip() or not required) and len(value) <= MAX_TEXT
    return None if ok else name


def ids_problem(values: object, name: str) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return name
    return None if all(isinstance(v, str) and 0 < len(v) <= MAX_ID for v in values) else name


def pattern_problem(value: object, pattern: re.Pattern[str], name: str) -> str | None:
    return None if isinstance(value, str) and pattern.fullmatch(value) else name


@dataclass(frozen=True, slots=True)
class Evidence:
    label: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "value": self.value}


def evidence_problem(values: object) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return "evidence"
    ok = all(
        isinstance(e, Evidence) and isinstance(e.label, str) and isinstance(e.value, str) for e in values
    )
    return None if ok else "evidence"


def read_evidence(data: Any) -> tuple[Evidence, ...]:
    return tuple(Evidence(e["label"], e["value"]) for e in data or ())


@dataclass(frozen=True, slots=True)
class Unsupported:
    """A calculation the analysis could not make, and why."""

    element_id: str
    code: str
    message: str
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.missing, tuple):
            object.__setattr__(self, "missing", tuple(sorted(set(self.missing))))
        check(
            [
                None
                if isinstance(self.element_id, str) and 0 < len(self.element_id) <= MAX_ID
                else "element_id",
                pattern_problem(self.code, CODE, "code"),
                text_problem(self.message, "message"),
                ids_problem(self.missing, "missing"),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "code": self.code,
            "message": self.message,
            "missing": list(self.missing),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(data["element_id"], data["code"], data["message"], tuple(data.get("missing") or ()))
        except (KeyError, TypeError) as error:
            raise InvalidEngineResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class Limitation:
    """Something no model of the analysis could establish (e.g. no catalog, no measurements)."""

    code: str
    message: str

    def __post_init__(self) -> None:
        check([pattern_problem(self.code, CODE, "code"), text_problem(self.message, "message")])

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        try:
            return cls(data["code"], data["message"])
        except (KeyError, TypeError) as error:
            raise InvalidEngineResult(details={"fields": [type(error).__name__]}) from None


@dataclass(frozen=True, slots=True)
class ModelSet:
    """Which models ran: a version derived from their ids and versions."""

    version: str
    models: tuple[tuple[str, int], ...] = ()

    @classmethod
    def of(cls, models: Iterable[tuple[str, int]]) -> ModelSet:
        ordered = tuple(sorted(set(models)))
        return cls(hashlib.sha256(json.dumps(ordered).encode()).hexdigest()[:16], ordered)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "models": [list(m) for m in self.models]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelSet:
        return cls(data["version"], tuple((m[0], m[1]) for m in data["models"]))
