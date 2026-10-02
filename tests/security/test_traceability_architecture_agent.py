"""LLM Architecture Agent: each testing requirement (sections 13.1 to 13.6) mapped to the tests that prove
it, plus structural guarantees — the model has no tools and the agent runs no process, network, SQL or
filesystem action; only a person's acceptance writes an architecture; there is no streaming, fix,
apply or auto-accept endpoint; the documentation covers every topic without claiming a guarantee.
Fails if a mapped test is renamed or removed, or a requirement is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

from ai.evaluation.architecture_agent import load_scenarios

U = "tests.unit.architecture_agent"
DOMAIN = f"{U}.test_agent_domain"
RECORDS = f"{U}.test_agent_records"
CONTEXT = f"{U}.test_agent_context"
CANDIDATE = f"{U}.test_agent_candidate"
PIPELINE = f"{U}.test_agent_pipeline"
AGENT = "tests.unit.ai.test_architecture_agent"
SCHEMA = "tests.unit.ai.test_structured_output"
PROVIDER = "tests.unit.ai.test_anthropic_provider"
API = "tests.integration.api.test_architecture_agent"
EVAL = "tests.evaluation.architecture.test_agent_regression"
S = "tests.security"
SAFETY = f"{S}.test_agent_safety"
TENANTS = (
    f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing"
)
HERE = f"{S}.test_traceability_architecture_agent"
REGRESSION = f"{EVAL}::test_the_guardrails_do_not_regress"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "architecture-agent.md"
ADR = DOCS / "adr" / "ADR-024-bounded-architecture-agent.md"
ROUTES = ROOT / "apps" / "api" / "routes" / "architecture_agent.py"
AGENT_CODE = (
    ROOT / "core" / "domain" / "architecture_agent",
    ROOT / "engines" / "architecture_agent",
    ROOT / "ai" / "agents" / "architecture_agent.py",
)

ACCEPTANCE: dict[str, list[str]] = {
    # 13.1 Unit tests.
    "13.1 request validation": [
        f"{DOMAIN}::test_invalid_requests",
        f"{DOMAIN}::test_request_is_cleaned_and_kept_apart",
    ],
    "13.1 requirement interpretation contract": [
        f"{CONTEXT}::test_missing_traffic_and_availability_block",
        f"{CONTEXT}::test_vague_requirements_are_asked_without_blocking",
        f"{CONTEXT}::test_requirements_are_labelled_by_reference",
    ],
    "13.1 context assembly and limits": [
        f"{CONTEXT}::test_passages_give_way_to_the_budget_and_say_so",
        f"{CONTEXT}::test_the_essentials_are_never_cut",
        f"{CONTEXT}::test_retrieval_is_bounded",
    ],
    "13.1 evidence/provenance preservation": [
        f"{CANDIDATE}::test_everything_is_an_unverified_model_proposal",
        f"{CANDIDATE}::test_normalizations_evidence_and_gaps_are_recorded",
        f"{DOMAIN}::test_a_retrieved_claim_must_cite_its_passage",
    ],
    "13.1 structured output parsing": [
        f"{AGENT}::test_a_valid_output_becomes_a_proposal",
        f"{AGENT}::test_outputs_off_the_schema_are_malformed",
        f"{SCHEMA}::test_agrees_with_jsonschema",
    ],
    "13.1 candidate IR construction": [
        f"{CANDIDATE}::test_a_valid_proposal_becomes_canonical_ir",
        f"{CANDIDATE}::test_assumptions_become_ir_assumptions",
    ],
    "13.1 referential integrity": [
        f"{CANDIDATE}::test_invalid_proposals_are_rejected_whole",
        f"{CANDIDATE}::test_a_label_outside_the_set_is_not_traced",
    ],
    "13.1 configuration validation": [f"{CANDIDATE}::test_invalid_proposals_are_rejected_whole"],
    "13.1 prompt version selection": [f"{AGENT}::test_the_prompt_is_versioned_and_closed"],
    "13.1 tool allowlist and argument validation": [
        f"{HERE}::test_the_model_has_no_tools_and_the_agent_runs_nothing",
        f"{PIPELINE}::test_a_covered_set_becomes_a_validated_candidate",
    ],
    "13.1 run lifecycle transitions": [
        f"{DOMAIN}::test_illegal_moves_are_refused",
        f"{DOMAIN}::test_inconsistent_runs_are_refused",
        f"{DOMAIN}::test_clarification_pauses_and_resumes_the_same_run",
    ],
    "13.1 usage-budget enforcement": [
        f"{AGENT}::test_the_budget_limits_calls",
        f"{AGENT}::test_a_retry_needs_tokens_left",
        f"{PIPELINE}::test_the_time_budget_stops_a_pass",
    ],
    "13.1 error normalization": [
        f"{AGENT}::test_errors_never_escape",
        f"{PIPELINE}::test_a_model_failure_fails_the_run_without_a_candidate",
    ],
    # 13.2 Provider tests (mocked responses).
    "13.2 valid structured output": [
        f"{PROVIDER}::test_a_structured_answer",
        f"{AGENT}::test_one_call_on_success",
    ],
    "13.2 invalid JSON": [f"{PROVIDER}::test_failures_map_to_the_port_errors"],
    "13.2 schema mismatch": [f"{AGENT}::test_malformed_output_is_retried_once_then_fails"],
    "13.2 truncated output": [f"{PROVIDER}::test_a_cut_off_answer_is_not_retried"],
    "13.2 refusal": [f"{PROVIDER}::test_a_refusal_is_unavailable_and_not_retried"],
    "13.2 timeout": [
        f"{PROVIDER}::test_timeouts_and_connection_failures",
        f"{AGENT}::test_two_failures_end_the_stage",
    ],
    "13.2 rate limiting": [f"{PROVIDER}::test_only_transient_failures_are_retryable"],
    "13.2 provider outage": [
        f"{PROVIDER}::test_only_transient_failures_are_retryable",
        f"{API}::test_without_a_model_a_run_fails_llm_unavailable",
    ],
    "13.2 retryable and non-retryable errors": [
        f"{AGENT}::test_a_retryable_failure_is_retried_once",
        f"{AGENT}::test_permanent_failures_are_not_retried",
    ],
    "13.2 usage metadata present or absent": [
        f"{AGENT}::test_a_retryable_failure_is_retried_once",
        f"{DOMAIN}::test_unknown_tokens_stay_unknown",
    ],
    # 13.3 Integration tests.
    "13.3 authorized project run creation": [
        f"{API}::test_a_covered_set_reaches_a_validated_candidate",
        f"{API}::test_a_viewer_reads_but_does_not_run",
    ],
    "13.3 retrieval scope enforcement": [f"{SAFETY}::test_only_the_runs_project_knowledge_reaches_the_model"],
    "13.3 candidate generation and validation": [f"{API}::test_a_covered_set_reaches_a_validated_candidate"],
    "13.3 deterministic engine invocation": [
        f"{PIPELINE}::test_a_covered_set_becomes_a_validated_candidate",
        f"{PIPELINE}::test_a_failing_analysis_engine_is_reported_not_hidden",
    ],
    "13.3 candidate persistence": [
        f"{API}::test_the_stored_record_holds",
        f"{RECORDS}::test_a_ready_run_survives_storage",
    ],
    "13.3 run history": [
        f"{DOMAIN}::test_the_happy_path_is_recorded",
        f"{API}::test_rejecting_and_cancelling_are_recorded",
    ],
    "13.3 clarification flow": [f"{API}::test_blocking_gaps_wait_for_answers_then_resume"],
    "13.3 acceptance through the versioning workflow": [
        f"{API}::test_accepting_creates_an_architecture_from_exactly_the_candidate",
        f"{API}::test_an_iteration_on_a_current_base_is_a_new_revision",
    ],
    "13.3 stale architecture revision conflict": [
        f"{API}::test_an_iteration_needs_its_base_to_still_be_current"
    ],
    "13.3 failed run behavior": [f"{API}::test_without_a_model_a_run_fails_llm_unavailable"],
    "13.3 cancellation": [f"{API}::test_rejecting_and_cancelling_are_recorded"],
    # 13.4 Security tests.
    "13.4 cross-tenant run access": [f"{API}::test_runs_and_sets_of_other_projects_are_not_found", TENANTS],
    "13.4 cross-project candidate access": [f"{API}::test_runs_and_sets_of_other_projects_are_not_found"],
    "13.4 unauthorized acceptance/rejection": [f"{API}::test_a_viewer_reads_but_does_not_run"],
    "13.4 IDOR on run and candidate ids": [
        TENANTS,
        f"{API}::test_accepting_creates_an_architecture_from_exactly_the_candidate",
    ],
    "13.4 prompt injection in user input": [
        f"{SAFETY}::test_injected_instructions_stay_data",
        f"{AGENT}::test_sections_cannot_escape_their_delimiters",
    ],
    "13.4 prompt injection in retrieved documents": [
        f"{SAFETY}::test_injected_instructions_stay_data",
        f"{AGENT}::test_urls_addresses_and_credentials_are_refused",
    ],
    "13.4 attempts to invoke unapproved tools": [
        f"{HERE}::test_the_model_has_no_tools_and_the_agent_runs_nothing",
        f"{PROVIDER}::test_a_structured_answer",
    ],
    "13.4 attempts to broaden retrieval scope": [
        f"{SAFETY}::test_only_the_runs_project_knowledge_reaches_the_model",
        f"{S}.test_mass_assignment_sweep::test_every_body_rejects_undeclared_privileged_fields",
    ],
    "13.4 oversized requests": [
        f"{API}::test_oversized_requests_are_refused_before_anything_runs",
        f"{AGENT}::test_an_oversized_context_is_never_sent",
    ],
    "13.4 sensitive content leakage in logs/errors": [
        f"{SAFETY}::test_the_prompt_and_retrieved_text_are_never_returned_stored_or_logged",
        f"{SAFETY}::test_secrets_are_redacted_before_the_model_sees_them",
        f"{AGENT}::test_rejections_never_echo_the_output",
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
    ],
    "13.4 unauthorized source citation access": [
        f"{PIPELINE}::test_a_passage_nothing_retrieved_cannot_be_cited",
        f"{SAFETY}::test_only_the_runs_project_knowledge_reaches_the_model",
    ],
    # 13.5 Deterministic and regression tests.
    "13.5 identical inputs and outputs give equivalent candidates": [
        f"{PIPELINE}::test_the_same_inputs_give_the_same_candidate",
        f"{EVAL}::test_the_evaluation_is_deterministic",
    ],
    "13.5 invalid model output cannot become canonical IR": [
        f"{AGENT}::test_outputs_off_the_schema_are_malformed",
        f"{CANDIDATE}::test_invalid_proposals_are_rejected_whole",
        f"{RECORDS}::test_a_tampered_record_is_refused",
    ],
    "13.5 validation cannot be bypassed": [
        f"{PIPELINE}::test_without_validation_there_is_no_candidate",
        f"{DOMAIN}::test_a_candidate_needs_a_validation_report",
    ],
    "13.5 engine results never replaced by model values": [
        f"{SCHEMA}::test_the_agent_schema_is_valid_and_closed",
        f"{PIPELINE}::test_a_failing_analysis_engine_is_reported_not_hidden",
    ],
    "13.5 failed or cancelled runs cannot be accepted": [
        f"{DOMAIN}::test_a_failed_run_has_no_candidate_and_cannot_be_accepted",
        f"{API}::test_rejecting_and_cancelling_are_recorded",
    ],
    "13.5 acceptance respects revision concurrency": [
        f"{API}::test_an_iteration_needs_its_base_to_still_be_current"
    ],
    "13.5 missing evidence stays unknown": [f"{DOMAIN}::test_unknown_tokens_stay_unknown", REGRESSION],
    "13.5 no unrelated tenant or project data in prompts or results": [
        f"{SAFETY}::test_only_the_runs_project_knowledge_reaches_the_model"
    ],
    # 13.6 Evaluation dataset.
    "13.6 representative scenarios, size and limits documented": [
        f"{EVAL}::test_the_set_is_well_formed_and_its_size_is_stated",
        f"{HERE}::test_the_evaluation_covers_the_named_scenarios",
    ],
    "13.6 output schema validity": [REGRESSION],
    "13.6 IR structural validity": [REGRESSION],
    "13.6 requirement traceability": [REGRESSION],
    "13.6 assumption disclosure": [REGRESSION],
    "13.6 citation/source correctness": [REGRESSION],
    "13.6 unsupported-claim behavior": [REGRESSION],
    "13.6 validation integration": [REGRESSION],
    "13.6 tool-budget compliance": [
        f"{EVAL}::test_every_metric_has_a_threshold_and_ceilings_hold_at_zero",
        f"{EVAL}::test_a_wrong_expectation_is_a_miss",
    ],
}
SCENARIOS = {
    "simple-web-app", "api-relational-db", "async-processing", "object-storage", "workload-scaling",
    "missing-requirements", "conflicting-constraints", "unsupported-component", "prompt-injection",
    "insufficient-evidence",
}  # fmt: skip
FORBIDDEN = {
    "subprocess",
    "socket",
    "urllib",
    "http",
    "httpx",
    "requests",
    "sqlalchemy",
    "shutil",
    "os",
    "anthropic",
}


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_requirement_is_mapped() -> None:
    assert (
        len(ACCEPTANCE) == 62
    )  # 13 unit, 10 provider, 11 integration, 11 security, 8 regression, 9 evaluation


def test_the_evaluation_covers_the_named_scenarios() -> None:
    assert {s["id"] for s in load_scenarios()} == SCENARIOS


def _files() -> list[Path]:
    return [p for root in AGENT_CODE for p in ([root] if root.is_file() else sorted(root.rglob("*.py")))]


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_the_model_has_no_tools_and_the_agent_runs_nothing() -> None:
    """No module of the agent can run a process, open a socket, fetch, touch SQL or the filesystem, or
    reach a provider directly; the provider is sent no tools."""
    for path in _files():
        bad = [n for n in _imports(path) if n.split(".")[0] in FORBIDDEN]
        assert not bad, (path.relative_to(ROOT).as_posix(), bad)
        text = path.read_text()
        assert "eval(" not in text
        assert "exec(" not in text
    provider = (ROOT / "ai" / "llm" / "providers" / "anthropic.py").read_text()
    assert "tools=" not in provider
    assert "tool_choice" not in provider


def test_only_a_persons_acceptance_writes_an_architecture() -> None:
    for path in _files():
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text()
        if relative.endswith("agent_service.py"):
            assert text.count("self._architectures.create(") == 1
            assert text.count("self._architectures.replace(") == 1
            body = text[text.index("async def accept(") : text.index("# --- reading")]
            assert "self._architectures.create(" in body
            assert "self._architectures.replace(" in body
        else:
            assert "architecture_service" not in text, relative  # the pipeline never revises
            assert "uow.architectures" not in text, relative


def test_there_is_no_stream_fix_apply_or_auto_accept_endpoint() -> None:
    text = ROUTES.read_text()
    for verb in ("put", "patch", "delete"):
        assert f"@router.{verb}(" not in text
    for word in ("stream", "fix", "apply", "auto", "execute", "deploy", "chat"):
        assert not re.search(rf'["/][a-z-]*{word}', text), word
    assert text.count("@router.post(") == 5  # start, answers, cancel, reject, accept


def test_nothing_is_claimed_guaranteed() -> None:
    for path in (
        DOC,
        ADR,
        DOCS / "api" / "architecture-agent.md",
        DOCS / "frontend" / "architecture-agent-contract.md",
        ROOT / "ai" / "evaluation" / "datasets" / "architecture_agent" / "v1" / "README.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(r"[^.]*\b(?:guarantee[sd]?|proves?|certif\w*)\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Section 14's topics."""
    text = DOC.read_text()
    for heading in (
        "## Agent architecture and boundaries",
        "## Run lifecycle and state transitions",
        "## Prompt versions",
        "## Provider configuration",
        "## Supported model capabilities",
        "## Context assembly and retrieval",
        "## Evidence and provenance semantics",
        "## Candidate IR construction",
        "## Deterministic validation and analysis integration",
        "## Tool allowlist and execution limits",
        "## API contracts",
        "## Authorization and tenant isolation",
        "## Usage and cost controls",
        "## Failure and retry behavior",
        "## Testing and evaluation instructions",
        "## Known limitations and future work",
    ):
        assert heading in text, heading
    environment = (ROOT / ".env.example").read_text()
    for variable in (
        "ARCHITECTURE_AGENT_LLM_PROVIDER",
        "ARCHITECTURE_AGENT_LLM_MODEL",
        "ARCHITECTURE_AGENT_LLM_TIMEOUT_SECONDS",
    ):
        assert variable in environment
        assert variable in text
    assert re.search(r"^#\s*ANTHROPIC_API_KEY=\s", environment, re.M)  # commented out: never a real value
    assert "## Decision" in ADR.read_text()
    assert "not integrated yet" in (DOCS / "frontend" / "architecture-agent-contract.md").read_text()
