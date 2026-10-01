"""Migration Planning Engine: each phase's acceptance criteria and the quality requirements mapped to
the tests that prove them, plus structural guarantees — the domain and the engine free of storage,
network and execution, no execution endpoint, and the documentation covering every topic and example
without claiming a guarantee. Fails if a mapped test is renamed or removed, or a criterion is
unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

U = "tests.unit.migration"
DOMAIN = f"{U}.test_migration_domain"
CHANGES = f"{U}.test_migration_changes"
PATTERNS = f"{U}.test_migration_patterns"
STEPS = f"{U}.test_migration_steps"
SEQUENCING = f"{U}.test_migration_sequencing"
DATA = f"{U}.test_migration_data_downtime"
RISK = f"{U}.test_migration_risk_rollback"
VERSIONING = f"{U}.test_migration_versioning"
EVIDENCE = f"{U}.test_migration_evidence"
FIXTURES = f"{U}.test_migration_fixtures"
API = "tests.integration.api.test_migration_plans"
MIGRATIONS = "tests.integration.database.test_migrations"
S = "tests.security"
HERE = f"{S}.test_traceability_migration_planning"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "migration-planning-engine.md"
PACKAGES = ("core/domain/migrations", "engines/migration")
ROUTES = ROOT / "apps" / "api" / "routes" / "migration_plans.py"

ACCEPTANCE: dict[str, list[str]] = {
    # Phase 1: domain contract.
    "1.1 typed, validated migration models": [
        f"{DOMAIN}::test_invalid_requests_name_the_field",
        f"{DOMAIN}::test_a_proposal_is_internally_consistent_deterministic_and_unscored",
    ],
    "1.2 exact source and target references": [
        f"{DOMAIN}::test_a_request_names_exact_revisions_and_no_topology",
        f"{DOMAIN}::test_the_target_is_a_later_revision_or_a_candidate_on_the_source",
    ],
    "1.3 stable plan and step ids": [
        f"{DOMAIN}::test_steps_have_stable_ids_traces_and_explicit_unknowns",
        f"{FIXTURES}::test_every_fixture_is_deterministic",
    ],
    "1.4 explicit plan lifecycle status": [
        f"{DOMAIN}::test_the_status_follows_the_findings",
        f"{DOMAIN}::test_the_review_lifecycle_is_made_by_people_and_recorded",
    ],
    "1.5 explicit proposal-versus-execution distinction": [
        f"{DOMAIN}::test_there_is_no_execution_status",
        f"{STEPS}::test_nothing_records_an_execution",
    ],
    "1.6 unknown and unsupported states preserved": [
        f"{DOMAIN}::test_steps_have_stable_ids_traces_and_explicit_unknowns",
        f"{STEPS}::test_unsupported_and_ambiguous_changes_are_findings_not_steps",
    ],
    "1.7 no duplicate architecture representation": [
        f"{DOMAIN}::test_a_request_names_exact_revisions_and_no_topology",
        f"{CHANGES}::test_the_ir_diff_is_reused_as_it_is",
    ],
    # Phase 2: diff and change analysis.
    "2.1 the existing architecture diff is reused": [f"{CHANGES}::test_the_ir_diff_is_reused_as_it_is"],
    "2.2 source and target revisions are preserved": [
        f"{CHANGES}::test_the_contents_must_be_the_referenced_revisions",
        f"{CHANGES}::test_a_candidate_target_is_its_overlay_on_the_source_revision",
    ],
    "2.3 migration-relevant changes are identifiable": [
        f"{CHANGES}::test_changes_are_classified_by_what_they_touch_and_which_engines_read_it",
        f"{CHANGES}::test_metadata_only_changes_are_recorded_but_need_no_step",
    ],
    "2.4 unsupported or ambiguous changes remain explicit": [
        f"{CHANGES}::test_what_is_not_modeled_needs_interpretation_and_a_role_change_is_unsupported",
        f"{CHANGES}::test_unrecognized_settings_and_unknown_values_are_for_a_person",
    ],
    "2.5 no duplicate graph comparison": [f"{CHANGES}::test_the_ir_diff_is_reused_as_it_is"],
    "2.6 no architecture mutation during diff analysis": [
        f"{CHANGES}::test_analysis_is_deterministic_and_changes_neither_architecture"
    ],
    # Phase 3: patterns.
    "3.1 patterns are deterministic and versioned": [
        f"{PATTERNS}::test_every_pattern_is_versioned_and_declares_its_contract",
        f"{PATTERNS}::test_alternatives_are_deterministic_and_unscored",
    ],
    "3.2 each step records its originating pattern": [
        f"{STEPS}::test_steps_are_traced_deterministic_and_never_commands"
    ],
    "3.3 unsupported strategies are not fabricated": [
        f"{PATTERNS}::test_a_preferred_strategy_that_is_not_supported_is_answered",
        f"{STEPS}::test_an_unsupported_preference_falls_back_to_in_place_with_a_finding",
    ],
    "3.4 pattern generation is independently testable": [
        f"{PATTERNS}::test_rolling_needs_declared_replicas_and_a_health_check",
        f"{PATTERNS}::test_blue_green_needs_a_routing_component_in_front",
        f"{PATTERNS}::test_replication_cutover_needs_a_declared_replication_mode",
    ],
    "3.5 strategy trade-offs are explicit": [
        f"{FIXTURES}::test_fixture_12_several_supported_strategies_are_shown_side_by_side"
    ],
    # Phase 4: steps.
    "4.1 steps are structured and traceable": [
        f"{STEPS}::test_steps_are_traced_deterministic_and_never_commands"
    ],
    "4.2 step ids and normalized ordering are deterministic": [
        f"{STEPS}::test_steps_are_traced_deterministic_and_never_commands",
        f"{FIXTURES}::test_every_fixture_is_deterministic",
    ],
    "4.3 preconditions and expected outcomes are represented": [
        f"{STEPS}::test_a_configuration_change_is_applied_then_verified_in_place",
        f"{FIXTURES}::test_fixture_01_vertical_scaling_states_its_explicit_configurations",
    ],
    "4.4 unsupported changes are surfaced": [
        f"{STEPS}::test_unsupported_and_ambiguous_changes_are_findings_not_steps"
    ],
    "4.5 no deployment or data migration is executed": [
        f"{STEPS}::test_nothing_records_an_execution",
        f"{HERE}::test_there_is_no_execution_endpoint",
    ],
    # Phase 5: dependencies.
    "5.1 dependency references are valid": [
        f"{SEQUENCING}::test_a_dependency_on_a_missing_step_is_reported_and_nothing_is_ordered"
    ],
    "5.2 cycles are detected": [
        f"{SEQUENCING}::test_a_cycle_is_reported_with_every_step_in_it",
        f"{FIXTURES}::test_fixture_09_a_dependency_cycle_is_reported_and_blocks_review",
    ],
    "5.3 step ordering is deterministic": [
        f"{SEQUENCING}::test_the_order_is_deterministic_with_ties_broken_by_key"
    ],
    "5.4 invalid plans receive actionable findings": [
        f"{SEQUENCING}::test_a_cycle_keeps_the_plan_from_review",
        f"{SEQUENCING}::test_cutovers_and_decommissioning_need_a_verification_before_them",
    ],
    "5.5 parallel execution is never assumed": [
        f"{SEQUENCING}::test_steps_are_grouped_only_when_each_is_explicitly_parallelizable",
        f"{SEQUENCING}::test_a_sequence_must_cover_each_step_once_and_respect_explicit_parallelism",
    ],
    # Phase 6: data, downtime, compatibility.
    "6.1 data migration requirements are represented": [
        f"{DATA}::test_an_offline_move_states_its_data_requirements_and_what_is_missing",
        f"{DATA}::test_a_replicated_move_states_its_replication_and_where_data_could_be_lost",
    ],
    "6.2 downtime assumptions are explicit": [
        f"{DATA}::test_downtime_is_held_against_the_stated_constraints",
        f"{DATA}::test_an_in_place_change_to_a_single_replica_may_cause_downtime",
    ],
    "6.3 compatibility checks are traceable": [
        f"{DATA}::test_a_database_replacement_raises_every_compatibility_question"
    ],
    "6.4 unknown values remain unknown": [
        f"{DATA}::test_declared_replicas_without_a_rolling_strategy_leave_downtime_unknown",
        f"{FIXTURES}::test_fixture_03_missing_data_volume_is_unevaluable_never_invented",
    ],
    "6.5 no fabricated duration or zero-downtime claims": [
        f"{DATA}::test_no_duration_volume_or_zero_downtime_is_ever_stated"
    ],
    # Phase 7: risk, checkpoints, rollback.
    "7.1 risks reference evidence or explicit assumptions": [
        f"{RISK}::test_every_risk_rests_on_evidence_and_has_no_score"
    ],
    "7.2 checkpoints have clear status semantics": [
        f"{RISK}::test_engine_checkpoints_are_not_run_until_an_analysis_is_attached",
        f"{RISK}::test_data_checkpoints_are_for_a_person_or_the_running_system",
        f"{RISK}::test_a_pass_needs_current_modeled_evidence",
    ],
    "7.3 rollback limitations are represented": [
        f"{RISK}::test_switching_back_after_a_data_cutover_states_the_divergence",
        f"{FIXTURES}::test_fixture_08_unknown_rollback_behaviour_is_a_stated_limitation",
    ],
    "7.4 no fabricated risk scores or guarantees": [
        f"{RISK}::test_every_risk_rests_on_evidence_and_has_no_score",
        f"{HERE}::test_nothing_is_claimed_guaranteed",
    ],
    "7.5 no runtime success is inferred from a plan": [
        f"{RISK}::test_no_checkpoint_is_passed_at_planning_time",
        f"{RISK}::test_health_checks_are_observed_only_at_runtime",
    ],
    # Phase 8: versioning and review.
    "8.1 plan versions are distinguishable": [
        f"{VERSIONING}::test_regeneration_appends_a_version_and_supersedes_the_previous_one",
        f"{API}::test_an_exact_version_is_reviewed_by_authorized_people",
    ],
    "8.2 source and target revision changes are detected": [
        f"{VERSIONING}::test_a_version_is_stale_when_what_it_was_planned_from_changed",
        f"{API}::test_a_new_revision_makes_a_plan_stale_and_unreviewable",
    ],
    "8.3 approval references an exact plan version": [
        f"{VERSIONING}::test_approval_must_name_the_exact_version",
        f"{VERSIONING}::test_a_verdict_cannot_bypass_review_or_the_exact_version",
    ],
    "8.4 review history is preserved": [
        f"{VERSIONING}::test_a_rejected_version_keeps_its_feedback_when_revised",
        f"{FIXTURES}::test_fixture_15_a_rejected_then_revised_plan_keeps_its_prior_version",
    ],
    "8.5 no automatic approval or execution": [
        f"{VERSIONING}::test_reading_a_version_changes_nothing",
        f"{FIXTURES}::test_no_fixture_fabricates_estimates_or_executes",
    ],
    # Phase 9: engine integration.
    "9.1 existing engine contracts are reused": [
        f"{EVIDENCE}::test_analyses_are_matched_to_the_exact_source_and_target",
        f"{API}::test_stored_analyses_of_the_target_become_evaluated_checkpoints",
    ],
    "9.2 every imported result references its source analysis": [
        f"{EVIDENCE}::test_the_target_validation_checkpoint_follows_its_run",
        f"{EVIDENCE}::test_capacity_prerequisites_come_from_the_target_analysis",
    ],
    "9.3 stale evidence is identified": [
        f"{EVIDENCE}::test_only_stale_evidence_cannot_evaluate",
        f"{EVIDENCE}::test_cited_evidence_drives_staleness_and_integration_is_deterministic",
    ],
    "9.4 no duplicated calculations or incompatible combinations": [
        f"{EVIDENCE}::test_without_analyses_nothing_is_invented",
        f"{EVIDENCE}::test_a_cost_increase_is_stated_only_under_the_same_pricing",
    ],
    "9.5 unsupported analysis dimensions remain explicit": [
        f"{EVIDENCE}::test_dimensions_without_current_target_evidence_are_stated_not_blocking",
        f"{EVIDENCE}::test_a_candidate_target_cites_the_candidates_evidence_and_no_engine_analysis_of_it",
    ],
    # Phase 10: API, persistence, authorization.
    "10.1 every operation enforces authorization": [
        f"{API}::test_access_isolation_and_listing",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "10.2 API contracts are typed and documented": [
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
    ],
    "10.3 persistence is transactional and append-only where required": [
        f"{API}::test_a_versions_content_never_changes_and_is_never_deleted",
        f"{API}::test_an_engine_failure_exposes_nothing_and_stores_nothing",
        f"{MIGRATIONS}::test_downgrading_migration_plans_leaves_evolution_intact",
    ],
    "10.4 existing contracts remain compatible or are deliberately updated": [
        f"{S}.test_audit_sweep::test_every_mutating_project_endpoint_is_classified",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    "10.5 plan generation never mutates the canonical architecture": [
        f"{API}::test_a_plan_is_generated_from_exact_revisions_stored_and_read_back",
        f"{FIXTURES}::test_fixture_14_no_plan_mutates_the_source_or_target",
    ],
    "10.6 no migration execution capability is introduced": [f"{HERE}::test_there_is_no_execution_endpoint"],
    # Phase 11: quality requirements.
    "11.1 the fifteen fixtures": [
        f"{FIXTURES}::test_fixture_01_vertical_scaling_states_its_explicit_configurations",
        f"{FIXTURES}::test_fixture_15_a_rejected_then_revised_plan_keeps_its_prior_version",
    ],
    "11.2 no unbounded graph traversal or uncontrolled plan generation": [
        f"{SEQUENCING}::test_a_long_chain_is_ordered_without_recursion",
        f"{FIXTURES}::test_a_transition_too_large_for_one_plan_is_refused_not_truncated",
    ],
    "11.3 stored plans survive persistence unchanged": [f"{FIXTURES}::test_every_fixture_survives_storage"],
    "11.4 the domain and the engine reach no storage, network or execution": [
        f"{HERE}::test_the_domain_and_engine_reach_no_storage_network_or_execution"
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
    assert len(ACCEPTANCE) == 58  # 54 acceptance criteria (phases 1-10) and 4 quality requirements


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


def test_the_domain_and_engine_reach_no_storage_network_or_execution() -> None:
    forbidden = (
        "persistence", "apps", "sqlalchemy", "httpx", "requests", "socket", "urllib", "subprocess",
        "random", "boto3", "kubernetes",
    )  # fmt: skip
    builtins = {"eval", "exec", "compile", "__import__"}
    for package in PACKAGES:
        for path, tree in _python(package):
            for name in _imports(tree):
                assert name.split(".")[0] not in forbidden, (path, name)
            calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
            assert not [f.id for f in calls if isinstance(f, ast.Name) and f.id in builtins], path


def test_there_is_no_execution_endpoint() -> None:
    text = ROUTES.read_text()
    assert "@router.post(" in text
    for verb in ("execute", "run", "apply", "deploy", "rollout", "rollback", "cutover", "start"):
        assert not re.search(rf'["/]{verb}\b', text), verb  # no path segment names an execution


def test_nothing_is_claimed_guaranteed() -> None:
    assert "A migration plan is a proposal for engineering review and authorization." in DOC.read_text()
    for path in (
        DOC,
        DOCS / "api" / "migration-plans.md",
        DOCS / "adr" / "ADR-020-deterministic-migration-planning.md",
        DOCS / "frontend" / "migration-contract.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(r"[^.]*\b(?:guarantee[sd]?|proves?|certif\w*)\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Phase 12's eighteen topics and eight examples, the audit and the final review."""
    text = DOC.read_text()
    for heading in (
        "## Purpose and scope",
        "## Source and target revisions",
        "## Architecture diff integration",
        "## Supported migration patterns",
        "## Migration step contract",
        "## Dependency graph semantics",
        "## Data migration planning",
        "## Downtime and compatibility semantics",
        "## Risk and assumption representation",
        "## Verification checkpoint behavior",
        "## Rollback and recovery modeling",
        "## Plan versioning and review lifecycle",
        "## Engine integration contracts",
        "## API contracts",
        "## Example migration plan",
        "## Adding a deterministic migration pattern",
        "## Tests",
        "## Known limitations",
        "## Example: missing information prevents a confident plan",
        "## Example: a dependency cycle is rejected",
        "## Example: several strategies with their trade-offs",
        "## Example: a rollback limitation",
        "## Example: a plan requiring manual verification",
        "## Example: a revision change makes a plan stale",
        "## Example: the canonical architecture is unchanged",
        "## Repository audit",
        "## Final review",
    ):
        assert heading in text, heading
    assert (DOCS / "frontend" / "migration-contract.md").exists()
    assert "## Decision" in (DOCS / "adr" / "ADR-020-deterministic-migration-planning.md").read_text()
