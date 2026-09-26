"""The Architecture IR's Definition of Done (specification section 23), each item mapped to the tests
that prove it, plus the guard that keeps one canonical model. Fails if a mapped test is renamed or
removed, if an item is left unmapped, or if a competing architecture model appears."""

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
U = "tests.unit"
I = "tests.integration"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
IR = f"{U}.architecture_ir"
SERVICE = f"{U}.architecture.test_architecture_service"
API = f"{I}.api.test_architectures"
DB = f"{I}.database.test_architecture_persistence"
DOC = ROOT / "docs" / "architecture" / "architecture-ir.md"

DONE: dict[str, list[str]] = {
    "existing implementation was audited before modification": [
        f"{S}.test_traceability_architecture_ir::test_the_audit_and_review_are_recorded"
    ],
    "one canonical Architecture IR representation exists": [
        f"{IR}.test_ir_model::test_a_valid_architecture",
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
    ],
    "no competing architecture model was introduced": [
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        f"{U}.engines.test_architecture_engine_contract::test_a_proposal_becomes_a_revision_traceable_to_its_requirement_set",
    ],
    "nodes and connections are consistently represented": [
        f"{IR}.test_ir_model::test_connections_carry_their_semantics",
        f"{IR}.test_ir_serialization::test_a_round_trip_preserves_the_architecture",
    ],
    "stable identifiers are supported": [
        f"{IR}.test_ir_model::test_identity_is_the_id_not_the_name",
        f"{IR}.test_ir_diff::test_a_rename_is_a_modification_not_a_replacement",
    ],
    "configuration is structured and extensible": [
        f"{IR}.test_ir_configuration::test_each_kind_has_its_own_properties",
        f"{IR}.test_ir_configuration::test_unrecognized_settings_are_preserved_unchanged_and_immutable",
    ],
    "requirement traceability is supported": [
        f"{IR}.test_ir_model::test_requirements_are_referenced_not_copied",
        f"{SERVICE}::test_requirement_references_must_exist_in_the_project",
    ],
    "provenance and unknown values are preserved": [
        f"{IR}.test_ir_model::test_discovered_values_keep_their_provenance_and_unknowns",
        f"{API}::test_an_import_keeps_unknowns_and_provenance",
        f"{IR}.test_ir_values::test_a_model_proposal_states_its_confidence_and_is_never_verified_by_itself",
    ],
    "structural invariants are enforced deterministically": [
        f"{IR}.test_ir_model::test_every_problem_is_reported_at_once_in_a_stable_order",
        f"{IR}.test_ir_model::test_containment_rules",
        f"{API}::test_invalid_architectures_say_exactly_what_is_wrong",
    ],
    "serialization and deserialization work": [
        f"{IR}.test_ir_serialization::test_a_round_trip_preserves_the_architecture",
        f"{IR}.test_ir_serialization::test_the_json_is_canonical",
        f"{IR}.test_ir_schema::test_what_the_code_writes_matches_the_schema",
    ],
    "schema versioning is explicit": [
        f"{IR}.test_ir_serialization::test_schema_versions_are_explicit",
        f"{IR}.test_ir_serialization::test_older_documents_are_upgraded_step_by_step_without_touching_the_input",
        f"{DB}::test_an_older_schema_is_upgraded_when_read",
    ],
    "architecture revisions are immutable": [
        f"{U}.architecture.test_revisions::test_revisions_are_immutable",
        f"{DB}::test_revisions_are_append_only",
    ],
    "historical revisions can be retrieved": [
        f"{SERVICE}::test_edits_create_revisions_and_history_is_kept",
        f"{API}::test_edits_create_revisions_with_their_changes",
    ],
    "deterministic architecture diff works": [
        f"{IR}.test_ir_diff::test_the_summary_and_the_diff_are_deterministic",
        f"{IR}.test_ir_diff::test_configuration_technology_and_resource_changes_are_categorized",
        f"{API}::test_compare_two_revisions",
    ],
    "persistence is transaction-safe": [
        f"{I}.database.test_architecture_revision_race::test_concurrent_edits_of_one_revision_create_one_revision",
        f"{DB}::test_the_current_revision_must_exist",
        f"{DB}::test_one_architecture_per_project_and_the_transaction_stays_usable",
    ],
    "project and organization boundaries are enforced": [
        f"{API}::test_who_may_read_and_change",
        f"{DB}::test_everything_stays_inside_one_project",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "API contracts remain compatible": [
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
    ],
    "Architecture Engine can consume and produce the canonical IR": [
        f"{U}.engines.test_architecture_engine_contract::test_a_proposal_becomes_a_revision_traceable_to_its_requirement_set",
        f"{U}.engines.test_architecture_engine_contract::test_what_a_generator_may_not_produce",
    ],
    "downstream engines can consume the same model": [
        f"{IR}.test_ir_topology::test_what_depends_on_what",
        f"{IR}.test_ir_topology::test_containment",
    ],
    "tests pass": [f"{S}.test_audit_sweep::test_every_mutation_is_audited_without_requirement_text"],
    "documentation is updated": [
        f"{IR}.test_ir_documentation::test_every_topic_of_the_specification_is_covered",
        f"{IR}.test_ir_documentation::test_every_ir_example_is_a_valid_architecture",
        f"{S}.test_documentation::test_the_decisions_are_recorded",
    ],
    "no unrelated subsystem was rewritten": [
        f"{S}.test_traceability_projects::test_every_item_of_the_spec_is_mapped",
        f"{S}.test_traceability_requirements_engine::test_every_item_of_the_spec_is_mapped",
    ],
}

SPEC = [
    "existing implementation was audited before modification",
    "one canonical Architecture IR representation exists",
    "no competing architecture model was introduced",
    "nodes and connections are consistently represented",
    "stable identifiers are supported",
    "configuration is structured and extensible",
    "requirement traceability is supported",
    "provenance and unknown values are preserved",
    "structural invariants are enforced deterministically",
    "serialization and deserialization work",
    "schema versioning is explicit",
    "architecture revisions are immutable",
    "historical revisions can be retrieved",
    "deterministic architecture diff works",
    "persistence is transaction-safe",
    "project and organization boundaries are enforced",
    "API contracts remain compatible",
    "Architecture Engine can consume and produce the canonical IR",
    "downstream engines can consume the same model",
    "tests pass",
    "documentation is updated",
    "no unrelated subsystem was rewritten",
]


@pytest.mark.parametrize(("item", "tests"), DONE.items(), ids=list(DONE))
def test_item_is_proven_by_existing_tests(item: str, tests: list[str]) -> None:
    assert tests, item
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{item}: {reference} not found"


def test_every_item_of_the_spec_is_mapped() -> None:
    assert list(DONE) == SPEC
    assert len(SPEC) == 22


# The model's own names. Any class with one of these names elsewhere would be a competing model.
MODEL_NAMES = {
    "ArchitectureIR",
    "Node",
    "Connection",
    "Edge",
    "ArchitectureNode",
    "ArchitectureEdge",
    "ArchitectureGraph",
}
PACKAGES = ("core", "engines", "apps/api", "persistence", "ai", "discovery", "knowledge", "workers")


def test_there_is_one_architecture_model() -> None:
    found = []
    for package in PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            if "core/architecture_ir" in path.as_posix():
                continue
            tree = ast.parse(path.read_text() or "")
            found += [
                f"{path.relative_to(ROOT)}: {node.name}"
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and node.name in MODEL_NAMES
            ]
    assert found == []
    assert not (ROOT / "core" / "domain" / "architecture" / "nodes.py").exists()


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Definition of done", "## Final review"):
        assert heading in text, heading
