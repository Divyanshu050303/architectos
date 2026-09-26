"""The security facts of an architecture's elements, as it declares them.

Security inputs are optional configuration properties of the IR (see
``core/architecture_ir/configuration.py``):

- components: ``exposure`` (``public``/``internal``/``private``), ``authentication`` (``none`` or
  a mechanism), ``authorization`` (``none`` or a model), ``sensitive_operations``,
  ``management_interface``, ``data_classification``, ``personal_data``, ``encryption_at_rest``,
  ``secrets_required``, ``secret_source``, ``secret_rotation``, ``audit_logging``;
- connections: ``tls``, ``authentication``, ``data_classification``, ``personal_data``, with the
  connection's own ``protocol``, ``kind`` and direction;
- boundaries: ``boundary_type`` (``trust_zone``) and ``trust_level``.

A mechanism's name is what the architecture says, never proof that it is implemented correctly. A
property that is absent is not modeled: never assumed present, never assumed absent. Facts keep
their provenance (``core/domain/facts.py``).
"""

from dataclasses import dataclass
from typing import Self

from core.architecture_ir.edge import Connection
from core.architecture_ir.node import Node
from core.domain.facts import ElementFacts

from .values import sensitivity

NODE_PROPERTIES = (
    "exposure",
    "authentication",
    "authorization",
    "sensitive_operations",
    "management_interface",
    "data_classification",
    "personal_data",
    "encryption_at_rest",
    "secrets_required",
    "secret_source",
    "secret_rotation",
    "audit_logging",
)
CONNECTION_PROPERTIES = ("tls", "authentication", "data_classification", "personal_data")
BOUNDARY_PROPERTIES = ("boundary_type", "trust_level")


@dataclass(frozen=True, slots=True)
class _Classified(ElementFacts):
    @property
    def sensitive(self) -> bool | None:
        """Whether it holds or carries sensitive data, by its declared classification and personal
        data; None when not established."""
        return sensitivity(self.known("data_classification"), self.known("personal_data"))


@dataclass(frozen=True, slots=True)
class ComponentSecurity(_Classified):
    """A component's security facts (``element_id`` is the node's id)."""

    @classmethod
    def of(cls, node: Node) -> Self:
        return cls.read(node, NODE_PROPERTIES)


@dataclass(frozen=True, slots=True)
class ConnectionSecurity(_Classified):
    """A connection's security facts (``element_id`` is the connection's id)."""

    @classmethod
    def of(cls, connection: Connection) -> Self:
        return cls.read(connection, CONNECTION_PROPERTIES)


@dataclass(frozen=True, slots=True)
class BoundarySecurity(ElementFacts):
    """A boundary's trust facts (``element_id`` is the boundary's id)."""

    @classmethod
    def of(cls, boundary: Node) -> Self:
        return cls.read(boundary, BOUNDARY_PROPERTIES)

    @property
    def trust_zone(self) -> bool:
        return self.known("boundary_type") == "trust_zone"
