"""The migration domain contract (Migration Planning, phase 1): a request with exact source and target
references and no topology; stable, traced steps, risks, checkpoints and rollbacks whose unknowns
stay unknown; an internally consistent, deterministic proposal whose status follows its findings;
plan versions whose review lifecycle is made by people — and no execution status anywhere."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from core.domain.evolution.candidates import EvidenceRef
from core.domain.evolution.values import EvidenceSource, EvidenceState
from core.domain.migrations.entities import (
    Assumption,
    DataRequirement,
    MigrationConstraints,
    MigrationPlanVersion,
    MigrationRequest,
    TargetSpec,
)
from core.domain.migrations.errors import InvalidMigrationPlan, InvalidMigrationRequest, InvalidPlanTransition
from core.domain.migrations.plans import MigrationProposal, PlanFinding, SourceRef, TargetRef
from core.domain.migrations.steps import (
    Checkpoint,
    CompatibilityAspect,
    CompatibilityCheck,
    DataMigration,
    MigrationStep,
    Risk,
    RollbackConsideration,
    Trace,
    step_id,
)
from core.domain.migrations.values import (
    CheckpointBasis,
    CheckpointStatus,
    CompatibilityStatus,
    DowntimeStatus,
    FindingType,
    PlanStatus,
    Reversibility,
    RiskCategory,
    RiskStatus,
    StepType,
    TargetKind,
    TraceKind,
)

ARCHITECTURE = uuid.UUID(int=3)
HASH_1, HASH_2 = "1" * 64, "2" * 64
AT = datetime(2026, 9, 28, tzinfo=UTC)
ADA, BOB = uuid.UUID(int=10), uuid.UUID(int=11)
CANDIDATE = "evo_" + "a" * 20
CHANGE = Trace(TraceKind.CHANGE, "node:db:modified")
PATTERN = Trace(TraceKind.PATTERN, "reconfigure@1")
FORBIDDEN = ("score", "probability", "likelihood", "duration", "estimated", "executed", "success")


def source() -> SourceRef:
    return SourceRef(ARCHITECTURE, 1, HASH_1)


def target(**overrides: Any) -> TargetRef:
    fields: dict[str, Any] = {
        "kind": TargetKind.REVISION,
        "architecture_id": ARCHITECTURE,
        "revision_number": 2,
        "content_hash": HASH_2,
    }
    return TargetRef(**(fields | overrides))


def candidate_target(revision: int = 1) -> TargetRef:
    return target(
        kind=TargetKind.CANDIDATE,
        revision_number=revision,
        analysis_id=uuid.UUID(int=4),
        candidate_id=CANDIDATE,
    )


def step(key: str = "configure:db", **overrides: Any) -> MigrationStep:
    fields: dict[str, Any] = {
        "key": key,
        "type": StepType.CONFIGURE,
        "title": "Change db's configuration",
        "outcome": "db runs with the target configuration.",
        "traces": (CHANGE, PATTERN),
        "completion": ("The target configuration is in effect.",),
        "element_ids": ("db",),
    }
    return MigrationStep(**(fields | overrides))


def request(**overrides: Any) -> MigrationRequest:
    fields: dict[str, Any] = {
        "architecture_id": ARCHITECTURE,
        "source_revision": 1,
        "target": TargetSpec(revision=2),
    }
    return MigrationRequest(**(fields | overrides))


def fields_of(error: pytest.ExceptionInfo[InvalidMigrationPlan]) -> list[str]:
    return list(error.value.details["fields"])


# --- the request -----------------------------------------------------------------------------------


def test_a_request_names_exact_revisions_and_no_topology() -> None:
    planned = request(
        goals=("Scale the database.",),
        constraints=MigrationConstraints(downtime_allowed=False),
        data_requirements=(DataRequirement("db", "Every order is kept."),),
        assumptions=(Assumption("traffic", "Peak is on Mondays."),),
    )
    data = planned.to_dict()
    assert (data["source_revision"], data["target"]["revision"]) == (1, 2)
    assert "nodes" not in json.dumps(data)  # the architecture is read from its revisions, never carried
    assert MigrationConstraints().downtime_allowed is None  # not stated: never assumed
    by_candidate = request(target=TargetSpec(analysis_id=uuid.UUID(int=4), candidate_id=CANDIDATE))
    assert by_candidate.target.revision is None


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"target": TargetSpec(revision=1)}, "target.revision", "not_after_the_source"),
        ({"source_revision": 0}, "source_revision", "invalid_revision"),
        ({"goals": ("",)}, "goals", "invalid_text"),
        ({"goals": tuple(f"g{i}" for i in range(21))}, "goals", "too_many"),
        ({"strategy": "Big Bang!"}, "strategy", "invalid_strategy"),
        ({"assumptions": (Assumption("a", "x"), Assumption("a", "y"))}, "assumptions", "duplicate"),
    ],
)
def test_invalid_requests_name_the_field(overrides: dict[str, Any], field: str, reason: str) -> None:
    with pytest.raises(InvalidMigrationRequest) as error:
        request(**overrides)
    assert error.value.details == {"field": field, "reason": reason}


def test_a_target_is_a_revision_or_a_candidate_never_both() -> None:
    both: dict[str, Any] = {"revision": 2, "analysis_id": uuid.UUID(int=4), "candidate_id": CANDIDATE}
    specs: list[dict[str, Any]] = [{}, both]
    for spec in specs:
        with pytest.raises(InvalidMigrationRequest) as error:
            TargetSpec(**spec)
        assert error.value.details == {"field": "target", "reason": "one_of_revision_or_candidate"}
    with pytest.raises(InvalidMigrationRequest):
        TargetSpec(analysis_id=uuid.UUID(int=4), candidate_id="not-a-candidate")


def test_the_target_is_a_later_revision_or_a_candidate_on_the_source() -> None:
    assert MigrationProposal(source(), target()).target.revision_number == 2
    assert MigrationProposal(source(), candidate_target()).target.kind is TargetKind.CANDIDATE
    for wrong, field in [
        (target(architecture_id=uuid.UUID(int=99)), "target.architecture_id"),  # another architecture
        (target(revision_number=1), "target.revision_number"),  # not later than the source
        (candidate_target(revision=2), "target.revision_number"),  # a candidate not on the source
    ]:
        with pytest.raises(InvalidMigrationPlan) as error:
            MigrationProposal(source(), wrong)
        assert fields_of(error) == [field]
    with pytest.raises(InvalidMigrationPlan):
        target(analysis_id=uuid.UUID(int=4))  # a revision target has no candidate


# --- the parts -------------------------------------------------------------------------------------


def test_steps_have_stable_ids_traces_and_explicit_unknowns() -> None:
    first = step()
    assert first.id == step().id == step_id("configure:db")
    assert first.id != step("configure:api").id
    assert (first.downtime, first.reversibility, first.parallelizable) == (
        DowntimeStatus.UNKNOWN,  # never read as online
        Reversibility.UNKNOWN,  # never read as safe
        False,  # parallel execution is never assumed
    )
    cases: list[tuple[dict[str, Any], str]] = [
        ({"traces": ()}, "traces"),  # nothing without a reason
        ({"completion": ()}, "completion"),
        ({"depends_on": (step_id("configure:db"),)}, "depends_on"),  # never on itself
        ({"downtime": DowntimeStatus.POTENTIAL_DOWNTIME}, "downtime_note"),  # under which conditions
    ]
    for overrides, field in cases:
        with pytest.raises(InvalidMigrationPlan) as error:
            step(**overrides)
        assert field in fields_of(error)


def test_risks_carry_evidence_and_no_score() -> None:
    risk = Risk(
        "divergence:db",
        RiskCategory.DATA_INCONSISTENCY,
        RiskStatus.POTENTIAL,
        "Writes accepted by the target are not in the source.",
        "Switching back loses writes.",
        (CHANGE,),
        preconditions=("Writes reach the target before rollback.",),
    )
    assert not any(word in json.dumps(risk.to_dict()) for word in ("score", "probability"))
    with pytest.raises(InvalidMigrationPlan) as error:
        Risk("r", RiskCategory.DOWNTIME, RiskStatus.POTENTIAL, "d", "i", (CHANGE,))
    assert fields_of(error) == ["preconditions"]  # a potential risk says when it materializes


def evidence(state: EvidenceState) -> EvidenceRef:
    return EvidenceRef(EvidenceSource.VALIDATION, "run-1", state, None, 2, HASH_2, "v1")


def test_a_checkpoint_status_is_modeled_evidence_or_a_request_for_verification() -> None:
    current = (evidence(EvidenceState.CURRENT),)
    passed = Checkpoint(
        "valid:target", "Target validates", "No blocking finding", CheckpointStatus.PASS,
        CheckpointBasis.MODELED, (PATTERN,), evidence=current,
    )  # fmt: skip
    assert passed.blocking
    manual = Checkpoint(
        "health:db", "db health", "Health checks pass", CheckpointStatus.MANUAL_VERIFICATION_REQUIRED,
        CheckpointBasis.MANUAL, (PATTERN,),
    )  # fmt: skip
    assert manual.status is CheckpointStatus.MANUAL_VERIFICATION_REQUIRED
    for status, basis, proof, field in [
        (CheckpointStatus.PASS, CheckpointBasis.MODELED, (), "evidence"),  # a pass rests on evidence
        (CheckpointStatus.PASS, CheckpointBasis.MODELED, (evidence(EvidenceState.STALE),), "evidence"),
        (CheckpointStatus.PASS, CheckpointBasis.RUNTIME_OBSERVED, current, "basis"),  # not at planning time
        (CheckpointStatus.PASS, CheckpointBasis.MANUAL, current, "basis"),
    ]:
        with pytest.raises(InvalidMigrationPlan) as error:
            Checkpoint("c", "s", "e", status, basis, (PATTERN,), evidence=proof)
        assert field in fields_of(error)


def test_rollbacks_say_how_or_why_not() -> None:
    sid = step_id("cutover:db")
    conditional = RollbackConsideration(
        sid, Reversibility.CONDITIONALLY_REVERSIBLE, (CHANGE,), action="Route back to the source.",
        preconditions=("No write reached the target.",),
    )  # fmt: skip
    assert conditional.preconditions
    for reversibility, missing in [
        (Reversibility.REVERSIBLE, "action"),
        (Reversibility.CONDITIONALLY_REVERSIBLE, "preconditions"),
        (Reversibility.IRREVERSIBLE, "limitations"),
        (Reversibility.UNKNOWN, "limitations"),  # unknown is not safe: it says what is not known
    ]:
        with pytest.raises(InvalidMigrationPlan) as error:
            RollbackConsideration(sid, reversibility, (CHANGE,), action=None if missing == "action" else "x")
        assert missing in fields_of(error)


def test_data_migrations_state_what_is_missing_never_a_volume() -> None:
    data = DataMigration("db", (CHANGE,), source_element_id="db", missing=("Data volume.", "Write rate."))
    assert "volume" not in data.to_dict()
    assert data.missing == ("Data volume.", "Write rate.")
    with pytest.raises(InvalidMigrationPlan):
        DataMigration("db", (CHANGE,))  # about which element


def test_compatibility_is_verified_only_with_evidence() -> None:
    question = "Does the client speak the target's protocol?"
    verified = CompatibilityCheck(
        "proto:api-db", CompatibilityAspect.PROTOCOL, CompatibilityStatus.VERIFIED, question,
        (Trace(TraceKind.EVIDENCE, "validation:run-1"),),
    )  # fmt: skip
    assert verified.status is CompatibilityStatus.VERIFIED
    with pytest.raises(InvalidMigrationPlan):  # the architecture's declaration alone does not verify it
        CompatibilityCheck(
            "proto:api-db", CompatibilityAspect.PROTOCOL, CompatibilityStatus.VERIFIED, question, (CHANGE,)
        )


# --- the proposal ----------------------------------------------------------------------------------


def test_a_proposal_is_internally_consistent_deterministic_and_unscored() -> None:
    provision, configure = step("provision:db", type=StepType.PROVISION), step("configure:db")
    risk = Risk(
        "down:db", RiskCategory.DOWNTIME, RiskStatus.UNKNOWN, "d", "i", (CHANGE,), step_ids=(configure.id,)
    )
    first = MigrationProposal(
        source(), target(), steps=(provision, configure), risks=(risk,), models={"migration": 1}
    )
    again = MigrationProposal(
        source(), target(), steps=(configure, provision), risks=(risk,), models={"migration": 1}
    )
    assert first.to_dict() == again.to_dict()
    assert first.fingerprint == again.fingerprint
    text = json.dumps(first.to_dict())
    assert not any(f'"{word}' in text for word in FORBIDDEN)
    assert first.summary()["steps_by_type"] == {"configure": 1, "provision": 1}
    orphan = Risk(
        "down:x", RiskCategory.DOWNTIME, RiskStatus.UNKNOWN, "d", "i", (CHANGE,), step_ids=(step_id("x"),)
    )
    with pytest.raises(InvalidMigrationPlan) as error:
        MigrationProposal(source(), target(), steps=(configure,), risks=(orphan,))
    assert fields_of(error) == ["risks.step_ids"]
    with pytest.raises(InvalidMigrationPlan) as error:
        MigrationProposal(source(), target(), steps=(configure, step()))
    assert fields_of(error) == ["steps"]  # ids are unique


def test_the_status_follows_the_findings() -> None:
    assert MigrationProposal(source(), target(), steps=(step(),)).status is PlanStatus.DRAFT
    assert MigrationProposal(source(), target()).status is PlanStatus.NEEDS_INFORMATION  # nothing planned
    missing = PlanFinding(
        FindingType.MISSING_INFORMATION, "db:volume", "The data volume is not stated.",
        element_ids=("db",), missing=("Data volume of db.",),
    )  # fmt: skip
    blocked = MigrationProposal(source(), target(), steps=(step(),), findings=(missing,))
    assert blocked.status is PlanStatus.NEEDS_INFORMATION
    evidence_gap = PlanFinding(
        FindingType.MISSING_EVIDENCE, "capacity", "No capacity analysis of revision 2."
    )
    assert (
        MigrationProposal(source(), target(), steps=(step(),), findings=(evidence_gap,)).status
        is PlanStatus.DRAFT
    )


# --- plan versions and review ----------------------------------------------------------------------


def version(status: PlanStatus = PlanStatus.DRAFT) -> MigrationPlanVersion:
    proposal = MigrationProposal(source(), target(), steps=(step(),))
    return MigrationPlanVersion(
        uuid.UUID(int=20), uuid.UUID(int=21), 1, uuid.UUID(int=22), request(), proposal, status, ADA, AT
    )


def test_the_review_lifecycle_is_made_by_people_and_recorded() -> None:
    submitted = version().move(PlanStatus.READY_FOR_REVIEW, user_id=ADA, at=AT)
    approved = submitted.move(PlanStatus.APPROVED, user_id=BOB, at=AT, comment="Reviewed with the team.")
    assert [(r.from_status, r.to_status, r.user_id) for r in approved.reviews] == [
        (PlanStatus.DRAFT, PlanStatus.READY_FOR_REVIEW, ADA),
        (PlanStatus.READY_FOR_REVIEW, PlanStatus.APPROVED, BOB),
    ]
    assert approved.approved_by is not None
    assert approved.approved_by.user_id == BOB
    assert approved.proposal is submitted.proposal  # content never changes with the status
    with pytest.raises(InvalidMigrationRequest) as error:
        submitted.move(PlanStatus.REJECTED, user_id=BOB, at=AT)
    assert error.value.details == {"field": "comment", "reason": "required"}  # a rejection says why
    for status, to in [
        (PlanStatus.DRAFT, PlanStatus.APPROVED),  # never approved without review
        (PlanStatus.NEEDS_INFORMATION, PlanStatus.READY_FOR_REVIEW),  # revised first, never as is
        (PlanStatus.ARCHIVED, PlanStatus.DRAFT),
        (PlanStatus.APPROVED, PlanStatus.APPROVED),
    ]:
        with pytest.raises(InvalidPlanTransition):
            version(status).move(to, user_id=ADA, at=AT)


def test_there_is_no_execution_status() -> None:
    expected = {
        "draft",
        "needs_information",
        "ready_for_review",
        "approved",
        "rejected",
        "superseded",
        "archived",
    }
    assert {s.value for s in PlanStatus} == expected
    assert not any(word in s.value for s in PlanStatus for word in ("execut", "running", "done", "complete"))
