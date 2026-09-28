"""The component catalog's use cases: reading categories, entries and exact specification versions,
and evaluating a configuration against a specification outside any architecture.

The catalog is reference data shared by every organization (no tenant's data is in it): reading it
needs a signed-in user, nothing more. It is read-only here — a specification changes by review of
its file and a new version (``make catalog-lock``), never through the API. Evaluating a
configuration stores nothing; evaluating an architecture's components is part of its validation
runs, under the project's authorization.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from core.architecture_ir.component import NodeKind
from core.architecture_ir.serialization import from_dict
from core.architecture_ir.versioning import IR_SCHEMA_VERSION

from .entities import SupportStatus
from .evaluation import ConstraintEngine, ConstraintEvaluation
from .repository import CategorySummary, ComponentCatalog
from .specifications import ComponentSpecification

EVALUATED_NODE = "configuration"


class ComponentService:
    def __init__(self, catalog: ComponentCatalog, engine: ConstraintEngine) -> None:
        self._catalog = catalog
        self._engine = engine

    @property
    def fingerprint(self) -> str:
        return self._catalog.fingerprint

    def categories(self) -> tuple[CategorySummary, ...]:
        return self._catalog.categories()

    def list(
        self, *, category: str | None = None, status: SupportStatus | None = None
    ) -> tuple[ComponentSpecification, ...]:
        return self._catalog.list(category=category, status=status)

    def get(self, component_id: str, version: int | None = None) -> ComponentSpecification:
        """The current specification, or the exact version asked for (ComponentNotFound)."""
        return self._catalog.get(component_id, version)

    def history(self, component_id: str) -> tuple[ComponentSpecification, ...]:
        return self._catalog.history(component_id)

    def evaluate(
        self,
        component_id: str,
        *,
        version: int | None,
        kind: NodeKind,
        technology_version: str | None,
        values: Mapping[str, Any],
        unknown: Sequence[str],
    ) -> ConstraintEvaluation:
        """A configuration, as an architecture would state it, against one specification version.
        The values are read by the Architecture IR's own rules (types, ranges, the kind's
        properties): a value the IR refuses is refused here (InvalidArchitecture)."""
        spec = self._catalog.get(component_id, version)
        document = {
            "schema_version": IR_SCHEMA_VERSION,
            "name": spec.name,
            "nodes": [
                {
                    "id": EVALUATED_NODE,
                    "kind": kind.value,
                    "name": spec.name,
                    "technology": {"name": spec.technology, "version": technology_version},
                    "component": spec.id,
                    "configuration": {"values": dict(values), "unknown": list(unknown)},
                }
            ],
        }
        [node] = from_dict(document).nodes
        return self._engine.evaluate_node(node, spec, self._catalog)
