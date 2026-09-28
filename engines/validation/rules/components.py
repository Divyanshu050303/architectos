"""Component rules: each node that refers to a catalog component, checked against the constraints its
specification documents (``engines/constraints``). The check itself is the constraint engine's; this
rule reports what it found as validation findings:

- a violated limit, an unsupported value, or a node kind the specification does not model: a
  finding with the constraint's severity;
- a recommendation or raisable default not met, or a deprecated specification: a finding with its
  (at most medium) severity;
- a check that cannot be evaluated (a value not stated or unknown, an undocumented limit, a
  specification only planned, a component not in the catalog): an ``info`` finding, so the unknown
  stays visible — never read as a pass.

Checks of one node's property with the same outcome are one finding (a finding's identity is its
rule, code, node and field), listing each check in its evidence. Passing and not-applicable checks
produce no finding; the specification versions used are recorded with the run
(``ValidationResult.components``)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.domain.components.evaluation import Check, ConstraintFinding, Outcome
from core.domain.validation.results import Category, Finding, Severity

from ..context import ValidationContext
from ..engine import Input, RuleMeta
from ..engine import Outcome as RuleOutcome
from ..findings import finding
from .consistency import ALL_PROFILES

CODES: Mapping[Outcome, str] = {
    Outcome.VIOLATION: "component_constraint_violated",
    Outcome.WARNING: "component_recommendation_not_met",
    Outcome.CANNOT_EVALUATE: "component_check_not_evaluable",
}
SPECIAL: Mapping[str, str] = {
    Check.KIND_NOT_MODELED.value: "component_kind_not_modeled",
    Check.DEPRECATED.value: "component_specification_deprecated",
    Check.NOT_IN_CATALOG.value: "component_not_in_catalog",
    Check.PLANNED.value: "component_not_specified",
}
TITLES: Mapping[Outcome, str] = {
    Outcome.VIOLATION: "{node} breaks a documented limit of {component}",
    Outcome.WARNING: "{node} does not meet a documented recommendation of {component}",
    Outcome.CANNOT_EVALUATE: "A check of {node} against {component} cannot be evaluated",
}
DEFAULT_REMEDIATION = "Review the node's configuration against the component's specification."


def _code(item: ConstraintFinding) -> str:
    return SPECIAL.get(item.check, CODES[item.outcome])


def _severity(item: ConstraintFinding) -> Severity:
    if item.outcome is Outcome.CANNOT_EVALUATE:
        return Severity.INFO
    return item.severity or Severity.MEDIUM


def _joined(values: list[str | None], separator: str) -> str | None:
    kept = list(dict.fromkeys(v for v in values if v))
    return separator.join(kept) if kept else None


@dataclass(frozen=True, slots=True)
class ComponentConstraints:
    meta = RuleMeta(
        "configuration.component-constraints",
        1,
        "Configuration within the component's documented constraints",
        "Each node that refers to a catalog component respects the limits, supported values and "
        "recommendations its specification documents; what cannot be evaluated is reported as such.",
        Category.CONFIGURATION,
        Severity.MEDIUM,
        profiles=ALL_PROFILES,
        inputs=frozenset({Input.CATALOG}),
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> RuleOutcome:
        evaluation = context.components
        if evaluation is None:
            return RuleOutcome()
        groups: dict[tuple[str, str, str | None], list[ConstraintFinding]] = {}
        for item in evaluation.findings:  # already in (node, check) order
            if item.outcome in CODES:  # passed and not-applicable checks are not findings
                groups.setdefault((item.node_id, _code(item), item.property), []).append(item)
        names = {n.id: n.name for n in context.ir.nodes}
        found: list[Finding] = []
        for (node_id, code, prop), items in groups.items():
            first = items[0]
            evidence = [("check", i.check) for i in items]
            evidence += [
                ("specification", ref) for ref in dict.fromkeys(i.specification for i in items) if ref
            ]
            evidence += [("basis", i.provenance.kind.value) for i in items[:1] if i.provenance is not None]
            found.append(
                finding(
                    self.meta,
                    code,
                    title=TITLES[first.outcome].format(
                        node=names.get(node_id, node_id), component=first.component
                    ),
                    explanation=_joined([i.explanation for i in items], " ") or first.explanation,
                    remediation=_joined([i.remediation for i in items], " ") or DEFAULT_REMEDIATION,
                    entity_ids=[node_id],
                    field_paths=[f"configuration.{prop}"] if prop else [],
                    expected=_joined([i.expected for i in items], "; "),
                    actual=None if first.actual is None else str(first.actual),
                    evidence=evidence,
                    severity=min((_severity(i) for i in items), key=lambda s: s.rank),  # the most severe
                )
            )
        return RuleOutcome(tuple(found))


RULES = (ComponentConstraints(),)
