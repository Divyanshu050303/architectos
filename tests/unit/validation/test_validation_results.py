"""The validation result contract (Milestone 6, phase 1)."""

import dataclasses
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from core.domain.validation.errors import InvalidFinding, InvalidRunTransition
from core.domain.validation.results import (
    Category,
    Evidence,
    Finding,
    RequirementResult,
    RuleFailure,
    RuleSet,
    Severity,
    ValidationResult,
    Verdict,
    summarize,
)
from core.domain.validation.runs import RunError, RunStatus, ValidationRun

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def finding(**overrides: Any) -> Finding:
    fields: dict[str, Any] = {
        "rule_id": "structure.disconnected-component",
        "rule_version": 1,
        "code": "not_connected",
        "severity": Severity.MEDIUM,
        "category": Category.STRUCTURE,
        "title": "Worker is not connected",
        "explanation": "No connection reaches or leaves this component.",
        "remediation": "Connect it, or remove it if it is not part of the system.",
        "entity_ids": ("worker",),
        "field_paths": ("connections",),
        "expected": "at least one connection",
        "actual": "no connections",
        "evidence": (Evidence("connections of worker", "0"),),
    }
    return Finding(**(fields | overrides))


def verdict(**overrides: Any) -> RequirementResult:
    fields: dict[str, Any] = {
        "requirement_id": str(uuid.UUID(int=1)),
        "reference": "REQ-1",
        "requirement_version": 2,
        "verdict": Verdict.SATISFIED,
        "reason": "Every component runs in an allowed region.",
        "rule_id": "requirements.regions",
        "entity_ids": ("api", "db"),
    }
    return RequirementResult(**(fields | overrides))


RULE_SET = RuleSet.of("default", [("structure.disconnected-component", 1), ("requirements.regions", 1)])


def test_severity_scale_is_ordered() -> None:
    assert [s.value for s in Severity] == ["critical", "high", "medium", "low", "info"]
    assert Severity.CRITICAL.rank < Severity.HIGH.rank < Severity.INFO.rank


def test_a_valid_finding_and_its_stable_id() -> None:
    one = finding(entity_ids=["worker", "api", "worker"])
    assert one.entity_ids == ("api", "worker")  # sorted, deduplicated
    assert one.id == finding(entity_ids=("api", "worker")).id  # the id is derived from the content
    assert one.id.startswith("fnd_")
    assert one.id != finding(entity_ids=("worker",)).id
    assert one.id == dataclasses.replace(one, title="Another wording").id  # wording is not identity


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"rule_id": "Bad Rule"}, "rule_id"),
        ({"rule_version": 0}, "rule_version"),
        ({"rule_version": True}, "rule_version"),
        ({"code": ""}, "code"),
        ({"severity": "urgent"}, "severity"),
        ({"category": "vibes"}, "category"),
        ({"title": "  "}, "title"),
        ({"remediation": "x" * 2001}, "remediation"),
        ({"entity_ids": ("",)}, "entity_ids"),
        ({"entity_ids": tuple(f"n{i}" for i in range(201))}, "entity_ids"),
        ({"evidence": ("a string",)}, "evidence"),
        ({"blocking": "yes"}, "blocking"),
    ],
)
def test_invalid_findings_are_refused(overrides: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidFinding) as raised:
        finding(**overrides)
    assert field in raised.value.details["fields"]


def test_invalid_verdicts_are_refused() -> None:
    with pytest.raises(InvalidFinding):
        verdict(verdict="passed")
    with pytest.raises(InvalidFinding):
        verdict(requirement_version=0)


def test_the_summary_is_derived_from_the_findings() -> None:
    findings = [
        finding(),
        finding(code="other", severity=Severity.CRITICAL, category=Category.POLICY, blocking=True),
        finding(code="third", severity=Severity.MEDIUM, category=Category.STRUCTURE),
    ]
    results = [verdict(), verdict(reference="REQ-2", verdict=Verdict.NOT_VERIFIABLE)]
    summary = summarize(findings, results, failures=1)
    assert summary.total == 3
    assert summary.by_severity == {"critical": 1, "high": 0, "medium": 2, "low": 0, "info": 0}
    assert summary.by_category == {
        "structure": 2,
        "configuration": 0,
        "completeness": 0,
        "policy": 1,
        "requirements": 0,
    }
    assert summary.blocking == 1
    assert summary.requirements == {"satisfied": 1, "violated": 0, "not_verifiable": 1, "not_applicable": 0}
    assert summary.rule_failures == 1
    assert summarize([]).total == 0


def test_results_are_ordered_and_fingerprinted_deterministically() -> None:
    findings = [
        finding(code="a", severity=Severity.LOW),
        finding(code="b", severity=Severity.CRITICAL),
        finding(code="c", severity=Severity.CRITICAL, category=Category.CONFIGURATION),
    ]
    one = ValidationResult(RULE_SET, "ctx", tuple(findings), (verdict(reference="REQ-2"), verdict()))
    other = ValidationResult(
        RULE_SET, "ctx", tuple(reversed(findings)), (verdict(), verdict(reference="REQ-2"))
    )
    assert [f.code for f in one.findings] == ["c", "b", "a"]  # severity first, then category
    assert [r.reference for r in one.requirement_results] == ["REQ-1", "REQ-2"]
    assert one == other
    assert one.fingerprint == other.fingerprint
    assert one.fingerprint != ValidationResult(RULE_SET, "other context", tuple(findings)).fingerprint
    duplicated = ValidationResult(RULE_SET, "ctx", (findings[0], findings[0]))
    assert len(duplicated.findings) == 1


def test_the_rule_set_version_follows_its_rules() -> None:
    same = RuleSet.of("default", [("requirements.regions", 1), ("structure.disconnected-component", 1)])
    assert same == RULE_SET  # order does not matter
    changed = RuleSet.of("default", [("structure.disconnected-component", 2), ("requirements.regions", 1)])
    assert changed.version != RULE_SET.version


def test_findings_and_verdicts_round_trip() -> None:
    original = finding(requirement_id="req-1", policy_rule="require_tls", blocking=True)
    assert Finding.from_dict(original.to_dict()) == original
    tampered = original.to_dict() | {"id": "fnd_forged"}
    with pytest.raises(InvalidFinding):
        Finding.from_dict(tampered)
    with pytest.raises(InvalidFinding):
        Finding.from_dict({"rule_id": "x"})
    assert RequirementResult.from_dict(verdict().to_dict()) == verdict()
    failure = RuleFailure("structure.cycles", 1, "unexpected_error", "The rule could not run.")
    assert RuleFailure.from_dict(failure.to_dict()) == failure


def run(**overrides: Any) -> ValidationRun:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "architecture_id": uuid.uuid4(),
        "revision_number": 3,
        "revision_content_hash": "a" * 64,
        "profile": "default",
        "status": RunStatus.PENDING,
        "requested_by_user_id": uuid.uuid4(),
        "requested_at": NOW,
    }
    return ValidationRun(**(fields | overrides))


def test_the_run_lifecycle() -> None:
    result = ValidationResult(RULE_SET, "ctx", (finding(),))
    completed = run().start(NOW).complete(result, NOW)
    assert (completed.status, completed.summary.total if completed.summary else None) == (
        RunStatus.COMPLETED,
        1,
    )
    failed = run().start(NOW).fail(RunError("rule_failed", "A rule could not run."), NOW)
    assert (failed.status, failed.result, failed.summary) == (RunStatus.FAILED, None, None)
    assert run().fail(RunError("invalid_context", "x"), NOW).status is RunStatus.FAILED
    illegal_moves: tuple[Callable[[], object], ...] = (
        lambda: run().complete(result, NOW),  # must start first
        lambda: completed.fail(RunError("x", "y"), NOW),  # final
        lambda: failed.start(NOW),  # final
        lambda: run().start(NOW).start(NOW),
    )
    for illegal in illegal_moves:
        with pytest.raises(InvalidRunTransition):
            illegal()
