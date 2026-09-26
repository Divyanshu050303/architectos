"""A project's architecture policy: the few organizational constraints the validation engine
enforces deterministically. Deliberately minimal and typed (no expression language, no free-form
rules): each field maps to exactly one policy rule, and a policy that says nothing checks nothing.

- ``allowed_technologies``: when not empty, every stated technology must be one of these;
- ``prohibited_technologies``: never used (a technology cannot be both allowed and prohibited);
- ``allowed_regions``: when not empty, every stated region must be one of these;
- ``require_tls``: every communicating connection must be encrypted in transit;
- ``max_components``: at most this many components (boundaries are not components).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Self

from core.architecture_ir.component import TECHNOLOGY_NAME
from core.architecture_ir.configuration import REGION
from core.architecture_ir.model import MAX_NODES

from .errors import InvalidArchitecturePolicy

MAX_POLICY_ENTRIES = 100
FIELDS = frozenset(
    {"allowed_technologies", "prohibited_technologies", "allowed_regions", "require_tls", "max_components"}
)


def _names(raw: object, field: str, pattern: Any) -> frozenset[str]:
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise InvalidArchitecturePolicy(details={"field": field, "reason": "not_a_list"})
    values = list(raw)
    if len(values) > MAX_POLICY_ENTRIES:
        raise InvalidArchitecturePolicy(details={"field": field, "reason": "too_many"})
    names: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise InvalidArchitecturePolicy(details={"field": field, "reason": "invalid_value"})
        name = value.strip().lower()
        if not pattern.fullmatch(name):
            raise InvalidArchitecturePolicy(details={"field": field, "reason": "invalid_value"})
        names.add(name)
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class ArchitecturePolicy:
    allowed_technologies: frozenset[str] = frozenset()
    prohibited_technologies: frozenset[str] = frozenset()
    allowed_regions: frozenset[str] = frozenset()
    require_tls: bool = False
    max_components: int | None = None

    def __post_init__(self) -> None:
        tech = TECHNOLOGY_NAME
        for field, pattern in (
            ("allowed_technologies", tech),
            ("prohibited_technologies", tech),
            ("allowed_regions", REGION),
        ):
            object.__setattr__(self, field, _names(getattr(self, field), field, pattern))
        if self.allowed_technologies & self.prohibited_technologies:
            raise InvalidArchitecturePolicy(
                details={"field": "prohibited_technologies", "reason": "also_allowed"}
            )
        if not isinstance(self.require_tls, bool):
            raise InvalidArchitecturePolicy(details={"field": "require_tls", "reason": "not_a_boolean"})
        limit = self.max_components
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_NODES
        ):
            raise InvalidArchitecturePolicy(details={"field": "max_components", "reason": "out_of_range"})

    @property
    def is_empty(self) -> bool:
        """A policy that constrains nothing."""
        return self == ArchitecturePolicy()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Self:
        if not isinstance(raw, Mapping):
            raise InvalidArchitecturePolicy(details={"field": None, "reason": "not_an_object"})
        unknown = sorted(set(raw) - FIELDS)
        if unknown:
            raise InvalidArchitecturePolicy(details={"field": unknown[0], "reason": "unknown"})
        return cls(
            allowed_technologies=raw.get("allowed_technologies", ()),
            prohibited_technologies=raw.get("prohibited_technologies", ()),
            allowed_regions=raw.get("allowed_regions", ()),
            require_tls=raw.get("require_tls", False),
            max_components=raw.get("max_components"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Canonical: sorted lists, every field present (also what the validation fingerprint hashes)."""
        return {
            "allowed_technologies": sorted(self.allowed_technologies),
            "prohibited_technologies": sorted(self.prohibited_technologies),
            "allowed_regions": sorted(self.allowed_regions),
            "require_tls": self.require_tls,
            "max_components": self.max_components,
        }
