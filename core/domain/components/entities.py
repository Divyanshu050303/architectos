"""The vocabulary of the component knowledge base: categories, support states, hosting, the engines
that read a specification, and the **provenance** every claim of a specification carries.

**Categories** are a registry (``CATEGORIES``): each names the catalog directory its specifications
live in (``databases/postgresql``) and the Architecture IR node kinds a component of that category
may model. Adding a category is one entry here; no engine changes.

**Provenance** says where a claim comes from, and the kinds are never mixed up:

- ``documented``: stated by a cited source (the specification's ``sources``: name, reference,
  version or publication date, and the date it was retrieved);
- ``user_configured``: stated by a person for their architecture — not independently verified;
- ``measured``: observed on a system, citing the measurement — valid for that system only;
- ``estimated``: produced by a model or from stated assumptions — never a measurement;
- ``inferred``: reasoned from other facts, with the reasoning stated — never a documented fact;
- ``unknown``: no evidence; the claim stays unknown and is never filled in.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Self

from core.architecture_ir.component import NodeKind

from .errors import InvalidSpecification

CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_NAME = 100
MAX_TEXT = 2000
MAX_REFERENCE = 500
MAX_ITEMS = 100  # claims per section, sources, aliases, assumptions


class SupportStatus(StrEnum):
    """How far ArchitectOS supports a catalog entry — never implied by the entry's presence."""

    SUPPORTED = "supported"  # specified: capabilities, configuration, failure modes, signals, security
    PARTIAL = "partial"  # some claims (at least its capabilities); the rest is unknown
    PLANNED = "planned"  # listed only: nothing is claimed about it yet
    DEPRECATED = "deprecated"  # kept readable for historical architectures; not for new designs


class Hosting(StrEnum):
    MANAGED = "managed"  # operated by a provider as a service
    SELF_HOSTED = "self_hosted"  # operated by the architecture's owners


class Engine(StrEnum):
    """The analysis engines a specification field or claim is read by."""

    VALIDATION = "validation"
    CAPACITY = "capacity"
    COST = "cost"
    RELIABILITY = "reliability"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    SIMULATION = "simulation"
    EVOLUTION = "evolution"


class ProvenanceKind(StrEnum):
    DOCUMENTED = "documented"
    USER_CONFIGURED = "user_configured"
    MEASURED = "measured"
    ESTIMATED = "estimated"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    name: str
    directory: str  # the catalog directory: a specification's id starts with it
    node_kinds: frozenset[NodeKind]  # the IR node kinds a component of this category may model


_K = NodeKind
CATEGORIES: Mapping[str, Category] = {
    c.id: c
    for c in (
        Category("compute", "Compute", "compute", frozenset({_K.SERVICE, _K.WORKER, _K.BOUNDARY})),
        Category("database", "Databases", "databases", frozenset({_K.DATABASE, _K.CACHE})),
        Category("messaging", "Messaging", "messaging", frozenset({_K.QUEUE})),
        Category("storage", "Storage", "storage", frozenset({_K.STORAGE})),
        Category(
            "networking",
            "Networking",
            "networking",
            frozenset({_K.LOAD_BALANCER, _K.GATEWAY, _K.CDN, _K.EXTERNAL, _K.BOUNDARY}),
        ),
        Category("observability", "Observability", "observability", frozenset({_K.OBSERVABILITY})),
    )
}


# --- checking helpers ----------------------------------------------------------------------------


def check(problems: Iterable[str | None]) -> None:
    found = [p for p in problems if p]
    if found:
        raise InvalidSpecification(details={"component": None, "fields": found})


def within[T](path: str, build: Callable[[], T]) -> T:
    """Builds a part of a specification from untrusted data, naming the part's path in any error."""
    try:
        return build()
    except InvalidSpecification as error:
        fields = error.details.get("fields", []) if isinstance(error.details, dict) else []
        raise InvalidSpecification(
            details={"component": None, "fields": [f"{path}.{f}" for f in fields] or [path]}
        ) from None
    except KeyError as missing:
        name = f"{path}.{missing.args[0]}" if missing.args else path
        raise InvalidSpecification(details={"component": None, "fields": [name]}) from None
    except TypeError, ValueError, AttributeError, ArithmeticError:
        raise InvalidSpecification(details={"component": None, "fields": [path]}) from None


def text(value: object, name: str, limit: int = MAX_TEXT, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    return None if isinstance(value, str) and value.strip() and len(value) <= limit else name


def code(value: object, name: str) -> str | None:
    return None if isinstance(value, str) and CODE.fullmatch(value) else name


def texts(values: object, name: str, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        return name
    return None if all(text(v, name, limit) is None for v in values) else name


def items(values: object, kind: type, name: str) -> str | None:
    ok = isinstance(values, tuple) and len(values) <= MAX_ITEMS and all(isinstance(v, kind) for v in values)
    return None if ok else name


def unique(ids: Iterable[str], name: str) -> str | None:
    seen = list(ids)
    return None if len(seen) == len(set(seen)) else name


def strict(data: object, cls: type) -> Mapping[str, Any]:
    """An untrusted mapping with no key the class does not define (a typo is refused, not ignored)."""
    if not isinstance(data, Mapping):
        raise TypeError("not a mapping")
    known = {f.name for f in fields(cls) if not f.name.startswith("_")}
    extra = sorted(str(k) for k in data if k not in known)
    if extra:
        raise InvalidSpecification(details={"component": None, "fields": extra})
    return data


def as_date(value: object) -> date | None:
    """A date from YAML (already a date) or JSON (ISO text); ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError("not a date")


def as_tuple(value: object) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise TypeError("not a list")
    return tuple(value)


# --- provenance ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Source:
    """A cited source: a vendor's documentation page, a standard, a measurement report."""

    id: str  # referred to by provenance within the specification
    name: str
    reference: str  # a URL or a document reference
    version: str | None = None  # the documented technology or document version
    published: date | None = None
    retrieved: date | None = None  # when the claim was last checked against the source

    def __post_init__(self) -> None:
        check(
            [
                code(self.id, "id"),
                text(self.name, "name", MAX_NAME),
                text(self.reference, "reference", MAX_REFERENCE),
                text(self.version, "version", MAX_NAME, required=False),
                None if self.published is None or isinstance(self.published, date) else "published",
                None if self.retrieved is None or isinstance(self.retrieved, date) else "retrieved",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "reference": self.reference,
            "version": self.version,
            "published": self.published.isoformat() if self.published else None,
            "retrieved": self.retrieved.isoformat() if self.retrieved else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            data["id"],
            data["name"],
            data["reference"],
            data.get("version"),
            as_date(data.get("published")),
            as_date(data.get("retrieved")),
        )


_CITED = frozenset({ProvenanceKind.DOCUMENTED, ProvenanceKind.MEASURED})


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where one claim comes from. ``sources`` are ids of the specification's sources."""

    kind: ProvenanceKind
    sources: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    model: str | None = None  # the model (and version) that produced an estimate
    note: str | None = None

    def __post_init__(self) -> None:
        kind = self.kind
        check(
            [
                None if isinstance(kind, ProvenanceKind) else "kind",
                items(self.sources, str, "sources"),
                texts(self.assumptions, "assumptions"),
                text(self.model, "model", MAX_NAME, required=False),
                text(self.note, "note", required=False),
                # documented and measured claims cite what they rest on
                "sources" if kind in _CITED and not self.sources else None,
                # an estimate states its model or assumptions; an inference its reasoning
                "model"
                if kind is ProvenanceKind.ESTIMATED and not (self.model or self.assumptions)
                else None,
                "assumptions" if kind is ProvenanceKind.INFERRED and not self.assumptions else None,
                # an unknown claim rests on nothing; only an estimate has a model
                "sources" if kind is ProvenanceKind.UNKNOWN and self.sources else None,
                "model" if self.model and kind is not ProvenanceKind.ESTIMATED else None,
            ]
        )

    @classmethod
    def unknown(cls, note: str | None = None) -> Self:
        return cls(ProvenanceKind.UNKNOWN, note=note)

    @property
    def is_known(self) -> bool:
        return self.kind is not ProvenanceKind.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "sources": list(self.sources),
            "assumptions": list(self.assumptions),
            "model": self.model,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = strict(data, cls)
        return cls(
            ProvenanceKind(data["kind"]),
            as_tuple(data.get("sources")),
            as_tuple(data.get("assumptions")),
            data.get("model"),
            data.get("note"),
        )


def read_provenance(data: Mapping[str, Any]) -> Provenance:
    """A claim's provenance from untrusted data, its errors named under ``provenance``."""
    return within("provenance", lambda: Provenance.from_dict(data["provenance"]))
