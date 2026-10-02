import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from core.architecture_ir.model import ArchitectureIR
from core.domain.architecture_agent.errors import (
    InvalidAgentRecord,
    InvalidAgentRequest,
    InvalidAgentTransition,
)
from core.domain.architecture_agent.proposals import (
    Answer,
    Claim,
    ClarificationQuestion,
    DesignDecision,
    Proposal,
    ProposedConnection,
    ProposedNode,
)
from core.domain.architecture_agent.requests import AgentRequest, AgentUsage, BaseRevision, Budget
from core.domain.architecture_agent.results import (
    AgentFinding,
    Candidate,
    EngineReport,
    EvidenceRef,
    Rejection,
)
from core.domain.architecture_agent.runs import AcceptedRevision, AgentRun, RawOutput, RunFailure
from core.domain.architecture_agent.values import (
    STAGES,
    TERMINAL,
    Basis,
    EngineStatus,
    FailureCode,
    QuestionKind,
    RunStatus,
    Stage,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
USER = uuid.uuid4()
SET = uuid.uuid4()


def a_request(**overrides: object) -> AgentRequest:
    fields: dict[str, object] = {"requirement_set_id": SET, "objective": "An order service for a web shop"}
    return AgentRequest(**(fields | overrides))  # type: ignore[arg-type]


def a_run(**overrides: object) -> AgentRun:
    fields: dict[str, object] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "requested_by_user_id": USER,
        "requested_at": NOW,
        "request": a_request(),
    }
    return AgentRun(**(fields | overrides))  # type: ignore[arg-type]


def a_proposal() -> Proposal:
    api = ProposedNode("api", "service", "Orders API", "Serves orders", requirement_refs=("REQ-1",))
    db = ProposedNode("db", "database", "Orders DB", "Stores orders")
    link = ProposedConnection("api-db", "api", "db", "sync", "Reads and writes orders", protocol="tcp")
    decision = DesignDecision(
        "Storage", "A relational database", "Orders are relational", ("A document store",)
    )
    claim = Claim("Orders must survive a node loss", Basis.ASSUMPTION, ("REQ-1",))
    return Proposal("Orders", "A service and its database", (api, db), (link,), (decision,), (claim,))


def a_candidate() -> Candidate:
    return Candidate(ArchitectureIR("orders"), normalizations=("ids lower-cased",))


def validation() -> EngineReport:
    return EngineReport("validation", EngineStatus.EVALUATED, {"engine": "1"}, summary={"errors": 0})


def a_question(**overrides: object) -> ClarificationQuestion:
    fields: dict[str, object] = {
        "kind": QuestionKind.MISSING_CONCERN,
        "question": "What availability is required?",
        "requirement_refs": ("REQ-2", "REQ-1", "REQ-1"),
    }
    return ClarificationQuestion(**(fields | overrides))  # type: ignore[arg-type]


def ready_run() -> AgentRun:
    return a_run().start(NOW).ready(a_proposal(), a_candidate(), (validation(),), NOW)


def any_revision() -> AcceptedRevision:
    return AcceptedRevision(uuid.uuid4(), 1, "0" * 64)


# --- vocabulary ---------------------------------------------------------------------------------


def test_stages_are_fixed_and_ordered() -> None:
    assert STAGES[0] is Stage.INTAKE
    assert STAGES[-1] is Stage.DECISION
    assert len(STAGES) == 10


def test_terminal_statuses() -> None:
    assert {RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.ACCEPTED, RunStatus.REJECTED} == TERMINAL
    assert RunStatus.CANDIDATE_READY not in TERMINAL


# --- request ------------------------------------------------------------------------------------


def test_request_is_cleaned_and_kept_apart() -> None:
    request = a_request(
        objective="  Orders\r\nservice  ",
        constraints=("must run on AWS", "must run on AWS", "no Kafka"),
        preferences=("prefer managed services",),
    )
    assert request.objective == "Orders\nservice"
    assert request.constraints == ("must run on AWS", "no Kafka")
    assert request.preferences == ("prefer managed services",)
    assert request.exclusions == ()
    assert request.context is None
    assert request.to_dict()["base"] is None


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"objective": "   "}, "objective", "required"),
        ({"objective": "x" * 4001}, "objective", "too_long"),
        ({"objective": "bad" + chr(0x202E)}, "objective", "invalid_text"),
        ({"constraints": tuple(f"c{i}" for i in range(21))}, "constraints", "too_many"),
        ({"preferences": ("x" * 301,)}, "preferences", "too_long"),
        ({"exclusions": ["not a tuple"]}, "exclusions", "too_many"),
        ({"requirement_set_id": "nope"}, "requirement_set_id", "required"),
        ({"context": "x" * 4001}, "context", "too_long"),
    ],
)
def test_invalid_requests(overrides: dict[str, object], field: str, reason: str) -> None:
    with pytest.raises(InvalidAgentRequest) as caught:
        a_request(**overrides)
    assert caught.value.details == {"field": field, "reason": reason}


def test_an_iteration_names_an_exact_revision() -> None:
    base = BaseRevision(uuid.uuid4(), 3)
    assert a_request(base=base).to_dict()["base"] == {
        "architecture_id": str(base.architecture_id),
        "number": 3,
    }
    with pytest.raises(InvalidAgentRequest):
        BaseRevision(uuid.uuid4(), 0)


# --- budget and usage ---------------------------------------------------------------------------


def test_budget_bounds_the_loop() -> None:
    assert Budget().max_model_calls == 2
    with pytest.raises(InvalidAgentRequest):
        Budget(max_model_calls=4)
    with pytest.raises(InvalidAgentRecord):
        Budget(max_seconds=0)


def test_unknown_tokens_stay_unknown() -> None:
    usage = AgentUsage().with_model_call(100, None, 40)
    assert usage.model_calls == 1
    assert usage.input_tokens == 100
    assert usage.output_tokens is None
    assert usage.with_model_call(5, 5, 1).output_tokens is None
    assert usage.to_dict()["cost"] is None


def test_budget_exhaustion_names_the_limit() -> None:
    budget = Budget(max_model_calls=1, max_input_tokens=1000, max_seconds=10)
    assert AgentUsage().exceeded(budget, 1) is None
    assert AgentUsage(model_calls=2).exceeded(budget, 1) == "model_calls"
    assert AgentUsage(input_tokens=1001).exceeded(budget, 1) == "input_tokens"
    assert AgentUsage(input_tokens=None).exceeded(budget, 11) == "seconds"


# --- proposals ----------------------------------------------------------------------------------


def test_a_proposal_keeps_each_basis_apart() -> None:
    proposal = a_proposal()
    assert proposal.claims_of(Basis.ASSUMPTION)[0].requirement_refs == ("REQ-1",)
    assert proposal.claims_of(Basis.RETRIEVED) == ()


def test_a_retrieved_claim_must_cite_its_passage() -> None:
    with pytest.raises(InvalidAgentRecord):
        Claim("The runbook says two zones", Basis.RETRIEVED)
    assert Claim("The runbook says two zones", Basis.RETRIEVED, evidence=("kch_1",)).evidence == ("kch_1",)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Proposal("x", "y", ()),
        lambda: ProposedNode("bad id!", "service", "n", "r"),
        lambda: ProposedNode("a", "service", "n", "r", requirement_refs=("REQ-0",)),
        lambda: ProposedNode("a", "service", "n", "r", configuration={"x": object()}),
        lambda: ProposedConnection("c", "a", "b", "sync", "r", requirement_refs=("req-1",)),
        lambda: DesignDecision("t", "c", "r", alternatives=tuple("abc" for _ in range(31))),
        lambda: Claim("s", "proposed"),  # type: ignore[arg-type]
    ],
)
def test_malformed_proposals_are_refused(build: object) -> None:
    with pytest.raises(InvalidAgentRecord):
        build()  # type: ignore[operator]


def test_proposal_size_is_bounded() -> None:
    nodes = tuple(ProposedNode(f"n{i}", "service", "n", "r") for i in range(61))
    with pytest.raises(InvalidAgentRecord):
        Proposal("x", "y", nodes)


# --- questions and answers ----------------------------------------------------------------------


def test_the_same_gap_is_the_same_question() -> None:
    question = a_question()
    assert question.requirement_refs == ("REQ-1", "REQ-2")
    assert question.id == a_question(requirement_refs=("REQ-1", "REQ-2")).id
    assert question.id != a_question(question="What latency is required?").id
    assert question.id.startswith("aq_")


def test_an_answer_becomes_a_user_provided_fact() -> None:
    question = a_question()
    claim = Answer(question.id, "99.9% monthly", USER, NOW).as_claim(question)
    assert claim.basis is Basis.USER_PROVIDED
    assert claim.requirement_refs == ("REQ-1", "REQ-2")
    assert "99.9% monthly" in claim.statement


# --- results ------------------------------------------------------------------------------------


def test_candidate_hash_is_the_ir_hash() -> None:
    assert a_candidate().content_hash == Candidate(ArchitectureIR("orders")).content_hash
    assert a_candidate().content_hash != Candidate(ArchitectureIR("other")).content_hash


def test_engine_reports_say_why() -> None:
    finding = AgentFinding("validation", "V-1", "error", "Orphan node", ("db",))
    assert validation().to_dict()["status"] == "evaluated"
    assert EngineReport("validation", EngineStatus.EVALUATED, findings=(finding,)).findings == (finding,)
    skipped = EngineReport("capacity", EngineStatus.NOT_EVALUATED, limitations=("no workload declared",))
    assert skipped.to_dict()["limitations"] == ["no workload declared"]
    with pytest.raises(InvalidAgentRecord):
        EngineReport("capacity", EngineStatus.NOT_EVALUATED)  # never silently skipped
    with pytest.raises(InvalidAgentRecord):
        EngineReport("security", EngineStatus.FAILED)  # a failure has a code
    with pytest.raises(InvalidAgentRecord):
        EngineReport("security", EngineStatus.EVALUATED, error="engine_error")
    with pytest.raises(InvalidAgentRecord):
        EngineReport("security", EngineStatus.FAILED, findings=(finding,), error="engine_error")


def test_rejections_and_evidence_are_well_formed() -> None:
    assert (
        Rejection("unknown_kind", "nodes[0].kind", "Not an IR node kind").to_dict()["code"] == "unknown_kind"
    )
    with pytest.raises(InvalidAgentRecord):
        Rejection("Unknown Kind", "nodes[0]", "x")
    with pytest.raises(InvalidAgentRecord):
        EvidenceRef("kch_1", "ks_1", 0, "Runbook")


# --- run lifecycle ------------------------------------------------------------------------------


def test_the_happy_path_is_recorded() -> None:
    run = ready_run()
    assert run.status is RunStatus.CANDIDATE_READY
    assert run.stage is Stage.REVIEW
    assert run.completed_at == NOW
    assert run.candidate is not None
    revision = AcceptedRevision(uuid.uuid4(), 1, run.candidate.content_hash)
    accepted = run.accept(revision, USER, NOW + timedelta(minutes=5))
    assert accepted.status is RunStatus.ACCEPTED
    assert accepted.accepted == revision
    statuses = [e.status for e in accepted.history]
    assert statuses == [RunStatus.RUNNING, RunStatus.CANDIDATE_READY, RunStatus.ACCEPTED]
    assert accepted.history[-1].user_id == USER
    assert accepted.history[0].user_id is None


def test_only_the_reviewed_candidate_can_be_accepted() -> None:
    with pytest.raises(InvalidAgentRequest) as caught:
        ready_run().accept(any_revision(), USER, NOW)
    assert caught.value.details == {"field": "content_hash", "reason": "not_the_candidate"}


def test_a_candidate_needs_a_validation_report() -> None:
    skipped = EngineReport("reliability", EngineStatus.NOT_EVALUATED, limitations=("x",))
    with pytest.raises(InvalidAgentRequest):
        a_run().start(NOW).ready(a_proposal(), a_candidate(), (skipped,), NOW)


def test_a_failed_run_has_no_candidate_and_cannot_be_accepted() -> None:
    failure = RunFailure(FailureCode.LLM_TIMEOUT, "The model did not answer in time.", Stage.PROPOSAL)
    failed = a_run().start(NOW).fail(failure, NOW)
    assert failed.status is RunStatus.FAILED
    assert failed.stage is Stage.PROPOSAL
    assert failed.candidate is None
    assert failure.to_dict()["code"] == "llm_timeout"
    with pytest.raises(InvalidAgentTransition):
        failed.accept(any_revision(), USER, NOW)
    with pytest.raises(InvalidAgentTransition):
        failed.start(NOW)


def test_a_rejected_proposal_keeps_why() -> None:
    message = "The proposal cannot become an architecture."
    failure = RunFailure(FailureCode.PROPOSAL_REJECTED, message, Stage.CONSTRUCTION)
    rejection = Rejection("unknown_kind", "nodes[0].kind", "Not an IR node kind")
    failed = a_run().start(NOW).fail(failure, NOW, proposal=a_proposal(), rejections=(rejection,))
    assert failed.rejections == (rejection,)
    assert failed.proposal is not None


@pytest.mark.parametrize(
    ("make", "to"),
    [
        (lambda: a_run().ready(a_proposal(), a_candidate(), (validation(),), NOW), "candidate_ready"),
        (lambda: ready_run().cancel(USER, NOW), "cancelled"),
        (lambda: ready_run().reject("no", USER, NOW).accept(any_revision(), USER, NOW), "accepted"),
        (lambda: a_run().cancel(USER, NOW).start(NOW), "running"),
    ],
)
def test_illegal_moves_are_refused(make: object, to: str) -> None:
    with pytest.raises(InvalidAgentTransition) as caught:
        make()  # type: ignore[operator]
    assert caught.value.details["to"] == to


def test_a_rejection_records_why() -> None:
    rejected = ready_run().reject("Too many services", USER, NOW)
    assert rejected.status is RunStatus.REJECTED
    assert rejected.decision_reason == "Too many services"
    assert rejected.candidate is not None  # kept for the record


def test_clarification_pauses_and_resumes_the_same_run() -> None:
    question = a_question()
    optional = a_question(question="Any preferred region?", blocking=False)
    waiting = a_run().start(NOW).ask((question, optional), NOW)
    assert waiting.status is RunStatus.AWAITING_CLARIFICATION
    assert waiting.unanswered == (question,)
    with pytest.raises(InvalidAgentRequest) as caught:
        waiting.answer((Answer(optional.id, "eu-west-1", USER, NOW),), USER, NOW)
    assert caught.value.details["reason"] == "blocking_questions_unanswered"
    with pytest.raises(InvalidAgentRequest) as unknown:
        waiting.answer((Answer("aq_nope", "x", USER, NOW),), USER, NOW)
    assert unknown.value.details["reason"] == "unknown_question"
    resumed = waiting.answer((Answer(question.id, "99.9%", USER, NOW),), USER, NOW)
    assert resumed.status is RunStatus.RUNNING
    assert resumed.id == waiting.id
    assert resumed.unanswered == ()
    assert resumed.history[-1].user_id == USER


def test_asking_needs_a_blocking_question() -> None:
    with pytest.raises(InvalidAgentRequest):
        a_run().start(NOW).ask((a_question(blocking=False),), NOW)


def test_the_same_question_is_asked_once() -> None:
    question = a_question()
    waiting = a_run().start(NOW).ask((question,), NOW)
    resumed = waiting.answer((Answer(question.id, "99.9%", USER, NOW),), USER, NOW)
    with pytest.raises(InvalidAgentRequest):
        resumed.ask((a_question(),), NOW)  # already answered: nothing left to wait on


def test_stages_move_only_while_running() -> None:
    running = a_run().start(NOW)
    assert running.at_stage(Stage.CONTEXT).stage is Stage.CONTEXT
    with pytest.raises(InvalidAgentTransition):
        a_run().at_stage(Stage.CONTEXT)


def test_model_use_is_recorded_without_its_output() -> None:
    raw = RawOutput("a" * 64, 1234)
    usage = AgentUsage(model_calls=1)
    run = a_run().start(NOW).with_model("anthropic/claude", "architecture-agent.v1", usage, raw)
    assert run.raw_output == raw
    assert run.prompt_version == "architecture-agent.v1"
    with pytest.raises(InvalidAgentRecord):
        RawOutput("not-a-hash", 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": RunStatus.CANDIDATE_READY},  # without a candidate
        {"status": RunStatus.FAILED},  # without a failure
        {"candidate": Candidate(ArchitectureIR("x"))},  # a candidate while queued
        {"status": RunStatus.AWAITING_CLARIFICATION},  # waiting on nothing
        {"failure": RunFailure(FailureCode.ENGINE_ERROR, "x", Stage.VALIDATION)},
    ],
)
def test_inconsistent_runs_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidAgentRecord):
        a_run(**overrides)


def test_records_are_immutable() -> None:
    run = a_run()
    with pytest.raises(AttributeError):
        run.status = RunStatus.RUNNING  # type: ignore[misc]
    assert replace(run, stage=Stage.CONTEXT).stage is Stage.CONTEXT
