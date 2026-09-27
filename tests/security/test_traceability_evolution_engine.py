"""Milestone 13 (architecture evolution engine): each acceptance criterion of phases 0-11 mapped to the
tests that prove it, plus structural guarantees (one architecture model, no dynamic code, no score,
rank or winner, no formula of its own, documentation of every topic with the required examples, and
candidates stated as proposals requiring review). Fails if a mapped test is renamed or removed, or a
criterion is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

U = "tests.unit.evolution"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
DOMAIN = f"{U}.test_evolution_domain"
RULES = f"{U}.test_evolution_rules"
TRIGGERS = f"{U}.test_evolution_triggers"
OVERLAYS = f"{U}.test_evolution_overlays"
VALIDATION = f"{U}.test_evolution_validation"
IMPACT = f"{U}.test_evolution_impact"
TRADEOFFS = f"{U}.test_evolution_tradeoffs"
DECISIONS = f"{U}.test_evolution_decisions"
SERVICE = f"{U}.test_evolution_service"
FIXTURES = f"{U}.test_evolution_fixtures"
API = f"{I}.api.test_evolution"
MIGRATIONS = f"{I}.database.test_migrations"
HERE = f"{S}.test_traceability_evolution_engine"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "evolution-engine.md"
PACKAGES = ("engines/evolution", "core/domain/evolution", "core/domain/decisions")

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 0: repository audit.
    "existing evolution files and their status were inspected": [
        f"{HERE}::test_the_audit_and_review_are_recorded"
    ],
    "existing engines, overlays and validation are reused, not duplicated": [
        f"{OVERLAYS}::test_the_overlay_is_a_simulation_scenario_for_its_impact",
        f"{VALIDATION}::test_the_baseline_result_is_reused_when_given",
        f"{HERE}::test_no_formula_of_its_own",
    ],
    "no competing architecture model is created": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{HERE}::test_the_engine_reads_the_ir_and_defines_no_graph",
    ],
    # Phase 1: evolution domain contract.
    "typed, validated models": [
        f"{DOMAIN}::test_goals_are_typed_with_explicit_targets_and_units",
        f"{DOMAIN}::test_invalid_goals_are_refused_with_the_field",
        f"{DOMAIN}::test_malformed_candidates_are_refused",
        f"{DOMAIN}::test_the_request_is_canonical_and_bounded",
    ],
    "stable candidate ids and deterministic ordering": [
        f"{DOMAIN}::test_a_candidate_is_a_proposal_with_a_stable_id",
        f"{DOMAIN}::test_results_are_ordered_counted_and_fingerprinted_without_a_winner",
        f"{FIXTURES}::test_identical_inputs_give_identical_results_whatever_the_order",
    ],
    "explicit proposal-versus-applied status": [
        f"{DOMAIN}::test_a_candidate_is_a_proposal_with_a_stable_id",
        f"{DECISIONS}::test_a_person_accepts_one_option_and_nothing_else_changes",
    ],
    "unknown and unsupported states preserved": [
        f"{DOMAIN}::test_stale_or_missing_evidence_never_supports_a_candidate",
        f"{DOMAIN}::test_the_status_follows_what_was_established",
        f"{IMPACT}::test_without_a_workload_capacity_stays_unknown",
    ],
    "no arbitrary overall ranking or numeric score": [
        f"{DOMAIN}::test_results_are_ordered_counted_and_fingerprinted_without_a_winner",
        f"{FIXTURES}::test_fixture_10_alternatives_with_distinct_tradeoffs_and_no_winner",
        f"{HERE}::test_no_score_rank_or_winner_in_the_results",
    ],
    # Phase 2: rule and candidate generation contract.
    "rules are deterministic and versioned": [
        f"{RULES}::test_every_rule_is_versioned_and_declares_its_contract",
        f"{RULES}::test_generation_is_deterministic_and_never_modifies_the_baseline",
    ],
    "each candidate records the rule that generated it": [
        f"{RULES}::test_fixture_1_a_capacity_scaling_option_becomes_a_resource_candidate",
        f"{DOMAIN}::test_effects_state_their_basis_and_candidates_round_trip",
    ],
    "candidate generation is testable independently": [
        f"{RULES}::test_a_rule_that_breaks_its_declaration_is_caught",
        f"{RULES}::test_one_proposal_serving_two_goals_is_one_candidate",
        f"{RULES}::test_constraints_exclude_candidates_visibly",
    ],
    "unsupported transformations are not fabricated": [
        f"{RULES}::test_no_structural_change_is_ever_proposed",
        f"{RULES}::test_no_mechanism_or_kind_is_invented",
        f"{RULES}::test_a_rule_may_only_change_properties_the_ir_defines",
        f"{FIXTURES}::test_fixture_2_a_capacity_finding_with_missing_inputs_proposes_nothing",
    ],
    "candidate output does not mutate the Architecture IR": [
        f"{RULES}::test_generation_is_deterministic_and_never_modifies_the_baseline",
        f"{FIXTURES}::test_fixture_12_a_candidate_proposal_leaves_the_baseline_unchanged",
    ],
    # Phase 3: evidence-driven triggers.
    "every trigger references its source evidence": [
        f"{TRIGGERS}::test_fixture_1_a_modeled_bottleneck_becomes_a_trigger_and_a_candidate",
        f"{TRIGGERS}::test_fixture_5_a_security_finding_linked_to_a_supported_control",
    ],
    "stale or incompatible evidence is identified": [
        f"{TRIGGERS}::test_fixture_11_stale_evidence_against_a_newer_revision_is_never_used",
        f"{TRIGGERS}::test_capacity_evidence_of_another_workload_does_not_describe_the_goal",
        f"{FIXTURES}::test_fixture_11_stale_evidence_against_a_newer_revision",
        f"{API}::test_fixture_11_stale_evidence_after_a_new_revision",
    ],
    "trigger evaluation is deterministic": [
        f"{TRIGGERS}::test_trigger_evaluation_is_deterministic",
        f"{FIXTURES}::test_identical_inputs_give_identical_results_whatever_the_order",
    ],
    "no unsupported urgency or impact claim": [
        f"{TRIGGERS}::test_missing_evidence_is_requested_not_invented",
        f"{TRIGGERS}::test_fixture_7_a_requirement_no_engine_evaluates_is_unsupported",
        f"{HERE}::test_no_score_rank_or_winner_in_the_results",
    ],
    # Phase 4: candidate overlays.
    "the baseline architecture remains unchanged": [
        f"{OVERLAYS}::test_fixture_12_a_candidate_leaves_the_baseline_unchanged",
        f"{FIXTURES}::test_fixture_12_a_candidate_proposal_leaves_the_baseline_unchanged",
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
    ],
    "the candidate overlay is deterministic and reproducible": [
        f"{OVERLAYS}::test_the_overlay_is_deterministic_serializable_and_reconstructible",
        f"{OVERLAYS}::test_every_proposed_value_carries_the_candidates_provenance",
    ],
    "invalid transformations are rejected with actionable errors": [
        f"{OVERLAYS}::test_fixtures_8_and_9_invalid_transformations_are_refused_with_what_to_act_on",
        f"{OVERLAYS}::test_a_change_that_breaks_an_ir_rule_is_refused",
        f"{OVERLAYS}::test_a_candidate_applies_only_to_its_own_baseline",
    ],
    "candidate changes are structurally diffable": [f"{OVERLAYS}::test_the_overlay_is_structurally_diffable"],
    # Phase 5: candidate validation.
    "validation findings reference exact candidate elements": [
        f"{VALIDATION}::test_a_candidate_that_introduces_a_blocking_finding_is_invalid",
        f"{FIXTURES}::test_fixtures_8_and_9_invalid_candidates_are_never_presented_as_valid",
    ],
    "invalid candidates are not silently presented as valid": [
        f"{VALIDATION}::test_fixtures_8_and_9_refused_overlays",
        f"{VALIDATION}::test_a_candidate_against_another_baseline_is_invalid",
        f"{DECISIONS}::test_an_option_validation_refused_cannot_be_accepted",
    ],
    "unknown checks remain visible": [
        f"{VALIDATION}::test_requirement_verdicts_and_rule_failures_decide_the_state"
    ],
    "existing Validation Engine rules are reused": [
        f"{VALIDATION}::test_a_candidate_that_resolves_a_policy_violation_is_valid_under_the_modeled_constraints",
        f"{VALIDATION}::test_the_baseline_result_is_reused_when_given",
    ],
    # Phase 6: impact analysis.
    "each impact links to its source engine and analysis": [
        f"{IMPACT}::test_a_scaling_candidate_is_evaluated_by_the_simulation_engine",
        f"{IMPACT}::test_a_security_control_is_evaluated_by_the_security_engine_on_both_sides",
        f"{IMPACT}::test_an_instrumentation_candidate_is_evaluated_by_the_observability_engine",
    ],
    "candidate and baseline use compatible assumptions": [
        f"{IMPACT}::test_a_cost_impact_uses_the_current_pricing_snapshot",
        f"{IMPACT}::test_impacts_are_deterministic_the_baseline_unchanged_and_computed_once",
    ],
    "unknown dimensions remain unknown": [
        f"{IMPACT}::test_without_a_workload_capacity_stays_unknown",
        f"{IMPACT}::test_an_invalid_candidate_is_not_evaluated",
    ],
    "no duplicated engine calculations": [
        f"{IMPACT}::test_impacts_are_deterministic_the_baseline_unchanged_and_computed_once",
        f"{HERE}::test_no_formula_of_its_own",
    ],
    # Phase 7: trade-offs.
    "every benefit or drawback has evidence or is labeled a consideration": [
        f"{TRADEOFFS}::test_modeled_rows_come_from_the_engines_evidence",
        f"{TRADEOFFS}::test_rule_rows_and_considerations_are_labelled",
        f"{TRADEOFFS}::test_consequences_keep_their_basis_honest",
    ],
    "no unsupported candidate ranking": [
        f"{TRADEOFFS}::test_fixture_10_alternatives_are_side_by_side_with_no_winner",
        f"{HERE}::test_no_score_rank_or_winner_in_the_results",
    ],
    "trade-offs are visible and comparable without a single verdict": [
        f"{TRADEOFFS}::test_a_cost_increase_is_a_drawback_checked_against_the_ceiling",
        f"{TRADEOFFS}::test_security_and_observability_rows_follow_the_findings",
        f"{TRADEOFFS}::test_the_table_is_deterministic_and_round_trips",
    ],
    # Phase 8: decisions and ADRs.
    "the human decision remains explicit": [
        f"{DECISIONS}::test_a_person_accepts_one_option_and_nothing_else_changes",
        f"{DECISIONS}::test_the_lifecycle_is_explicit",
    ],
    "ADR references are stable": [f"{DECISIONS}::test_the_adr_document_and_references_are_stable"],
    "candidate proposal and accepted decision are distinct states": [
        f"{DECISIONS}::test_a_proposed_adr_is_drafted_from_an_analysis_without_choosing",
        f"{DECISIONS}::test_options_can_be_chosen_and_must_exist",
    ],
    "architecture changes require a separate authorized workflow": [
        f"{DECISIONS}::test_a_resulting_revision_is_linked_only_by_a_person_after_the_change",
        f"{SERVICE}::test_a_decision_is_drafted_decided_and_linked_by_people",
        f"{API}::test_decisions_are_drafted_and_decided_by_people",
    ],
    # Phase 9: API, persistence and authorization.
    "every operation enforces authorization": [
        f"{SERVICE}::test_access_and_archived_architectures",
        f"{API}::test_access_and_listing",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
    ],
    "API contracts are typed and documented": [
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{API}::test_the_catalog",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
    ],
    "persistence is transactional where required": [
        f"{SERVICE}::test_an_analysis_is_stored_with_its_evidence_and_audited",
        f"{SERVICE}::test_invalid_requests_store_nothing",
        f"{API}::test_analyses_are_append_only",
        f"{MIGRATIONS}::test_downgrading_evolution_leaves_simulations_intact",
        f"{S}.test_audit_sweep::test_every_project_scoped_audit_action_is_exercised",
    ],
    "existing frontend and API contracts remain compatible": [
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{HERE}::test_the_frontend_contract_is_documented",
    ],
    "candidate generation never mutates the canonical architecture": [
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
        f"{FIXTURES}::test_fixture_12_a_candidate_proposal_leaves_the_baseline_unchanged",
    ],
    # Phase 10: testing and quality.
    "the twelve candidate fixtures": [
        f"{FIXTURES}::test_fixture_1_a_capacity_bottleneck_with_sufficient_evidence",
        f"{FIXTURES}::test_fixture_2_a_capacity_finding_with_missing_inputs_proposes_nothing",
        f"{FIXTURES}::test_fixture_3_a_cost_constraint_with_valid_pricing_evidence",
        f"{FIXTURES}::test_fixture_4_a_reliability_finding_with_explicit_redundancy_semantics",
        f"{FIXTURES}::test_fixture_5_a_security_finding_linked_to_a_supported_control",
        f"{FIXTURES}::test_fixture_6_an_observability_requirement_with_a_modeled_coverage_gap",
        f"{FIXTURES}::test_fixture_7_a_new_requirement_the_architecture_cannot_evaluate",
        f"{FIXTURES}::test_fixtures_8_and_9_invalid_candidates_are_never_presented_as_valid",
        f"{FIXTURES}::test_fixture_10_alternatives_with_distinct_tradeoffs_and_no_winner",
        f"{FIXTURES}::test_fixture_11_stale_evidence_against_a_newer_revision",
        f"{FIXTURES}::test_fixture_12_a_candidate_proposal_leaves_the_baseline_unchanged",
    ],
    "integration: stored analyses, tenant isolation, invalid requests, safe errors": [
        f"{API}::test_an_analysis_is_run_stored_and_read_back",
        f"{API}::test_access_and_listing",
        f"{API}::test_invalid_requests_are_refused_and_store_nothing",
        f"{API}::test_an_engine_failure_is_stored_as_failed",
        f"{SERVICE}::test_an_engine_failure_is_a_failed_analysis_without_internals",
    ],
    "determinism: ids, ordering, overlays, diffs, triggers, impact references": [
        f"{FIXTURES}::test_identical_inputs_give_identical_results_whatever_the_order",
        f"{SERVICE}::test_the_same_inputs_give_the_same_result",
        f"{VALIDATION}::test_validation_is_deterministic_and_round_trips",
    ],
    "regression: the IR unchanged and the other engines' contracts intact": [
        f"{FIXTURES}::test_no_other_engine_depends_on_evolution",
        f"{S}.test_traceability_simulation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_capacity_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
    ],
    "no silent exception swallowing, fabrication, opaque score or unbounded analysis": [
        f"{SERVICE}::test_an_engine_failure_is_a_failed_analysis_without_internals",
        f"{SERVICE}::test_missing_evidence_is_reported_not_invented",
        f"{FIXTURES}::test_a_large_architecture_is_analyzed_with_bounded_work",
        f"{FIXTURES}::test_the_evolution_engine_reaches_no_network_storage_or_randomness",
        f"{HERE}::test_the_engine_executes_no_dynamic_code",
    ],
    # Phase 11: documentation.
    "every documentation topic, with the five required examples": [
        f"{HERE}::test_every_documentation_topic_is_covered"
    ],
    "candidates are stated as proposals requiring review, never guarantees": [
        f"{HERE}::test_candidates_are_stated_as_proposals"
    ],
}


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_criterion_is_mapped() -> None:
    assert len(ACCEPTANCE) == 48  # 3 + 5 + 5 + 4 + 4 + 4 + 4 + 3 + 4 + 5 + 5 + 2 (phases 0-11)


def _python(package: str) -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text() or "")) for p in sorted((ROOT / package).rglob("*.py"))]


def _imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_the_engine_reads_the_ir_and_defines_no_graph() -> None:
    graph_names = {"Graph", "Node", "Edge", "Connection", "ArchitectureGraph", "Topology", "Component"}
    for package in PACKAGES:
        for path, tree in _python(package):
            classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
            assert not classes & graph_names, (path, classes & graph_names)
            assert not any(m in {"networkx", "igraph"} for m in _imports(tree)), path


def test_no_formula_of_its_own() -> None:
    """Impacts and validation call the engines' ports: no utilization, availability or price function
    is defined in the evolution code, and the impact module reaches the simulation, security and
    observability ports."""
    forbidden = {"utilization", "availability", "path_availability", "price", "monthly_cost"}
    for package in PACKAGES:
        for path, tree in _python(package):
            functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            assert not functions & forbidden, (path, functions & forbidden)
    imports = _imports(ast.parse((ROOT / "engines" / "evolution" / "impact.py").read_text()))
    for port in (
        "core.domain.simulations.ports",
        "core.domain.security.ports",
        "core.domain.observability.ports",
    ):
        assert port in imports, port


def test_the_engine_executes_no_dynamic_code() -> None:
    builtins = {"eval", "exec", "compile", "__import__"}
    for package in PACKAGES:
        for path, tree in _python(package):
            calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
            assert not [f.id for f in calls if isinstance(f, ast.Name) and f.id in builtins], path
            assert not [
                f.attr for f in calls if isinstance(f, ast.Attribute) and f.attr == "import_module"
            ], path
            assert "importlib" not in _imports(tree), path


def test_no_score_rank_or_winner_in_the_results() -> None:
    """No serialized field of an evolution result, candidate, trade-off or trigger is a score, a rank,
    a weight, a winner, an urgency or a risk level."""
    words = {"score", "rank", "ranking", "weight", "winner", "best", "optimal", "urgency", "risk_level"}
    for name in ("results.py", "candidates.py", "tradeoffs.py", "triggers.py"):
        tree = ast.parse((ROOT / "core" / "domain" / "evolution" / name).read_text())
        docstrings = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        }
        literals = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        }
        assert not literals & words, (name, literals & words)


def test_candidates_are_stated_as_proposals() -> None:
    """The documents say candidates are proposals requiring engineering review and authorization, and
    never claim a guarantee."""
    statement = "candidates are proposals requiring engineering review and authorization"
    for path in (
        DOC,
        DOCS / "api" / "evolution.md",
        DOCS / "adr" / "ADR-018-deterministic-evolution.md",
        DOCS / "frontend" / "evolution-contract.md",
    ):
        text = " ".join(path.read_text().split())
        assert statement in text.lower(), path.name
        for sentence in re.findall(r"[^.]*\b(?:proves?|guarantee[sd]?|verified)\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Phase 11's documentation list (14 topics) and the five required examples."""
    from core.domain.evolution.values import CandidateCategory, GoalType  # noqa: PLC0415
    from engines.evolution.rulebook import default_registry  # noqa: PLC0415 - the shipped rules

    text = DOC.read_text()
    for heading in (
        "## Purpose and scope",
        "## Supported goals and candidate types",
        "## Evolution rule contract and versioning",
        "## Evidence sources and compatibility",
        "## Candidate overlay semantics",
        "## Validation behavior",
        "## Impact analysis integrations",
        "## Trade-off representation",
        "## ADR and human decision lifecycle",
        "## API contracts",
        "## Example analysis and candidate diff",
        "## Adding a deterministic evolution rule",
        "## Tests",
        "## Known limitations",
        "## Example: a candidate rejected by structural validation",
        "## Example: missing evidence prevents a recommendation",
        "## Example: multiple candidates without a winner",
        "## Example: the baseline is unchanged",
        "## Execution limits",
        "## Persistence",
        "## Authorization",
        "## Determinism",
    ):
        assert heading in text, heading

    def section(title: str) -> str:
        return text.split(title, 1)[1].split("\n## ", 1)[0]

    assert "status: completed" in section("## Example analysis and candidate diff")
    assert "explicit evidence" in section("## Example analysis and candidate diff")
    assert '"diff"' in section("## Example analysis and candidate diff")
    assert "unknown_element" in section("## Example: a candidate rejected by structural validation")
    assert "insufficient_evidence" in section("## Example: missing evidence prevents a recommendation")
    assert "not a ranking" in section("## Example: multiple candidates without a winner")
    assert "content hash" in section("## Example: the baseline is unchanged")
    for rule in default_registry().rules():
        assert f"`{rule.meta.id}`" in text, rule.meta.id
    for goal in GoalType:
        assert f"`{goal.value}`" in text, goal.value
    for category in CandidateCategory:
        assert f"`{category.value}`" in text, category.value


def test_the_frontend_contract_is_documented() -> None:
    text = (DOCS / "frontend" / "evolution-contract.md").read_text()
    for field in ("dailyActiveUsers", "monthlyCost", "risk", "stages", "sourceFindingId", "alternatives"):
        assert field in text, field


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known limitations"):
        assert heading in text, heading
    assert "Reused" in text
    assert "Still empty placeholders" in text
