"""A project's architecture policy: the few organizational constraints the validation and security
engines enforce deterministically. Deliberately minimal and typed (no expression language, no
free-form rules): each field maps to exactly one rule, and a policy that says nothing checks nothing.

Validation:

- ``allowed_technologies``: when not empty, every stated technology must be one of these;
- ``prohibited_technologies``: never used (a technology cannot be both allowed and prohibited);
- ``allowed_regions``: when not empty, every stated region must be one of these;
- ``require_tls``: every communicating connection must be encrypted in transit (also reported by
  the security engine);
- ``max_components``: at most this many components (boundaries are not components).

Security (what the architecture must model; see the security engine):

- ``require_encryption_at_rest``: every data store declares encryption at rest;
- ``require_authentication_on_public``: every component declared public declares an
  authentication mechanism;
- ``require_authorization_on_sensitive``: every component performing sensitive operations declares
  an authorization model;
- ``prohibit_public_management_interfaces``: no management interface is declared public;
- ``approved_secret_sources``: when not empty, every component needing secrets gets them from one of
  these (``secret_manager``, ``environment``, ``file``, ``configuration``, ``hardcoded``);
- ``require_secret_rotation``: every component needing secrets declares their rotation;
- ``require_audit_logging``: every component (third parties aside) declares audit logging;
- ``require_data_classification``: every component declares a data classification.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Self

from core.architecture_ir.component import TECHNOLOGY_NAME
from core.architecture_ir.configuration import REGION, SECRET_SOURCES
from core.architecture_ir.model import MAX_NODES

from .errors import InvalidArchitecturePolicy

MAX_POLICY_ENTRIES = 100
SECURITY_FLAGS = (
    "require_encryption_at_rest",
    "require_authentication_on_public",
    "require_authorization_on_sensitive",
    "prohibit_public_management_interfaces",
    "require_secret_rotation",
    "require_audit_logging",
    "require_data_classification",
)
FIELDS = frozenset(
    {
        "allowed_technologies",
        "prohibited_technologies",
        "allowed_regions",
        "require_tls",
        "max_components",
        "approved_secret_sources",
        *SECURITY_FLAGS,
    }
)
_SECRET_SOURCE = re.compile("^(" + "|".join(sorted(SECRET_SOURCES)) + ")$")


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
    require_encryption_at_rest: bool = False
    require_authentication_on_public: bool = False
    require_authorization_on_sensitive: bool = False
    prohibit_public_management_interfaces: bool = False
    approved_secret_sources: frozenset[str] = frozenset()
    require_secret_rotation: bool = False
    require_audit_logging: bool = False
    require_data_classification: bool = False

    def __post_init__(self) -> None:
        tech = TECHNOLOGY_NAME
        for field, pattern in (
            ("allowed_technologies", tech),
            ("prohibited_technologies", tech),
            ("allowed_regions", REGION),
            ("approved_secret_sources", _SECRET_SOURCE),
        ):
            object.__setattr__(self, field, _names(getattr(self, field), field, pattern))
        if self.allowed_technologies & self.prohibited_technologies:
            raise InvalidArchitecturePolicy(
                details={"field": "prohibited_technologies", "reason": "also_allowed"}
            )
        for flag in ("require_tls", *SECURITY_FLAGS):
            if not isinstance(getattr(self, flag), bool):
                raise InvalidArchitecturePolicy(details={"field": flag, "reason": "not_a_boolean"})
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
            approved_secret_sources=raw.get("approved_secret_sources", ()),
            **{flag: raw.get(flag, False) for flag in SECURITY_FLAGS},
        )

    def to_dict(self) -> dict[str, Any]:
        """Canonical: sorted lists, every field present (also what the validation fingerprint hashes)."""
        return {
            "allowed_technologies": sorted(self.allowed_technologies),
            "prohibited_technologies": sorted(self.prohibited_technologies),
            "allowed_regions": sorted(self.allowed_regions),
            "require_tls": self.require_tls,
            "max_components": self.max_components,
            "approved_secret_sources": sorted(self.approved_secret_sources),
            **{flag: getattr(self, flag) for flag in SECURITY_FLAGS},
        }
