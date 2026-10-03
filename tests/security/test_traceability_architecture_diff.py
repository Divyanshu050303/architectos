"""AI Architecture Diff: each testing requirement (sections 24.1 to 24.7 and 25) mapped to the tests that
prove it, plus structural guarantees — the diff runs no process, network, SQL or filesystem action and
the model has no tools; the diff only reads other records and writes its own; there is no endpoint to
update, delete, apply, approve, migrate or deploy anything; the complex fixture has the components the
spec names; the documentation covers every topic without claiming a guarantee. Fails if a mapped test
is renamed or removed, or a requirement is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

from ai.evaluation.architecture_diff import load_scenarios
from core.architecture_ir.component import NodeKind
from tests.unit.architecture_diff.test_semantic_diff import platform

U = "tests.unit.architecture_diff"
SEM = f"{U}.test_semantic_diff"
DOMAIN = f"{U}.test_diff_domain"
TRACE = f"{U}.test_diff_traceability"
IMPACT = f"{U}.test_diff_impact"
CTX = f"{U}.test_explanation_context"
PARTS = f"{U}.test_diff_service_parts"
AGENT = "tests.unit.ai.test_diff_agent"
API = "tests.integration.api.test_architecture_diffs"
EVAL = "tests.evaluation.architecture.test_diff_regression"
S = "tests.security"
SAFETY = f"{S}.test_diff_safety"
TENANTS = (
    f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing"
)
HERE = f"{S}.test_traceability_architecture_diff"
REGRESSION = f"{EVAL}::test_the_diff_does_not_regress"
REFUSED = f"{AGENT}::test_unsupported_output_is_refused_and_not_retried"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "architecture-diff.md"
ADR = DOCS / "adr" / "ADR-025-grounded-architecture-diff.md"
ROUTES = ROOT / "apps" / "api" / "routes" / "architecture_diffs.py"
DIFF_CODE = (
    ROOT / "core" / "domain" / "architecture_diff",
    ROOT / "engines" / "architecture_diff",
    ROOT / "ai" / "agents" / "diff_agent.py",
)

ACCEPTANCE: dict[str, list[str]] = {
    # 24.1 Deterministic diff unit tests.
    "24.1 empty to populated, populated to empty": [f"{SEM}::test_empty_to_populated_and_back"],
    "24.1 added component": [f"{SEM}::test_an_added_and_a_removed_component"],
    "24.1 removed component": [f"{SEM}::test_an_added_and_a_removed_component"],
    "24.1 modified component": [f"{SEM}::test_a_scaling_change_is_field_aware"],
    "24.1 renamed component": [f"{SEM}::test_a_rename_is_the_same_element_modified"],
    "24.1 type change": [f"{SEM}::test_a_type_change_is_structural"],
    "24.1 configuration change": [
        f"{SEM}::test_a_scaling_change_is_field_aware",
        f"{SEM}::test_units_come_from_the_property_name",
    ],
    "24.1 nested configuration change": [f"{SEM}::test_a_nested_configuration_change"],
    "24.1 added edge": [f"{SEM}::test_added_removed_and_modified_connections"],
    "24.1 removed edge": [f"{SEM}::test_added_removed_and_modified_connections"],
    "24.1 modified edge": [f"{SEM}::test_added_removed_and_modified_connections"],
    "24.1 protocol change": [f"{SEM}::test_a_protocol_change_is_topology_not_an_outcome"],
    "24.1 scaling change": [f"{SEM}::test_a_scaling_change_is_field_aware"],
    "24.1 metadata-only change": [f"{SEM}::test_metadata_only_changes"],
    "24.1 secret/configuration-sensitive changes": [
        f"{SEM}::test_a_secret_is_reported_never_shown",
        f"{DOMAIN}::test_a_secret_never_carries_its_values",
    ],
    "24.1 stable ids": [
        f"{SEM}::test_the_same_states_always_give_the_same_diff",
        f"{DOMAIN}::test_change_ids_are_derived_from_identity",
    ],
    "24.1 ambiguous identity": [
        f"{SEM}::test_a_changed_id_is_a_removal_and_an_addition_never_matched_by_name"
    ],
    "24.1 bounded diff": [
        f"{SEM}::test_a_comparison_over_the_limit_is_refused_not_cut",
        f"{SEM}::test_a_value_too_long_to_show_is_refused_not_cut",
        f"{SEM}::test_too_many_groups_are_refused_not_cut",
    ],
    # 24.2 Complex architecture fixtures.
    "24.2 a realistic platform fixture": [
        f"{SEM}::test_a_realistic_change_set",
        f"{HERE}::test_the_complex_fixture_has_the_named_components",
    ],
    # 24.3 Engine integration tests.
    "24.3 replica count reaches capacity": [
        f"{IMPACT}::test_a_replica_change_reaches_the_capacity_engine",
        f"{API}::test_capacity_is_compared_only_on_a_named_analysis",
    ],
    "24.3 engines only where they have the models and inputs": [
        f"{IMPACT}::test_a_security_change_shows_up_as_the_engine_says",
        f"{IMPACT}::test_without_named_inputs_capacity_and_cost_are_not_evaluated",
        f"{IMPACT}::test_cost_is_priced_on_one_snapshot_for_both_states",
        f"{IMPACT}::test_a_state_that_cannot_be_priced_has_no_total",
        f"{IMPACT}::test_a_failing_engine_is_reported_never_hidden",
    ],
    # 24.4 AI tests (mocked model).
    "24.4 correct explanation": [
        f"{AGENT}::test_a_grounded_explanation_is_accepted_with_one_call",
        f"{API}::test_explanations_are_appended_and_never_change_the_diff",
    ],
    "24.4 invalid structured output": [
        f"{AGENT}::test_malformed_output_is_retried_once_then_fails",
        f"{AGENT}::test_malformed_then_valid_succeeds",
    ],
    "24.4 unsupported claim": [REFUSED, REGRESSION],
    "24.4 missing evidence": [REFUSED, f"{DOMAIN}::test_a_statement_is_grounded_or_labelled_an_inference"],
    "24.4 hallucinated citation": [REFUSED, f"{AGENT}::test_an_unknown_group_is_refused", REGRESSION],
    "24.4 prompt injection": [
        f"{AGENT}::test_instructions_in_the_data_stay_out_of_the_instructions",
        f"{SAFETY}::test_injected_instructions_stay_data",
    ],
    "24.4 contradictory deterministic findings": [
        f"{AGENT}::test_an_outcome_that_contradicts_the_engines_is_refused",
        f"{AGENT}::test_an_outcome_citing_the_engines_finding_or_labelled_an_inference_is_kept",
    ],
    "24.4 large diff": [
        f"{PARTS}::test_a_diff_too_large_for_the_budget_is_never_sent",
        f"{CTX}::test_field_details_give_way_first",
        f"{CTX}::test_a_diff_too_large_to_explain_sends_nothing",
    ],
    "24.4 empty diff": [
        f"{PARTS}::test_identical_states_are_not_explained",
        f"{API}::test_identical_states_need_no_explanation",
    ],
    "24.4 provider failure": [
        f"{AGENT}::test_a_permanent_failure_is_not_retried",
        f"{API}::test_without_a_model_an_explanation_fails_llm_unavailable",
    ],
    "24.4 timeout": [f"{AGENT}::test_two_failures_end_the_attempt", REGRESSION],
    "24.4 rate limit": [f"{AGENT}::test_transient_failures_are_retried_once"],
    # 24.5 Grounding tests.
    "24.5 known change and known evidence: the right evidence is cited": [
        f"{PARTS}::test_an_explanation_keeps_only_the_passages_it_cites",
        f"{SAFETY}::test_only_the_diffs_project_knowledge_reaches_the_model",
    ],
    "24.5 known change and absent evidence: none is fabricated": [
        f"{PARTS}::test_a_retrieval_failure_is_said_and_the_explanation_proceeds",
        f"{CTX}::test_everything_citable_is_listed_by_id",
        REFUSED,
    ],
    # 24.6 Security tests.
    "24.6 tenant isolation": [
        TENANTS,
        f"{SAFETY}::test_another_organizations_architecture_cannot_be_compared",
        f"{API}::test_diffs_of_other_projects_and_tenants_are_not_found",
    ],
    "24.6 project isolation": [
        f"{API}::test_diffs_of_other_projects_and_tenants_are_not_found",
        f"{SAFETY}::test_only_the_diffs_project_knowledge_reaches_the_model",
    ],
    "24.6 revision authorization": [f"{API}::test_missing_hidden_and_uncomparable_states_get_one_answer"],
    "24.6 candidate authorization": [
        f"{API}::test_missing_hidden_and_uncomparable_states_get_one_answer",
        f"{API}::test_an_agent_candidate_compares_with_a_revision",
    ],
    "24.6 IDOR": [TENANTS, f"{API}::test_diffs_of_other_projects_and_tenants_are_not_found"],
    "24.6 prompt injection": [f"{SAFETY}::test_injected_instructions_stay_data"],
    "24.6 sensitive-value redaction": [
        f"{SAFETY}::test_secrets_never_reach_the_model_the_response_or_the_record",
        f"{API}::test_a_secret_change_is_reported_never_stored_or_shown",
        f"{CTX}::test_a_secret_is_never_shown",
    ],
    "24.6 unauthorized diff retrieval": [
        f"{API}::test_a_viewer_reads_but_does_not_compare_or_explain",
        f"{API}::test_diffs_of_other_projects_and_tenants_are_not_found",
    ],
    "24.6 nothing leaks into prompts, records or logs": [
        f"{SAFETY}::test_the_prompt_and_retrieved_text_are_never_returned_stored_or_logged",
        f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text",
    ],
    # 24.7 Regression tests: the suites the diff must not change still pass.
    "24.7 architecture IR": [
        "tests.unit.architecture_ir.test_ir_commands::test_commands_produce_a_new_architecture_and_leave_the_old_one_alone"
    ],
    "24.7 versioning": ["tests.integration.api.test_architectures::test_create_and_read_back_exactly"],
    "24.7 validation": [
        "tests.unit.validation.test_configuration_and_policy_rules::test_the_examples_have_consistent_configuration"
    ],
    "24.7 capacity": [
        "tests.unit.capacity.test_capacity_engine::test_registration_resolution_and_duplicates"
    ],
    "24.7 cost": [
        "tests.unit.cost.test_cost_aggregation::test_multiple_components_add_up_to_the_known_total"
    ],
    "24.7 reliability": [
        "tests.unit.reliability.test_reliability_domain::test_availability_is_an_exact_fraction_kept_to_nine_places"
    ],
    "24.7 security": [
        "tests.unit.security.test_security_access::test_a_public_api_with_explicit_authentication_raises_nothing"
    ],
    "24.7 observability": [
        "tests.unit.observability.test_observability_domain::"
        "test_observability_properties_are_declared_on_the_architecture"
    ],
    "24.7 simulation": [
        "tests.unit.simulation.test_simulation_capacity::test_fixture_1_workload_increase_with_supported_inputs"
    ],
    "24.7 discovery": [
        "tests.unit.discovery.test_discovery_comparison::test_identical_inputs_are_comparable_and_identical"
    ],
    "24.7 drift detection": [
        "tests.unit.drift.test_drift_classification::test_no_difference_is_stated_only_of_the_inspected_scope"
    ],
    # 25 Evaluation dataset.
    "25 versioned pairs: scaling, migration, caching, security, no change": [
        f"{EVAL}::test_the_set_is_well_formed_and_its_size_is_stated",
        f"{HERE}::test_the_evaluation_covers_the_named_scenarios",
    ],
    "25 expected deterministic changes": [REGRESSION],
    "25 no guaranteed improvement claimed": [REGRESSION, f"{EVAL}::test_a_wrong_expectation_is_a_miss"],
    "25 identical revisions need no interpretation": [
        REGRESSION,
        f"{API}::test_identical_states_need_no_explanation",
    ],
    "25 deterministic and able to fail": [
        f"{EVAL}::test_the_evaluation_is_deterministic",
        f"{EVAL}::test_every_metric_has_a_threshold_and_ceilings_hold_at_zero",
    ],
}
SCENARIOS = {
    "scaling-replicas", "database-migration", "add-cache", "security-exposure", "no-change",
    "requirement-trace", "secret-rotation", "prompt-injection", "hallucinated-citation",
    "unsupported-claim", "model-timeout",
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
# What the diff may do with another aggregate's repository: read it. Its own: append and read.
ALLOWED_CALLS = {
    ("architectures", "get"), ("architectures", "get_revision"), ("agent_runs", "get"),
    ("requirements", "list_by_status"), ("requirements", "list_by_ids"), ("decisions", "list_for_project"),
    ("capacity", "get"), ("cost", "get"), ("pricing", "get"), ("audit", "record"),
    ("architecture_diffs", "add"), ("architecture_diffs", "get"), ("architecture_diffs", "list"),
    ("architecture_diffs", "add_explanation"), ("architecture_diffs", "list_explanations"),
}  # fmt: skip


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_requirement_is_mapped() -> None:
    sections = {criterion.split()[0] for criterion in ACCEPTANCE}
    assert sections == {"24.1", "24.2", "24.3", "24.4", "24.5", "24.6", "24.7", "25"}
    assert (
        len(ACCEPTANCE) == 60
    )  # 18 diff, 1 fixture, 2 engine, 12 AI, 2 grounding, 9 security, 11 regression, 5 eval


def test_the_evaluation_covers_the_named_scenarios() -> None:
    assert {s["id"] for s in load_scenarios()} == SCENARIOS


def test_the_complex_fixture_has_the_named_components() -> None:
    """API gateway, services, a database, a cache, a queue, object storage, a load balancer, observability."""
    kinds = [n.kind for n in platform().nodes]
    for kind in (
        NodeKind.GATEWAY,
        NodeKind.DATABASE,
        NodeKind.CACHE,
        NodeKind.QUEUE,
        NodeKind.STORAGE,
        NodeKind.LOAD_BALANCER,
        NodeKind.OBSERVABILITY,
    ):
        assert kind in kinds, kind
    assert kinds.count(NodeKind.SERVICE) >= 2


def _files() -> list[Path]:
    return [p for root in DIFF_CODE for p in ([root] if root.is_file() else sorted(root.rglob("*.py")))]


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_the_diff_runs_nothing_and_the_model_has_no_tools() -> None:
    """No module of the diff can run a process, open a socket, fetch, touch SQL or the filesystem, or
    reach a provider directly (the interpreter builds the provider through the port's adapter)."""
    for path in _files():
        bad = [n for n in _imports(path) if n.split(".")[0] in FORBIDDEN]
        assert not bad, (path.relative_to(ROOT).as_posix(), bad)
        text = path.read_text()
        assert "eval(" not in text
        assert "exec(" not in text
    provider = (ROOT / "ai" / "llm" / "providers" / "anthropic.py").read_text()
    assert "tools=" not in provider
    assert "tool_choice" not in provider


def test_the_diff_only_reads_others_and_appends_its_own() -> None:
    """No architecture, revision, agent run, analysis or decision is written: every repository call of
    the diff's code is a read of another aggregate or an append to its own."""
    calls = set()
    for path in _files():
        calls |= set(re.findall(r"uow\.(\w+)\.(\w+)\(", path.read_text()))
        assert "architecture_service" not in path.read_text(), path.name
    assert calls, "the service's repository calls were found"
    assert calls <= ALLOWED_CALLS, calls - ALLOWED_CALLS


def test_there_is_no_endpoint_to_change_apply_or_approve_anything() -> None:
    text = ROUTES.read_text()
    for verb in ("put", "patch", "delete"):
        assert f"@router.{verb}(" not in text
    for word in (
        "apply",
        "approve",
        "accept",
        "migrate",
        "deploy",
        "fix",
        "stream",
        "auto",
        "execute",
        "score",
    ):
        assert not re.search(rf'["/][a-z-]*{word}', text), word
    assert text.count("@router.post(") == 2  # compare, explain


def test_nothing_is_claimed_guaranteed() -> None:
    for path in (
        DOC,
        ADR,
        DOCS / "api" / "architecture-diffs.md",
        DOCS / "frontend" / "architecture-diff-contract.md",
        ROOT / "ai" / "evaluation" / "datasets" / "architecture_diff" / "v1" / "README.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(r"[^.]*\b(?:guarantee[sd]?|proves?|certif\w*)\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Section 27's topics, and its required statement."""
    text = DOC.read_text()
    for heading in (
        "## Architecture diff semantics",
        "## Stable identity rules",
        "## Deterministic and AI layers",
        "## Change classification",
        "## Impact analysis",
        "## Evidence and grounding",
        "## AI explanation lifecycle",
        "## API contracts",
        "## Security",
        "## Tenant isolation",
        "## Performance limits",
        "## Evaluation methodology",
        "## Known limitations",
    ):
        assert heading in text, heading
    statement = (
        "AI Architecture Diff explains architectural changes; it does not determine architectural "
        "correctness by itself."
    )
    for path in (DOC, ADR):
        assert statement in " ".join(path.read_text().replace(">", " ").split())
    environment = (ROOT / ".env.example").read_text()
    for variable in (
        "ARCHITECTURE_DIFF_LLM_PROVIDER",
        "ARCHITECTURE_DIFF_LLM_MODEL",
        "ARCHITECTURE_DIFF_LLM_TIMEOUT_SECONDS",
    ):
        assert variable in environment
        assert variable in text
    assert "## Decision" in ADR.read_text()
    assert "not integrated yet" in (DOCS / "frontend" / "architecture-diff-contract.md").read_text()
