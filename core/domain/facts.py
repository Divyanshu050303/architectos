"""What an architecture element (a node or a connection) declares about itself, as the engines read
it (reliability, security).

Engine inputs are optional configuration properties of the IR (``core/architecture_ir/
configuration.py``). Each fact keeps where it came from: ``declared`` (a value in the
configuration) or ``unknown`` (the configuration says the value is not known), the provenance of
the property when the architecture records one (e.g. ``terraform``), and whether it was inferred. A
property that is absent is simply not a fact: it is never filled in.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, Self

from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.provenance import Provenance
from core.domain.capacity.results import Source
from core.domain.engine_results import Evidence
from core.domain.requirements.value_objects import decimal_to_str


def text(value: ConfigValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return decimal_to_str(value)
    if isinstance(value, tuple):
        return ", ".join(value)
    return str(value)


@dataclass(frozen=True, slots=True)
class Fact:
    """One property of one element."""

    name: str
    value: ConfigValue | None  # None exactly when the source is unknown
    source: Source
    provenance: str | None = None  # e.g. "terraform", "user_edit"
    inferred: bool = False  # the architecture says the value was inferred, not stated or read

    @property
    def proposed(self) -> bool:
        """Inferred, or proposed by a language model: not declared by a person or read from a system."""
        return self.inferred or self.provenance == "llm_proposal"

    @property
    def path(self) -> str:
        return f"configuration.{self.name}"

    def evidence(self) -> Evidence:
        shown = "unknown" if self.value is None else text(self.value)
        origin = ", ".join(x for x in (self.provenance, "inferred" if self.inferred else None) if x)
        return Evidence(self.path, shown + (f" ({origin})" if origin else ""))


class Element(Protocol):
    """What facts are read from: an IR node or connection."""

    @property
    def id(self) -> str: ...
    @property
    def configuration(self) -> Configuration: ...
    @property
    def provenance(self) -> Provenance | None: ...
    @property
    def field_provenance(self) -> Mapping[str, Provenance]: ...


@dataclass(frozen=True, slots=True)
class ElementFacts:
    element_id: str
    facts: dict[str, Fact] = field(default_factory=dict)

    @classmethod
    def read(cls, element: Element, names: Iterable[str]) -> Self:
        config, facts = element.configuration, {}
        for name in names:
            origin = element.field_provenance.get(f"configuration.{name}") or element.provenance
            provenance = origin.source.value if origin is not None else None
            inferred = origin is not None and origin.inferred
            if config.is_unknown(name):
                facts[name] = Fact(name, None, Source.UNKNOWN, provenance, inferred)
            elif (value := config.get(name)) is not None:
                facts[name] = Fact(name, value, Source.DECLARED, provenance, inferred)
        return cls(element.id, facts)

    def known(self, name: str) -> ConfigValue | None:
        fact = self.facts.get(name)
        return fact.value if fact is not None else None

    def number(self, name: str) -> Decimal | None:
        value = self.known(name)
        if isinstance(value, bool) or not isinstance(value, int | Decimal):
            return None
        return Decimal(value)

    def missing(self, names: Iterable[str]) -> tuple[str, ...]:
        """The properties among ``names`` without a known value (absent or unknown)."""
        return tuple(f"configuration.{n}" for n in names if self.known(n) is None)

    def evidence(self, names: Iterable[str]) -> tuple[Evidence, ...]:
        return tuple(self.facts[n].evidence() for n in names if n in self.facts)
