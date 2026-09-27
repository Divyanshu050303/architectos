"""Candidate validation (Milestone 13, phase 5): the Validation Engine's own rules on the overlay next
to the baseline; findings a candidate introduces or resolves, with the exact elements; invalid never
presented as valid; unknown checks visible; ``valid`` only under the modeled constraints."""

import json
import uuid
from dataclasses import replace
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.dependency import ConnectionKind
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.serialization import content_hash
from core.domain.evolution.candidates import BaselineRef, Candidate, EvidenceRef, RuleRef
from core.domain.evolution.validation import validate_candidate
from core.domain.evolution.values import CandidateCategory, EvidenceSource, EvidenceState, ValidationState
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.simulations.scenarios import ConfigurationChange
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import RequirementResult, RuleFailure, RuleSet, ValidationResult, Verdict
from engines.validation.service import DeterministicValidationEngine
from tests.unit.architecture_ir.builders import connection, node

C = ConfigurationChange
V = ValidationState
POLICY = ArchitecturePolicy(require_tls=True)


def component(node_id: str, kind: NodeKind = NodeKind.SERVICE, **values: ConfigValue) -> Any:
    return node(node_id, kind, configuration=Configuration(values))


def shop(tls: bool) -> ArchitectureIR:
    return ArchitectureIR(
        "Shop",
        nodes=(node("web", NodeKind.CLIENT), component("api", replicas=2),
               component("db", NodeKind.DATABASE)),
        connections=(
            connection("web-api", "web", "api", kind=ConnectionKind.REQUEST, protocol="https",
                       configuration=Configuration({"tls": True})),
            connection("api-db", "api", "db", kind=ConnectionKind.DATA_ACCESS, protocol="postgresql",
                       configuration=Configuration({"tls": tls})),
        ),
    )  # fmt: skip


def setup(tls: bool) -> tuple[ArchitectureIR, RevisionInfo, BaselineRef]:
    ir = shop(tls)
    return (
        ir,
        RevisionInfo(str(uuid.UUID(int=7)), 1, content_hash(ir)),
        BaselineRef(uuid.UUID(int=7), 1, content_hash(ir)),
    )


def candidate(baseline: BaselineRef, *changes: ConfigurationChange, rule: str = "require-tls") -> Candidate:
    return Candidate(
        RuleRef(rule, 1),
        baseline,
        CandidateCategory.SECURITY_CONTROL,
        "A proposal",
        "Proposed configuration changes.",
        changes,
        ("address_finding:security:sec_1",),
        "The evidence states it.",
        (
            EvidenceRef(
                EvidenceSource.SECURITY, "a1", EvidenceState.CURRENT, "sec_1", 1, baseline.content_hash
            ),
        ),
    )


ENGINE = DeterministicValidationEngine()


def test_a_candidate_that_resolves_a_policy_violation_is_valid_under_the_modeled_constraints() -> None:
    ir, revision, baseline = setup(tls=False)
    validated = validate_candidate(
        ir, revision, candidate(baseline, C("api-db", "tls", True)), ENGINE, policy=POLICY
    )
    assert validated.validation is V.VALID
    resolved = [n for n in validated.validation_notes if n.code == "resolved_finding"]
    assert resolved
    assert all("api-db" in n.element_ids for n in resolved)
    assert all(n.reference and n.reference.startswith("fnd_") for n in resolved)


def test_a_candidate_that_introduces_a_blocking_finding_is_invalid() -> None:
    ir, revision, baseline = setup(tls=True)
    validated = validate_candidate(
        ir, revision, candidate(baseline, C("api-db", "tls", False)), ENGINE, policy=POLICY
    )
    assert validated.validation is V.INVALID  # never presented as valid
    [introduced] = [n for n in validated.validation_notes if n.code == "introduced_finding"]
    assert (introduced.blocking, introduced.element_ids) == (True, ("api-db",))
    unenforced = validate_candidate(ir, revision, candidate(baseline, C("api-db", "tls", False)), ENGINE)
    assert unenforced.validation is not V.INVALID  # without the policy, nothing blocks it


@pytest.mark.parametrize(
    ("change", "state", "reason"),
    [
        (C("cache", "replicas", 2), V.INVALID, "unknown_element"),  # fixture 8
        (C("api", "tls", True), V.UNSUPPORTED, "not_applicable"),  # fixture 9
    ],
)
def test_fixtures_8_and_9_refused_overlays(
    change: ConfigurationChange, state: ValidationState, reason: str
) -> None:
    ir, revision, baseline = setup(tls=True)
    validated = validate_candidate(ir, revision, candidate(baseline, change), ENGINE)
    assert validated.validation is state
    [note] = validated.validation_notes
    assert (note.code, note.reference, note.element_ids) == ("overlay_refused", reason, (change.element_id,))


def test_a_candidate_against_another_baseline_is_invalid() -> None:
    ir, revision, baseline = setup(tls=True)
    other = BaselineRef(baseline.architecture_id, 2, "e" * 64)
    validated = validate_candidate(ir, revision, candidate(other, C("api", "replicas", 3)), ENGINE)
    assert (validated.validation, validated.validation_notes[0].reference) == (V.INVALID, "baseline_mismatch")


class Stub:
    """A validation engine returning chosen results for the baseline and the overlay."""

    def __init__(self, ir: ArchitectureIR, before: ValidationResult, after: ValidationResult) -> None:
        self.hash = content_hash(ir)
        self.before, self.after = before, after
        self.calls = 0

    def validate(self, ir: ArchitectureIR, revision: RevisionInfo, **_: Any) -> ValidationResult:
        self.calls += 1
        return self.before if revision.content_hash == self.hash else self.after

    def rules(self) -> tuple[()]:
        return ()


def result(verdict: Verdict | None = None, *failures: RuleFailure) -> ValidationResult:
    requirements = (
        (
            RequirementResult(
                str(uuid.UUID(int=5)), "REQ-1", 1, verdict, "As declared.", "requirements", ("api",)
            ),
        )
        if verdict is not None
        else ()
    )
    return ValidationResult(
        RuleSet("default", "v1"), "c" * 64, requirement_results=requirements, failures=failures
    )


@pytest.mark.parametrize(
    ("before", "after", "state", "code"),
    [
        (result(Verdict.SATISFIED), result(Verdict.VIOLATED), V.INVALID, "requirement_violated"),
        (result(Verdict.SATISFIED), result(Verdict.NOT_VERIFIABLE), V.NOT_EVALUABLE,
         "requirement_not_verifiable"),
        (result(), result(None, RuleFailure("scalability", 1, "unexpected_error", "The rule failed.")),
         V.NOT_EVALUABLE, "rule_failed"),
        (result(Verdict.VIOLATED), result(Verdict.VIOLATED), V.VALID, None),  # not introduced by it
    ],
)  # fmt: skip
def test_requirement_verdicts_and_rule_failures_decide_the_state(
    before: ValidationResult, after: ValidationResult, state: ValidationState, code: str | None
) -> None:
    ir, revision, baseline = setup(tls=True)
    validated = validate_candidate(
        ir, revision, candidate(baseline, C("api", "replicas", 3)), Stub(ir, before, after)
    )
    assert validated.validation is state
    assert [n.code for n in validated.validation_notes] == ([code] if code else [])


def test_the_baseline_result_is_reused_when_given() -> None:
    ir, revision, baseline = setup(tls=True)
    stub = Stub(ir, result(), result())
    validate_candidate(ir, revision, candidate(baseline, C("api", "replicas", 3)), stub, baseline=result())
    assert stub.calls == 1  # the overlay only


def test_validation_is_deterministic_and_round_trips() -> None:
    ir, revision, baseline = setup(tls=False)
    proposed = candidate(baseline, C("api-db", "tls", True))
    first = validate_candidate(ir, revision, proposed, ENGINE, policy=POLICY)
    again = validate_candidate(ir, revision, proposed, ENGINE, policy=POLICY)
    assert first.to_dict() == again.to_dict()
    assert first.id == proposed.id  # validation never changes what the proposal is
    assert Candidate.from_dict(json.loads(json.dumps(first.to_dict()))) == first
    assert replace(first, validation_notes=()).validation is V.VALID
