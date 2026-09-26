"""Completeness rules: what the architecture does not say. An unknown value (discovery could not
read it, nobody has decided it yet) is never filled in or guessed at; it is reported, so the
people responsible know what the other rules could not check."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.configuration import Configuration
from core.domain.validation.results import Category, Finding, Severity

from ..context import ValidationContext
from ..engine import Outcome, RuleMeta
from ..findings import finding
from .consistency import ALL_PROFILES


@dataclass(frozen=True, slots=True)
class UnknownValues:
    meta = RuleMeta(
        "completeness.unknown-values",
        1,
        "Unknown values",
        "Configuration properties marked as unknown are reported, one finding per element.",
        Category.COMPLETENESS,
        Severity.LOW,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        elements: list[tuple[str, str, str, Configuration]] = [
            *(("node", n.id, n.name, n.configuration) for n in context.ir.nodes),
            *(("connection", c.id, c.name or c.id, c.configuration) for c in context.ir.connections),
        ]
        return Outcome(
            tuple(
                self._unknown(element, element_id, name, sorted(configuration.unknown))
                for element, element_id, name, configuration in elements
                if configuration.unknown
            )
        )

    def _unknown(self, element: str, element_id: str, name: str, keys: list[str]) -> Finding:
        return finding(
            self.meta,
            "unknown_value",
            title=f"{len(keys)} unknown value{'s' if len(keys) > 1 else ''} on {name}",
            explanation=(
                f"The {element} {name!r} does not say what {', '.join(keys)} "
                f"{'are' if len(keys) > 1 else 'is'}. Rules that need these values cannot "
                "check them, and nothing assumes a default in their place."
            ),
            remediation="Provide the values, or confirm them from the running system.",
            entity_ids=[element_id],
            field_paths=[f"configuration.{key}" for key in keys],
            expected="a known value",
            actual="unknown",
        )


RULES = (UnknownValues(),)
