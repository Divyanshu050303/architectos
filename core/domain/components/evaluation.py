"""The result of evaluating architecture components against their catalog specifications.

Each finding is one check of one node: a constraint of the node's specification, or a check of the
reference itself. Outcomes:

- ``pass``: the configured value satisfies the documented constraint;
- ``warning``: it does not satisfy a recommendation or a raisable default (or the specification is
  deprecated);
- ``violation``: it breaks a documented limit, an unsupported value, or the node is of a kind the
  specification does not model;
- ``cannot_evaluate``: something needed is unknown — the value is not stated or marked unknown, a
  condition cannot be decided, the limit itself is undocumented, the specification is only planned,
  or the component is not in the catalog. Never read as a pass;
- ``not_applicable``: a condition of the constraint does not hold for this configuration.

Only the constraints a specification states are evaluated: no throughput or capacity is concluded
from a configuration. Each finding names the specification version (``ref``) it used and the
constraint's evidence; the evaluation records every specification version and the catalog's
fingerprint, so it can be reproduced against exactly those versions.
"""

import builtins
import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from core.architecture_ir.configuration import ConfigValue
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity

from .entities import Provenance
from .repository import ComponentCatalog
from .specifications import ComponentSpecification

MODEL = ("constraints", 1)


class Outcome(StrEnum):
    PASS = "pass"  # noqa: S105 - an outcome, not a password
    WARNING = "warning"
    VIOLATION = "violation"
    CANNOT_EVALUATE = "cannot_evaluate"
    NOT_APPLICABLE = "not_applicable"


class Check(StrEnum):
    """Checks of the reference itself (a constraint's check is the constraint's id)."""

    NOT_IN_CATALOG = "component_not_in_catalog"
    PLANNED = "specification_planned"
    DEPRECATED = "specification_deprecated"
    KIND_NOT_MODELED = "node_kind_not_modeled"
    REQUIRED_VALUE_MISSING = "required_value_missing"


# The unit a property's canonical name carries (the IR puts units in names).
UNIT_SUFFIXES = (
    ("_bytes_per_second", "B/s"),
    ("_bytes", "B"),
    ("_seconds", "s"),
    ("_cores", "cores"),
    ("_ratio", "ratio"),
)
COUNTS = {"max_connections": "connections", "replicas": "replicas"}


def unit_of(prop: str | None) -> str | None:
    if prop is None:
        return None
    for suffix, unit in UNIT_SUFFIXES:
        if prop.endswith(suffix):
            return unit
    return COUNTS.get(prop)


def json_value(value: ConfigValue | Decimal | None) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def finding_id(component: str, node_id: str, check: str) -> str:
    """Stable: the same check of the same node against the same component has the same id."""
    digest = hashlib.sha256(json.dumps([component, node_id, check]).encode()).hexdigest()
    return f"cst_{digest[:20]}"


@dataclass(frozen=True, slots=True)
class ConstraintFinding:
    node_id: str
    component: str  # the node's catalog reference
    specification: str | None  # the specification version used (None: not in the catalog)
    check: str  # a constraint id, or a Check
    outcome: Outcome
    explanation: str
    constraint_type: str | None = None
    severity: Severity | None = None  # for violations and warnings
    property: str | None = None
    actual: Any = None  # the configured value (JSON), or None when not stated
    expected: str | None = None  # the condition, e.g. "between 60 and 1209600"
    remediation: str | None = None
    provenance: Provenance | None = None  # the constraint's evidence

    @builtins.property  # the field "property" shadows the builtin in this class
    def id(self) -> str:
        return finding_id(self.component, self.node_id, self.check)

    @builtins.property
    def unit(self) -> str | None:
        return unit_of(self.property)

    @builtins.property
    def sort_key(self) -> tuple[str, str]:
        return (self.node_id, self.check)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "component": self.component,
            "specification": self.specification,
            "check": self.check,
            "constraint_type": self.constraint_type,
            "outcome": self.outcome.value,
            "severity": self.severity.value if self.severity else None,
            "property": self.property,
            "actual": self.actual,
            "expected": self.expected,
            "unit": self.unit,
            "explanation": self.explanation,
            "remediation": self.remediation,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }


@dataclass(frozen=True, slots=True)
class ConstraintEvaluation:
    findings: tuple[ConstraintFinding, ...]
    specifications: Mapping[str, str]  # ref -> content hash, every specification version used
    catalog_fingerprint: str
    revision: RevisionInfo | None = None  # None: a configuration evaluated outside an architecture

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(sorted(self.findings, key=lambda f: f.sort_key)))
        object.__setattr__(self, "specifications", dict(sorted(self.specifications.items())))

    def summary(self) -> dict[str, int]:
        counts = Counter(f.outcome for f in self.findings)
        return {o.value: counts.get(o, 0) for o in Outcome}

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(self._content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _content(self) -> dict[str, Any]:
        revision = self.revision
        return {
            "model": {"name": MODEL[0], "version": MODEL[1]},
            "revision": None
            if revision is None
            else {
                "architecture_id": revision.architecture_id,
                "number": revision.number,
                "content_hash": revision.content_hash,
            },
            "catalog_fingerprint": self.catalog_fingerprint,
            "specifications": dict(self.specifications),
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content(), "fingerprint": self.fingerprint}


class ConstraintEngine(Protocol):
    def evaluate_architecture(
        self,
        ir: ArchitectureIR,
        revision: RevisionInfo,
        catalog: ComponentCatalog,
        versions: Mapping[str, int] | None = None,
    ) -> ConstraintEvaluation: ...

    def evaluate_node(
        self, node: Node, specification: ComponentSpecification, catalog: ComponentCatalog
    ) -> ConstraintEvaluation: ...
