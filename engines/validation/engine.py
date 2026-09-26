"""The deterministic validation engine: the rule contract, the registry and the orchestrator.

A **rule** declares what it is (``RuleMeta``: stable id, version, name, description, category,
default severity, the profiles it belongs to, the inputs it needs, whether it is mandatory, the
parameters it accepts) and evaluates a read-only ``ValidationContext`` into findings and
requirement verdicts. Rules never persist anything, never call the API, and never run user code:
the registry is built from rule objects in this package, not from dynamic imports.

The **orchestrator** runs the selected rules one by one in id order (no parallelism: order and
shared state stay trivially deterministic), then normalizes the result. A rule that raises, or
returns something malformed, becomes a ``RuleFailure`` of that rule: never a finding, never
silently dropped. The run still completes, with the failures listed, so callers can see exactly
which rules did not produce a verdict (their absence of findings proves nothing).
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from core.domain.parameters import ParamSpec, ParamType, parameter_problem
from core.domain.validation.errors import InvalidFinding, InvalidValidationConfig
from core.domain.validation.results import (
    Category,
    Finding,
    Limitation,
    RequirementResult,
    RuleFailure,
    RuleSet,
    Severity,
    ValidationResult,
)

from .context import MAX_SELECTED_RULES, ValidationConfig, ValidationContext
from .severity import apply_override

log = logging.getLogger("architectos.validation")

CATALOG_UNAVAILABLE = Limitation(
    "catalog_unavailable",
    "No component catalog is available: configuration is checked against the architecture "
    "schema's property definitions only, not against what each technology supports or limits.",
)
NO_POLICY = Limitation(
    "no_policy",
    "The project has no architecture policy, so no policy rule constrained this architecture.",
)

NO_REQUIREMENTS = Limitation(
    "requirements_not_provided",
    "The project's requirements were not provided, so no requirement was traced or given a verdict.",
)


def limitations(context: ValidationContext) -> tuple[Limitation, ...]:
    """What no rule of this run could check, whichever rules were selected."""
    found = [CATALOG_UNAVAILABLE]
    if not context.has_policy:
        found.append(NO_POLICY)
    if context.requirements is None:
        found.append(NO_REQUIREMENTS)
    return tuple(found)


class Input(StrEnum):
    """What a rule needs beyond the architecture itself."""

    REQUIREMENTS = "requirements"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class RuleMeta:
    id: str
    version: int
    name: str
    description: str
    category: Category
    severity: Severity  # the default
    profiles: frozenset[str] = frozenset({"default"})
    inputs: frozenset[Input] = frozenset()
    mandatory: bool = False  # cannot be deselected or re-graded by a client
    parameters: Mapping[str, ParamSpec] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "severity": self.severity.value,
            "profiles": sorted(self.profiles),
            "inputs": sorted(i.value for i in self.inputs),
            "mandatory": self.mandatory,
            "parameters": {
                name: {"type": p.type.value, "description": p.description, "default": p.default}
                for name, p in sorted(self.parameters.items())
            },
        }


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a rule produced (empty when the architecture passes it)."""

    findings: tuple[Finding, ...] = ()
    requirement_results: tuple[RequirementResult, ...] = ()


class Rule(Protocol):
    @property
    def meta(self) -> RuleMeta: ...

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome: ...


class DuplicateRule(ValueError):
    """A programming error: two rules registered under one id."""


class Registry:
    """The rules this engine knows. Built once, in code; read-only afterwards."""

    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        self._rules: dict[str, Rule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: Rule) -> None:
        if rule.meta.id in self._rules:
            raise DuplicateRule(f"rule {rule.meta.id!r} is already registered")
        self._rules[rule.meta.id] = rule

    def get(self, rule_id: str) -> Rule | None:
        return self._rules.get(rule_id)

    def rules(self, *, category: Category | None = None) -> tuple[Rule, ...]:
        """Every rule, in id order."""
        return tuple(
            rule
            for rule_id, rule in sorted(self._rules.items())
            if category is None or rule.meta.category is category
        )

    def profiles(self) -> frozenset[str]:
        return frozenset(p for rule in self._rules.values() for p in rule.meta.profiles)

    def select(self, config: ValidationConfig) -> tuple[Rule, ...]:
        """The rules ``config`` asks for, checked: a known profile; known rule ids; mandatory rules
        always included; only declared parameters, with valid values; no re-grading of mandatory
        rules. In id order."""
        if config.profile not in self.profiles():
            raise InvalidValidationConfig(details={"reason": "unknown_profile", "profile": config.profile})
        in_profile = {r.meta.id: r for r in self.rules() if config.profile in r.meta.profiles}
        if config.rules is None:
            chosen = dict(in_profile)
        else:
            if len(config.rules) > MAX_SELECTED_RULES:
                raise InvalidValidationConfig(details={"reason": "too_many_rules"})
            unknown = sorted(set(config.rules) - set(in_profile))
            if unknown:
                raise InvalidValidationConfig(details={"reason": "unknown_rule", "rule_id": unknown[0]})
            chosen = {rule_id: in_profile[rule_id] for rule_id in config.rules}
            chosen |= {rule_id: r for rule_id, r in in_profile.items() if r.meta.mandatory}
        for rule_id in sorted(set(config.parameters) | set(config.severity_overrides)):
            if rule_id not in chosen:
                raise InvalidValidationConfig(details={"reason": "rule_not_selected", "rule_id": rule_id})
        for rule_id, params in sorted(config.parameters.items()):
            _check_parameters(chosen[rule_id].meta, params)
        for rule_id, severity in sorted(config.severity_overrides.items()):
            if chosen[rule_id].meta.mandatory:
                raise InvalidValidationConfig(details={"reason": "mandatory_rule", "rule_id": rule_id})
            if not isinstance(severity, Severity):
                raise InvalidValidationConfig(details={"reason": "invalid_severity", "rule_id": rule_id})
        return tuple(rule for _, rule in sorted(chosen.items()))

    def rule_set(self, profile: str, rules: Iterable[Rule]) -> RuleSet:
        return RuleSet.of(profile, [(r.meta.id, r.meta.version) for r in rules])


def _check_parameters(meta: RuleMeta, params: Mapping[str, Any]) -> None:
    problem = parameter_problem(meta.parameters, params)
    if problem is not None:
        name, reason = problem
        raise InvalidValidationConfig(details={"reason": reason, "rule_id": meta.id, "parameter": name})


def _parameters(meta: RuleMeta, given: Mapping[str, Any]) -> dict[str, Any]:
    return {name: given.get(name, spec.default) for name, spec in sorted(meta.parameters.items())}


def _checked(meta: RuleMeta, outcome: object) -> Outcome:
    """A rule's output, refused if it is not what the contract says (a rule bug)."""
    if not isinstance(outcome, Outcome):
        raise InvalidFinding(details={"fields": ["outcome"]})
    for finding in outcome.findings:
        if not isinstance(finding, Finding) or (finding.rule_id, finding.rule_version) != (
            meta.id,
            meta.version,
        ):
            raise InvalidFinding(details={"fields": ["rule_id"]})
    for result in outcome.requirement_results:
        if not isinstance(result, RequirementResult) or result.rule_id != meta.id:
            raise InvalidFinding(details={"fields": ["rule_id"]})
    return outcome


def validate(context: ValidationContext, registry: Registry) -> ValidationResult:
    """Every selected rule against ``context``; deterministic for equal inputs."""
    rules = registry.select(context.config)
    findings: list[Finding] = []
    verdicts: list[RequirementResult] = []
    failures: list[RuleFailure] = []
    for rule in rules:
        meta = rule.meta
        parameters = _parameters(meta, context.config.parameters.get(meta.id, {}))
        try:
            outcome = _checked(meta, rule.evaluate(context, parameters))
        except InvalidFinding:
            failures.append(
                RuleFailure(meta.id, meta.version, "invalid_output", "The rule produced a malformed result.")
            )
            log.error("validation rule produced a malformed result", extra={"rule_id": meta.id})
            continue
        except Exception:  # a crashing rule must neither take the others down nor go unnoticed
            failures.append(RuleFailure(meta.id, meta.version, "unexpected_error", "The rule could not run."))
            log.exception("validation rule failed", extra={"rule_id": meta.id})
            continue
        override = context.config.severity_overrides.get(meta.id)
        findings.extend(apply_override(f, override) for f in outcome.findings)
        verdicts.extend(outcome.requirement_results)
    return ValidationResult(
        rule_set=registry.rule_set(context.config.profile, rules),
        context_fingerprint=context.fingerprint,
        findings=tuple(findings),
        requirement_results=tuple(verdicts),
        failures=tuple(failures),
        limitations=limitations(context),
    )


__all__ = ["ParamSpec", "ParamType"]  # re-exported: the rules import them from here
