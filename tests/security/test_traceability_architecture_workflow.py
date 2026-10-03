"""Autonomous Architecture Workflow: each testing requirement (sections 42 to 50) mapped to the tests
that prove it, plus structural guarantees — only a person's approval in the service writes an
architecture; the API has no endpoint to deploy, execute, provision or change anything outside the
workflow; the evaluation covers its named workflows; the documentation covers every topic of section
51 without claiming more than the evidence supports. Fails if a mapped test is renamed or removed, or
a requirement is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

from ai.evaluation.architecture_workflow import load_scenarios

U = "tests.unit.architecture_workflow"
DOMAIN = f"{U}.test_workflow_domain"
CTRL = f"{U}.test_workflow_controller"
EXEC = f"{U}.test_workflow_executors"
REC = f"{U}.test_workflow_recovery"
SAFE = f"{U}.test_workflow_safety"
RECORDS = f"{U}.test_workflow_records"
EVIDENCE = f"{U}.test_workflow_evidence"
API = "tests.integration.api.test_architecture_workflows"
PERSIST = "tests.integration.api.test_workflow_persistence"
EVAL = "tests.evaluation.architecture.test_workflow_regression"
S = "tests.security"
SEC = f"{S}.test_workflow_safety"
TENANTS = (
    f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing"
)
AUDIT = f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text"
MASS = f"{S}.test_mass_assignment_sweep::test_every_body_rejects_undeclared_privileged_fields"
REGRESSION = f"{EVAL}::test_the_metrics_do_not_regress"
LEASE = f"{PERSIST}::test_a_lease_keeps_others_out_until_it_expires_then_another_resumes"
LIMITS = f"{DOMAIN}::test_every_expensive_action_is_checked_before_it_runs"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "architecture-workflow.md"
ADR = DOCS / "adr" / "ADR-026-bounded-autonomous-workflow.md"
API_DOC = DOCS / "api" / "architecture-workflows.md"
CONTRACT = DOCS / "frontend" / "architecture-workflow-contract.md"
DATASET_README = ROOT / "ai" / "evaluation" / "datasets" / "architecture_workflow" / "v1" / "README.md"
ROUTES = ROOT / "apps" / "api" / "routes" / "architecture_workflows.py"
SERVICE = ROOT / "core" / "domain" / "architecture_workflow" / "workflow_service.py"

ACCEPTANCE: dict[str, list[str]] = {
    # 42.1 State machine.
    "42.1 every valid transition": [f"{DOMAIN}::test_every_valid_transition"],
    "42.1 created, running, failed, cancelled to approved are refused": [
        f"{DOMAIN}::test_nothing_reaches_approval_but_through_review",
        f"{DOMAIN}::test_every_other_transition_is_refused",
    ],
    # 43 Workflow integration.
    "43 goal to review through every stage, with mocked providers and deterministic engines": [
        f"{EXEC}::test_a_goal_becomes_a_validated_analyzed_candidate_for_review",
        f"{REC}::test_the_reference_run_passes_every_named_stage",
    ],
    "43 requirements stage": [
        f"{CTRL}::test_a_person_confirms_requirements_before_any_design",
        f"{API}::test_requirements_are_confirmed_by_a_person_before_design",
    ],
    "43 every stage produces persisted state": [
        f"{PERSIST}::test_a_worker_carries_a_workflow_to_review_and_everything_is_stored",
        f"{API}::test_a_goal_becomes_an_architecture_only_when_a_person_approves",
    ],
    # 44 Iteration.
    "44 validation failure, improvement, candidate 2, validation, analysis": [
        f"{CTRL}::test_validation_blocks_are_answered_by_the_agent_and_kept",
        f"{EXEC}::test_findings_drive_rule_improvements_kept_with_their_parents",
    ],
    "44 candidate 1 remains immutable": [
        f"{DOMAIN}::test_a_candidates_architecture_never_changes",
        f"{SAFE}::test_a_step_that_rewrites_a_candidate_stops_the_workflow_and_keeps_nothing",
        f"{RECORDS}::test_a_candidate_reads_back_as_it_was_and_never_as_another_architecture",
        f"{PERSIST}::test_the_records_are_guarded",
    ],
    "44 candidate 2 references candidate 1": [
        f"{DOMAIN}::test_the_first_candidate_is_the_agents_and_improvements_have_lineage",
        f"{CTRL}::test_a_finding_drives_an_improvement_that_keeps_its_parent",
    ],
    "44 findings preserved": [
        f"{EVIDENCE}::test_each_engines_results_become_current_evidence_for_the_candidate",
        f"{CTRL}::test_a_finding_drives_an_improvement_that_keeps_its_parent",
    ],
    "44 analysis associated with the correct candidate": [
        f"{EXEC}::test_findings_drive_rule_improvements_kept_with_their_parents",
        REGRESSION,  # grounded_triggers, triggers_resolved
    ],
    # 45 Budgets.
    "45 iteration limit": [
        f"{CTRL}::test_no_iterations_means_review_after_the_first_candidate",
        f"{CTRL}::test_an_unanswered_finding_at_the_iteration_limit_is_said",
    ],
    "45 LLM-call limit": [LIMITS, REGRESSION],  # budget-exhausted
    "45 tool-call limit": [LIMITS],
    "45 context limit": [LIMITS],
    "45 workflow timeout": [f"{CTRL}::test_the_time_limit_stops_it"],
    "45 simulation limit": [LIMITS],
    "45 stops safely": [
        f"{CTRL}::test_a_limit_reached_with_a_valid_candidate_goes_to_review",
        f"{CTRL}::test_a_limit_reached_with_nothing_valid_fails",
        f"{API}::test_a_lowered_budget_is_kept",
    ],
    # 46 Resume.
    "46 after requirement analysis": [
        f"{REC}::test_resuming_after_the_requirement_analysis_does_not_analyze_again"
    ],
    "46 after knowledge retrieval, generation, validation, capacity analysis, comparison": [
        f"{REC}::test_resuming_after_any_step_repeats_nothing",
        f"{CTRL}::test_resuming_continues_after_the_last_completed_step",
    ],
    "46 no duplicated side effects": [
        f"{CTRL}::test_a_crash_is_retried_once_and_completed_steps_never_repeat",
        f"{DOMAIN}::test_an_operation_has_one_stable_identity",
        LEASE,
    ],
    # 47 Concurrency.
    "47 two workflows on one revision: one approval, one stale conflict": [
        f"{API}::test_two_workflows_on_one_revision_only_one_is_approved"
    ],
    "47 one worker at a time": [LEASE],
    # 48 Security.
    "48 cross-tenant workflow access": [
        f"{API}::test_workflows_of_other_projects_and_tenants_are_not_found",
        TENANTS,
    ],
    "48 cross-project workflow access": [
        f"{API}::test_workflows_of_other_projects_and_tenants_are_not_found"
    ],
    "48 unauthorized approval": [f"{API}::test_a_viewer_reads_but_does_not_run_or_decide"],
    "48 IDOR": [f"{SEC}::test_a_candidate_is_addressed_only_through_its_own_workflow"],
    "48 prompt injection": [
        f"{SEC}::test_injected_instructions_in_the_goal_and_documents_stay_data",
        f"{SEC}::test_a_hostile_model_output_cannot_act",
    ],
    "48 RAG injection": [
        f"{SEC}::test_injected_instructions_in_the_goal_and_documents_stay_data",
        f"{SEC}::test_only_the_workflows_project_knowledge_reaches_the_model",
    ],
    "48 tool escalation": [
        f"{DOMAIN}::test_actions_run_only_when_registered_running_and_in_their_stage",
        f"{DOMAIN}::test_every_action_is_registered_and_none_changes_the_canonical_architecture",
        f"{SEC}::test_a_hostile_model_output_cannot_act",
    ],
    "48 unauthorized canonical mutation": [
        f"{SAFE}::test_nothing_the_workflow_runs_can_write_an_architecture_or_reach_outside",
        f"{API}::test_permissions_are_checked_again_before_every_action",
    ],
    "48 sensitive information leakage": [
        f"{SEC}::test_secrets_and_prompts_are_never_sent_returned_stored_or_logged",
        f"{SAFE}::test_every_step_is_observable_without_the_goal",
        AUDIT,
    ],
    "48 workflow manipulation through crafted inputs": [
        f"{SEC}::test_a_crafted_goal_cannot_set_what_the_server_sets",
        f"{SEC}::test_crafted_input_cannot_move_a_workflow",
        f"{API}::test_a_goal_is_checked_before_it_is_queued",
        MASS,
    ],
    # 49 AI evaluation.
    "49 requirements, architecture, analysis, iteration and safety measured": [
        REGRESSION,
        f"{EVAL}::test_every_metric_has_something_to_measure",
    ],
    "49 not judged by convincing prose": [
        f"{EVAL}::test_the_graders_can_fail",
        f"{EVAL}::test_a_wrong_expectation_is_a_miss",
    ],
    "49 versioned and deterministic": [
        f"{EVAL}::test_the_evaluation_is_deterministic",
        f"{EVAL}::test_the_set_is_well_formed_and_its_size_is_stated",
        f"{EVAL}::test_every_metric_has_a_threshold_and_ceilings_hold_at_zero",
    ],
    # 50 Failure scenarios.
    "50 LLM unavailable": [f"{EXEC}::test_without_a_model_the_workflow_fails_llm_unavailable", REGRESSION],
    "50 RAG unavailable": [f"{EXEC}::test_a_retrieval_failure_is_said_and_the_design_proceeds"],
    "50 capacity engine unavailable": [
        f"{REC}::test_an_engine_that_cannot_run_is_reported_as_failed_never_as_a_result"
    ],
    "50 invalid candidate": [
        f"{CTRL}::test_validation_blocks_are_answered_by_the_agent_and_kept",
        f"{CTRL}::test_no_valid_candidate_stops_with_why",
    ],
    "50 conflicting requirements": [
        f"{EXEC}::test_gaps_in_the_requirements_wait_for_a_person_then_resume",
        REGRESSION,  # conflicting-requirements
    ],
    "50 infinite improvement loop": [
        f"{CTRL}::test_an_unanswered_finding_at_the_iteration_limit_is_said",
        f"{CTRL}::test_a_second_failure_is_final",
    ],
    "50 database failure": [f"{REC}::test_a_lost_commit_loses_only_that_step_which_runs_again", LEASE],
    "50 user cancels": [
        f"{CTRL}::test_a_cancellation_stops_every_future_step_and_keeps_history",
        f"{PERSIST}::test_a_persons_cancellation_is_never_overwritten",
        f"{API}::test_cancelling_and_rejecting_are_recorded",
    ],
}
SCENARIOS = {
    "goal-to-review", "requirements-confirmed-first", "nothing-to-extract", "clarification-then-design",
    "conflicting-requirements", "rule-improvements", "iteration-limit", "model-unavailable",
    "prompt-injection", "knowledge-unavailable", "grounded-citation", "assumption-disclosed",
    "permission-revoked", "simulation-scenario", "budget-exhausted",
}  # fmt: skip
TOPICS = (
    "## Autonomous workflow architecture", "## State machine", "## Stage lifecycle",
    "## Planner and controller responsibilities", "## LLM responsibilities", "## Tool registry",
    "## Side-effect policy", "## Approval model", "## Candidate lifecycle", "## Checkpointing",
    "## Retry behavior", "## Budgets", "## Cancellation", "## Concurrency", "## Security",
    "## Prompt injection controls", "## API contracts", "## Frontend workflow", "## Observability",
    "## Evaluation methodology", "## Known limitations",
)  # fmt: skip
STATEMENT = (
    "The architecture workflow is a bounded autonomous architecture engineering workflow. It proposes "
    "and analyzes candidate architectures; it does not determine architectural correctness, and nothing "
    "it produces becomes an architecture without a person's approval."
)
OVERCLAIMS = (
    r"fully autonomous|production[- ]ready|zero hallucinations?|zero security risk|optimal architecture"
    r"|correct architecture|guarantee[sd]?"
)


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_requirement_is_mapped() -> None:
    sections = {criterion.split()[0] for criterion in ACCEPTANCE}
    assert sections == {"42.1", "43", "44", "45", "46", "47", "48", "49", "50"}
    # 2 state, 3 integration, 5 iteration, 7 budget, 3 resume, 2 concurrency, 10 security, 3 evaluation,
    # 8 failure scenarios
    assert len(ACCEPTANCE) == 43


def test_the_evaluation_covers_the_named_workflows() -> None:
    assert {s["id"] for s in load_scenarios()} == SCENARIOS


def test_only_a_persons_approval_writes_an_architecture() -> None:
    """In the service, the architecture workflow is called from ``approve`` and nowhere else."""
    tree = ast.parse(SERVICE.read_text())
    writers = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for call in ast.walk(function):
            if (
                isinstance(call, ast.Attribute)
                and isinstance(call.value, ast.Attribute)
                and call.value.attr == "_architectures"
            ):
                writers.add(function.name)
    assert writers == {"approve"}


def test_there_is_no_endpoint_to_execute_deploy_or_provision_anything() -> None:
    text = ROUTES.read_text()
    for verb in ("put", "patch", "delete"):
        assert f"@router.{verb}(" not in text
    for word in ("deploy", "execute", "provision", "apply", "migrate", "shell", "sql", "run-tool", "auto"):
        assert not re.search(rf'["/][a-z-]*{word}', text), word
    assert text.count("@router.post(") == 5  # start, input, cancel, reject, approve
    assert text.count("@router.get(") == 3  # list, read, candidate


def test_nothing_is_claimed_beyond_the_evidence() -> None:
    """No 'fully autonomous', 'production ready', 'guaranteed' and the like — unless negated."""
    for path in (DOC, ADR, API_DOC, CONTRACT, DATASET_README):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(rf"[^.]*\b(?:{OVERCLAIMS})\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Section 51's topics, the statement of what the workflow is, and its configuration."""
    text = DOC.read_text()
    for heading in TOPICS:
        assert heading in text, heading
    for path in (DOC, ADR):
        assert STATEMENT in " ".join(path.read_text().replace(">", " ").split()), path.name
    environment = (ROOT / ".env.example").read_text()
    for variable in (
        "ARCHITECTURE_WORKFLOW_MAX_ITERATIONS",
        "ARCHITECTURE_WORKFLOW_MAX_LLM_CALLS",
        "ARCHITECTURE_WORKFLOW_MAX_SECONDS",
        "ARCHITECTURE_WORKFLOW_LEASE_SECONDS",
    ):
        assert variable in environment
        assert variable in text + API_DOC.read_text()
    assert "## Decision" in ADR.read_text()
    assert "not integrated yet" in CONTRACT.read_text()
