"""The rule contract, registry and orchestrator (Milestone 6, phase 2), with stand-in rules."""

import dataclasses
import logging
from collections.abc import Mapping
from typing import Any

import pytest

from core.domain.validation.errors import InvalidValidationConfig
from core.domain.validation.results import Category, Severity, ValidationResult
from engines.validation.context import RevisionInfo, ValidationConfig, ValidationContext
from engines.validation.engine import (
    DuplicateRule,
    Outcome,
    ParamSpec,
    ParamType,
    Registry,
    RuleMeta,
    validate,
)
from engines.validation.findings import finding
from tests.unit.architecture_ir.builders import api_and_postgres

REVISION = RevisionInfo("arch-1", 3, "a" * 64)


@dataclasses.dataclass(frozen=True)
class CountingRule:
    """Reports every node whose name is longer than ``limit`` characters."""

    meta: RuleMeta

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        limit = parameters["limit"]
        return Outcome(
            tuple(
                finding(
                    self.meta,
                    "long_name",
                    title=f"{node.id} has a long name",
                    explanation="Names over the limit are hard to read.",
                    remediation="Shorten the name.",
                    entity_ids=[node.id],
                    field_paths=["name"],
                    expected=f"at most {limit} characters",
                    actual=f"{len(node.name)} characters",
                )
                for node in context.ir.nodes
                if len(node.name) > limit
            )
        )


class CrashingRule:
    meta = RuleMeta("test.crashes", 1, "Crashes", "Always fails.", Category.STRUCTURE, Severity.HIGH)

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        raise RuntimeError("boom: secret internal detail")


class MalformedRule:
    meta = RuleMeta("test.malformed", 1, "Malformed", "Returns garbage.", Category.STRUCTURE, Severity.LOW)

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        return "not an outcome"  # type: ignore[return-value]


def counting(rule_id: str = "test.long-names", **overrides: Any) -> CountingRule:
    fields: dict[str, Any] = {
        "id": rule_id,
        "version": 1,
        "name": "Long names",
        "description": "Node names are short.",
        "category": Category.CONFIGURATION,
        "severity": Severity.LOW,
        "profiles": frozenset({"default", "strict"}),
        "parameters": {
            "limit": ParamSpec(ParamType.INTEGER, "Longest name allowed.", default=8, minimum=1, maximum=100)
        },
    }
    return CountingRule(RuleMeta(**(fields | overrides)))


MANDATORY = counting(
    "test.mandatory", mandatory=True, severity=Severity.HIGH, profiles=frozenset({"default"})
)


def context(**config: Any) -> ValidationContext:
    return ValidationContext(api_and_postgres(), REVISION, config=ValidationConfig(**config))


def test_registration_lookup_and_listing_are_ordered() -> None:
    registry = Registry([counting("test.zeta"), counting("test.alpha")])
    assert [r.meta.id for r in registry.rules()] == ["test.alpha", "test.zeta"]
    assert registry.get("test.alpha") is not None
    assert registry.get("test.missing") is None
    assert registry.profiles() == {"default", "strict"}
    assert [r.meta.id for r in registry.rules(category=Category.STRUCTURE)] == []
    with pytest.raises(DuplicateRule):
        registry.register(counting("test.alpha"))


def test_a_rule_finds_what_it_is_made_to_find() -> None:
    result = validate(context(), Registry([counting()]))
    # "Orders API" (10) and "Orders DB" (9) exceed 8; "Web app" (7) does not.
    assert [f.entity_ids for f in result.findings] == [("api",), ("db",)]
    assert result.summary.total == 2
    assert result.failures == ()
    configured = validate(context(parameters={"test.long-names": {"limit": 9}}), Registry([counting()]))
    assert [f.entity_ids for f in configured.findings] == [("api",)]


def test_results_are_deterministic_whatever_the_registration_order() -> None:
    rules = [counting("test.b"), counting("test.a", severity=Severity.CRITICAL), MANDATORY]
    one = validate(context(), Registry(rules))
    other = validate(context(), Registry(list(reversed(rules))))
    assert one == other
    assert one.fingerprint == other.fingerprint
    assert [f.rule_id for f in one.findings][:2] == ["test.a", "test.a"]  # critical first
    assert validate(context(), Registry(rules)).fingerprint == one.fingerprint  # repeatable


def test_a_crashing_rule_is_a_failure_not_a_finding(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="architectos.validation"):
        result = validate(context(), Registry([counting(), CrashingRule(), MalformedRule()]))
    assert [(f.rule_id, f.error) for f in result.failures] == [
        ("test.crashes", "unexpected_error"),
        ("test.malformed", "invalid_output"),
    ]
    assert all("boom" not in f.message for f in result.failures)  # no internals in the result
    assert {f.rule_id for f in result.findings} == {"test.long-names"}  # the others still ran
    assert result.summary.rule_failures == 2
    assert any(getattr(r, "rule_id", None) == "test.crashes" for r in caplog.records)  # but logged


def test_a_rule_cannot_report_under_another_rules_name() -> None:
    impostor = counting("test.impostor")
    honest = dataclasses.replace(impostor, meta=dataclasses.replace(impostor.meta, id="test.honest"))

    class Impostor:
        meta = honest.meta

        def evaluate(self, ctx: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
            return impostor.evaluate(ctx, {"limit": 1})

    result = validate(context(), Registry([Impostor()]))
    assert [f.error for f in result.failures] == ["invalid_output"]
    assert result.findings == ()


def test_selection_by_profile_and_rule_ids() -> None:
    registry = Registry([counting("test.a"), counting("test.b", profiles=frozenset({"strict"})), MANDATORY])
    assert [r.meta.id for r in registry.select(ValidationConfig())] == ["test.a", "test.mandatory"]
    assert [r.meta.id for r in registry.select(ValidationConfig(profile="strict"))] == ["test.a", "test.b"]
    chosen = registry.select(ValidationConfig(rules=("test.a",)))
    assert [r.meta.id for r in chosen] == ["test.a", "test.mandatory"]  # mandatory rules always run
    assert [r.meta.id for r in registry.select(ValidationConfig(rules=()))] == ["test.mandatory"]


def test_an_empty_selection_runs_nothing() -> None:
    result = validate(context(profile="strict", rules=()), Registry([counting("test.a")]))
    assert (result.findings, result.failures, result.rule_set.rules) == ((), (), ())
    assert isinstance(result, ValidationResult)


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        ({"profile": "paranoid"}, "unknown_profile"),
        ({"rules": ("test.nope",)}, "unknown_rule"),
        ({"rules": tuple(f"r{i}" for i in range(101))}, "too_many_rules"),
        ({"parameters": {"test.a": {"colour": 1}}}, "unknown_parameter"),
        ({"parameters": {"test.a": {"limit": 0}}}, "out_of_range"),
        ({"parameters": {"test.a": {"limit": "8"}}}, "not_an_integer"),
        ({"parameters": {"test.nope": {"limit": 1}}}, "rule_not_selected"),
        ({"severity_overrides": {"test.mandatory": Severity.INFO}}, "mandatory_rule"),
        ({"severity_overrides": {"test.a": "urgent"}}, "invalid_severity"),
    ],
)
def test_invalid_configuration_is_refused_before_anything_runs(config: dict[str, Any], reason: str) -> None:
    registry = Registry([counting("test.a"), MANDATORY])
    with pytest.raises(InvalidValidationConfig) as raised:
        validate(context(**config), registry)
    assert raised.value.details["reason"] == reason


def test_severity_overrides_regrade_but_keep_identity() -> None:
    plain = validate(context(), Registry([counting("test.a")]))
    graded = validate(context(severity_overrides={"test.a": Severity.HIGH}), Registry([counting("test.a")]))
    assert [f.severity for f in graded.findings] == [Severity.HIGH, Severity.HIGH]
    assert [f.id for f in graded.findings] == [f.id for f in plain.findings]
    assert graded.fingerprint != plain.fingerprint  # a different configuration is a different result


def test_the_rule_set_and_context_identify_the_run() -> None:
    registry = Registry([counting("test.a")])
    result = validate(context(), registry)
    assert result.rule_set.rules == (("test.a", 1),)
    bumped = Registry([counting("test.a", version=2)])
    assert validate(context(), bumped).rule_set.version != result.rule_set.version
    other_revision = ValidationContext(api_and_postgres(), RevisionInfo("arch-1", 4, "b" * 64))
    assert other_revision.fingerprint != context().fingerprint


def test_the_context_is_read_only() -> None:
    ctx = context()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.ir = api_and_postgres()  # type: ignore[misc]
    assert ctx.topology is ctx.topology  # built once, shared by every rule
