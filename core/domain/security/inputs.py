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

from core.architecture_ir.component import NodeKind
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


_STORES = frozenset(
    {NodeKind.DATABASE, NodeKind.CACHE, NodeKind.STORAGE, NodeKind.QUEUE, NodeKind.OBSERVABILITY}
)


def expected(kind: NodeKind, facts: ComponentSecurity) -> tuple[str, ...]:
    """The security properties a component of ``kind`` should model for its controls to be
    evaluable (what its coverage is judged on). A third party's own controls are not ours to model,
    only what it is trusted with; authorization and a secret source matter once the component says
    it performs sensitive operations or needs secrets. Policies may require more (audit logging,
    rotation), checked by their own rules."""
    if kind is NodeKind.EXTERNAL:
        names = ["data_classification"]
    else:
        names = ["exposure", "data_classification", "authentication", "secrets_required"]
    if kind in _STORES:
        names.append("encryption_at_rest")
    if facts.known("sensitive_operations") is True:
        names.append("authorization")
    if facts.known("secrets_required") is True:
        names.append("secret_source")
    return tuple(names)


@dataclass(frozen=True, slots=True)
class BoundarySecurity(ElementFacts):
    """A boundary's trust facts (``element_id`` is the boundary's id)."""

    @classmethod
    def of(cls, boundary: Node) -> Self:
        return cls.read(boundary, BOUNDARY_PROPERTIES)

    @property
    def trust_zone(self) -> bool:
        return self.known("boundary_type") == "trust_zone"
