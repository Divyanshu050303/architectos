"""Evaluates one architecture node against its component specification: the reference itself (a
planned or deprecated specification, a node kind it does not model, a required value not stated),
then each documented constraint. Deterministic and pure: the node and the specification in, the
findings out.

A constraint is evaluated only with what it needs: the configured value (not stated, or marked
unknown: ``cannot_evaluate``), its conditions (unknown: ``cannot_evaluate``; not holding:
``not_applicable``), the node's technology version when the constraint was checked for particular
versions, and a documented limit (an ``unknown`` constraint: ``cannot_evaluate``). A failed hard,
conditional or unsupported-value constraint is a ``violation``; a failed recommendation or raisable
default is a ``warning`` — never more.
"""

from dataclasses import dataclass
from decimal import Decimal

from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.node import Node
from core.domain.components.constraints import ADVISORY, Comparison, Constraint, ConstraintType
from core.domain.components.entities import SupportStatus
from core.domain.components.evaluation import Check, ConstraintFinding, Outcome, json_value
from core.domain.components.specifications import ComponentSpecification
from core.domain.validation.results import Severity


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _listed(values: tuple[ConfigValue, ...]) -> str:
    return ", ".join(str(json_value(v)) for v in values)


def expected(constraint: Constraint) -> str | None:
    """The condition a value must meet, in words."""
    low, high, limit = constraint.minimum, constraint.maximum, constraint.limit
    match constraint.comparison:
        case Comparison.AT_MOST if limit is not None:
            return f"at most {_number(limit)}"
        case Comparison.AT_LEAST if limit is not None:
            return f"at least {_number(limit)}"
        case Comparison.BETWEEN if low is not None and high is not None:
            return f"between {_number(low)} and {_number(high)}"
        case Comparison.ONE_OF:
            return f"one of {_listed(constraint.values)}"
        case Comparison.NOT_ONE_OF:
            return f"none of {_listed(constraint.values)}"
    return None


def _holds(constraint: Constraint, value: ConfigValue) -> bool:
    if constraint.comparison in {Comparison.ONE_OF, Comparison.NOT_ONE_OF}:
        return (value in constraint.values) == (constraint.comparison is Comparison.ONE_OF)
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return False  # a numeric constraint on a non-numeric value cannot hold (the IR types it)
    number = Decimal(value)
    low, high, limit = constraint.minimum, constraint.maximum, constraint.limit
    match constraint.comparison:
        case Comparison.AT_MOST:
            return limit is not None and number <= limit
        case Comparison.AT_LEAST:
            return limit is not None and number >= limit
        case Comparison.BETWEEN:
            return low is not None and high is not None and low <= number <= high
    return False


def _stated(configuration: Configuration, prop: str) -> tuple[ConfigValue | None, str]:
    """The configured value, or why there is none."""
    if prop in configuration.unknown:
        return None, f"{prop} is marked unknown in the architecture"
    if prop not in configuration.values:
        return None, f"{prop} is not stated in the architecture"
    return configuration.values[prop], ""


@dataclass(frozen=True, slots=True)
class _Evaluation:
    """One node's evaluation against one specification."""

    node: Node
    spec: ComponentSpecification

    def finding(
        self,
        check: str,
        outcome: Outcome,
        explanation: str,
        *,
        constraint: Constraint | None = None,
        prop: str | None = None,
        actual: object = None,
        severity: Severity | None = None,
        remediation: str | None = None,
    ) -> ConstraintFinding:
        return ConstraintFinding(
            node_id=self.node.id,
            component=self.spec.id,
            specification=self.spec.ref,
            check=check,
            outcome=outcome,
            explanation=explanation,
            constraint_type=constraint.type.value if constraint else None,
            severity=severity,
            property=constraint.property if constraint else prop,
            actual=actual,
            expected=expected(constraint) if constraint else None,
            remediation=constraint.remediation if constraint else remediation,
            provenance=constraint.provenance if constraint else None,
        )

    def reference(self) -> tuple[list[ConstraintFinding], bool]:
        """Findings about the reference itself, and whether the constraints can be evaluated."""
        spec, node = self.spec, self.node
        if spec.support_status is SupportStatus.PLANNED:
            text = f"{spec.name} is listed in the catalog but not specified yet: nothing can be evaluated."
            return [self.finding(Check.PLANNED, Outcome.CANNOT_EVALUATE, text)], False
        found = []
        if spec.support_status is SupportStatus.DEPRECATED:
            successor = f" Use {spec.replaced_by}." if spec.replaced_by else ""
            advice = f"Refer to {spec.replaced_by}." if spec.replaced_by else None
            text = f"{spec.ref} is deprecated.{successor}"
            found.append(
                self.finding(
                    Check.DEPRECATED, Outcome.WARNING, text, severity=Severity.LOW, remediation=advice
                )
            )
        if node.kind not in spec.node_kinds:
            kinds = ", ".join(k.value for k in spec.node_kinds)
            text = f"{node.id} is a {node.kind.value}; {spec.ref} models {kinds}."
            advice = "Refer to a component of this node's kind, or change the node's kind."
            found.append(
                self.finding(
                    Check.KIND_NOT_MODELED,
                    Outcome.VIOLATION,
                    text,
                    severity=Severity.MEDIUM,
                    remediation=advice,
                )
            )
            return found, False
        for configured in spec.configuration:
            prop = configured.property
            value, why = _stated(node.configuration, prop)
            if configured.required and value is None:
                check = f"{Check.REQUIRED_VALUE_MISSING}.{prop}"
                text = f"{spec.name}'s analysis needs {prop}: {why}."
                found.append(
                    self.finding(
                        check, Outcome.CANNOT_EVALUATE, text, prop=prop, remediation=f"State {prop}."
                    )
                )
        return found, True

    def _precondition(self, constraint: Constraint) -> tuple[Outcome, str] | None:
        """Why the constraint cannot be compared here (undocumented, another version, a condition
        that does not hold or cannot be decided), or None when it can."""
        node, check = self.node, constraint.id
        if constraint.type is ConstraintType.UNKNOWN:
            return (
                Outcome.CANNOT_EVALUATE,
                f"{constraint.description} The limit is not documented in {self.spec.ref}.",
            )
        versions = constraint.technology_versions
        version = node.technology.version if node.technology else None
        if versions and version is None:
            listed = ", ".join(versions)
            return (
                Outcome.CANNOT_EVALUATE,
                f"{check} was checked for versions {listed}; {node.id} states no version.",
            )
        if versions and version not in versions:
            return (
                Outcome.NOT_APPLICABLE,
                f"{check} applies to versions {', '.join(versions)}, not {version}.",
            )
        for condition in constraint.conditions:
            value, why = _stated(node.configuration, condition.property)
            holds = condition.holds(value)
            if holds is None:
                return Outcome.CANNOT_EVALUATE, f"Its condition cannot be decided: {why}."
            if not holds:
                return (
                    Outcome.NOT_APPLICABLE,
                    f"It applies when {condition.property} is one of {_listed(condition.values)}.",
                )
        return None

    def constraint(self, constraint: Constraint) -> ConstraintFinding:
        node, check = self.node, constraint.id
        blocked = self._precondition(constraint)
        if blocked is not None:
            return self.finding(check, blocked[0], blocked[1], constraint=constraint)
        value, why = _stated(node.configuration, constraint.property)
        if value is None:
            text = f"{constraint.description} It cannot be evaluated: {why}."
            return self.finding(check, Outcome.CANNOT_EVALUATE, text, constraint=constraint)
        actual = json_value(value)
        if _holds(constraint, value):
            text = f"{constraint.property} is {actual}: {expected(constraint)}, as documented."
            return self.finding(check, Outcome.PASS, text, constraint=constraint, actual=actual)
        outcome = Outcome.WARNING if constraint.type in ADVISORY else Outcome.VIOLATION
        text = f"{constraint.property} is {actual}; {constraint.description}"
        return self.finding(
            check, outcome, text, constraint=constraint, actual=actual, severity=constraint.severity
        )


def evaluate_node(node: Node, spec: ComponentSpecification) -> list[ConstraintFinding]:
    """Every finding for one node against the specification it refers to."""
    evaluation = _Evaluation(node, spec)
    found, evaluable = evaluation.reference()
    if evaluable:
        found += [evaluation.constraint(c) for c in spec.constraints]
    return found


def not_in_catalog(node: Node) -> ConstraintFinding:
    component = node.component or ""
    return ConstraintFinding(
        node_id=node.id,
        component=component,
        specification=None,
        check=Check.NOT_IN_CATALOG,
        outcome=Outcome.CANNOT_EVALUATE,
        explanation=f"{component} is not in the component catalog: nothing can be evaluated.",
        remediation="Refer to a catalog entry, or leave the node's component unset.",
    )
