"""ARCH-COMP-001 (component knowledge base): each acceptance criterion (section 16) and each test
category (section 13) mapped to the tests that prove it, plus structural guarantees — one catalog,
read safely, no dynamic code, the domain and the engine free of storage and network, and the
documentation covering every topic with the required examples. Fails if a mapped test is renamed or
removed, or a criterion is unmapped."""

import ast
import importlib
import re
from pathlib import Path

import pytest

U = "tests.unit.components"
I = "tests.integration.api"  # noqa: E741 - short prefix, read as a path
S = "tests.security"
SPECS = f"{U}.test_component_specifications"
CATALOG = f"{U}.test_component_catalog"
FILES = f"{U}.test_component_catalog_files"
CONSTRAINTS = f"{U}.test_component_constraints"
EVALUATION = f"{U}.test_component_evaluation"
VALIDATION = f"{U}.test_component_validation"
API = f"{I}.test_components"
RUNS = f"{I}.test_validations"
HERE = f"{S}.test_traceability_component_catalog"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "component-catalog.md"
PACKAGES = ("core/domain/components", "engines/constraints")
READER = "persistence/component_catalog.py"

ACCEPTANCE: dict[str, list[str]] = {
    # Section 16: acceptance criteria.
    "1. the existing component domain was audited": [f"{HERE}::test_the_audit_and_review_are_recorded"],
    "2. a canonical machine-readable specification contract": [
        f"{SPECS}::test_a_specification_is_canonical_versioned_and_hashed",
        f"{FILES}::test_the_published_schema_is_up_to_date_and_describes_every_entry",
        f"{FILES}::test_the_schema_refuses_what_the_code_refuses",
    ],
    "3. the initial categories and support states are represented": [
        f"{SPECS}::test_the_categories_are_a_registry_of_ir_node_kinds",
        f"{SPECS}::test_support_states_say_exactly_what_is_claimed",
        f"{FILES}::test_the_repository_catalog_loads_and_lists_every_specified_technology",
        f"{API}::test_categories_and_entries_state_their_support",
    ],
    "4. component configuration schemas can be validated": [
        f"{SPECS}::test_configuration_fields_are_architecture_ir_properties",
        f"{SPECS}::test_a_default_is_stated_only_when_documented_and_valid",
        f"{API}::test_the_configuration_is_read_by_the_ir_rules",
    ],
    "5. constraints distinguish hard, configurable, recommended, conditional and unknown limits": [
        f"{CONSTRAINTS}::test_constraints_are_never_promoted_or_invented",
        f"{CONSTRAINTS}::test_conditions_say_when_a_limit_holds",
        f"{EVALUATION}::test_every_constraint_type_has_its_outcome",
    ],
    "6. provenance is preserved for specification claims": [
        f"{SPECS}::test_every_claim_carries_provenance_and_documented_claims_cite_sources",
        f"{SPECS}::test_provenance_kinds_are_never_mixed_up",
        f"{CONSTRAINTS}::test_the_specified_catalog_entries_cite_what_they_claim",
        f"{API}::test_a_specification_shows_every_claim_with_its_provenance",
    ],
    "7. specification versions are identifiable and usable by analysis results": [
        f"{CATALOG}::test_the_current_version_and_every_older_one_are_readable",
        f"{CATALOG}::test_a_published_version_is_never_rewritten_or_removed",
        f"{FILES}::test_older_versions_are_read_from_history",
        f"{EVALUATION}::test_an_earlier_specification_version_is_reused_exactly",
        f"{VALIDATION}::test_a_stored_run_records_the_specification_versions",
        f"{RUNS}::test_catalog_components_are_checked_and_their_versions_recorded",
    ],
    "8. constraint evaluation returns deterministic, explainable outcomes": [
        f"{EVALUATION}::test_a_documented_limit_passes_or_is_violated_with_its_evidence",
        f"{EVALUATION}::test_identical_inputs_give_identical_findings_and_ids",
        f"{API}::test_a_configuration_is_evaluated_against_the_documented_constraints",
    ],
    "9. unknown data produces cannot_evaluate, never fabricated results": [
        f"{EVALUATION}::test_unknown_data_is_never_a_pass",
        f"{EVALUATION}::test_the_spec_example_concludes_nothing_the_catalog_does_not_state",
        f"{SPECS}::test_capacity_values_carry_their_basis_and_no_generic_throughput",
        f"{VALIDATION}::test_what_cannot_be_evaluated_stays_visible",
    ],
    "10. downstream engines consume shared specifications without competing catalogs": [
        f"{VALIDATION}::test_a_violated_limit_is_a_validation_finding_with_its_evidence",
        f"{VALIDATION}::test_the_limitation_names_what_was_not_checked",
        f"{HERE}::test_there_is_one_catalog",
    ],
    "11. API authorization and tenant isolation are tested": [
        f"{API}::test_the_catalog_needs_a_session",
        f"{S}.test_authentication_sweep::test_every_protected_endpoint_refuses_bad_credentials",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
    ],
    "12. existing frontend and API contracts remain compatible or change deliberately": [
        f"{VALIDATION}::test_without_a_catalog_or_references_validation_is_unchanged",
        f"{RUNS}::test_a_validation_is_run_stored_and_read_back",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
        f"{HERE}::test_the_frontend_contract_is_documented",
    ],
    "13. unit, integration, security and regression tests pass": [
        f"{S}.test_traceability_validation_engine::test_every_criterion_is_mapped",
        f"{S}.test_traceability_architecture_ir::test_there_is_one_architecture_model",
        "tests.unit.architecture_ir.test_ir_schema::test_the_published_schema_is_up_to_date",
    ],
    "14. documentation explains the schema and the extension process": [
        f"{HERE}::test_every_documentation_topic_is_covered",
        f"{S}.test_documentation::test_every_error_code_is_documented",
    ],
    "15. the report identifies supported, partial, planned and unsupported entries": [
        f"{CONSTRAINTS}::test_the_specified_catalog_entries_cite_what_they_claim",
        f"{HERE}::test_every_documentation_topic_is_covered",
    ],
    # Section 13: test categories not already named above.
    "untrusted specification input is refused precisely": [
        f"{SPECS}::test_untrusted_input_is_refused_with_the_fields_at_fault",
        f"{FILES}::test_files_are_untrusted_input",
        f"{FILES}::test_files_are_bounded_and_links_are_not_followed",
        f"{FILES}::test_invalid_specifications_name_their_file_and_fields",
    ],
    "category and component lookup, deprecated components": [
        f"{CATALOG}::test_lookups_are_by_catalog_id_and_exact_version",
        f"{CATALOG}::test_listing_and_categories_count_current_entries_by_status",
        f"{CATALOG}::test_deprecated_entries_stay_readable_and_name_a_known_successor",
        f"{EVALUATION}::test_a_deprecated_specification_is_a_warning_and_still_evaluated",
        f"{API}::test_unknown_components_and_versions_are_not_found",
    ],
    "capability and security metadata": [
        f"{SPECS}::test_capability_states_are_explicit_and_never_assumed",
        f"{SPECS}::test_security_properties_name_the_ir_property_that_enables_them",
    ],
    "unit compatibility and documented values": [
        f"{CONSTRAINTS}::test_the_documented_limits_are_the_sources_values",
        f"{CONSTRAINTS}::test_list_constraints_use_the_ir_properties_values",
        f"{EVALUATION}::test_lambda_memory_is_checked_against_the_documented_range",
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
    assert len(ACCEPTANCE) == 19  # 15 acceptance criteria and 4 test categories


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


def test_the_domain_and_engine_reach_no_storage_network_or_dynamic_code() -> None:
    forbidden = (
        "persistence",
        "apps",
        "sqlalchemy",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "yaml",
        "random",
    )
    builtins = {"eval", "exec", "compile", "__import__"}
    for package in PACKAGES:
        for path, tree in _python(package):
            for name in _imports(tree):
                assert name.split(".")[0] not in forbidden, (path, name)
            calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
            assert not [f.id for f in calls if isinstance(f, ast.Name) and f.id in builtins], path


def test_specification_files_are_read_safely() -> None:
    """Only the catalog reader (and discovery's reader of untrusted artifacts) parse YAML, each with a
    safe loader that refuses aliases."""
    discovery = "engines/discovery/loading.py"
    readers = [
        path.relative_to(ROOT).as_posix()
        for package in ("core", "engines", "persistence", "apps")
        for path, tree in _python(package)
        if "yaml" in {name.split(".")[0] for name in _imports(tree)}
    ]
    assert sorted(readers) == sorted([READER, discovery])
    assert "Loader=_SafeLoader" in (ROOT / READER).read_text()
    assert "class _Loader(yaml.SafeLoader)" in (ROOT / discovery).read_text()
    for path in (READER, discovery):
        text = (ROOT / path).read_text()
        assert "aliases are not allowed" in text, path
        for unsafe in ("yaml.unsafe_load", "FullLoader", "UnsafeLoader", "yaml.Loader"):
            assert unsafe not in text, (path, unsafe)


def _assigned(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign):
        return [node.target]
    return []


def test_there_is_one_catalog() -> None:
    """The catalog's files are read in one place, and the vocabularies are defined in one module:
    no engine keeps technology data of its own."""
    knowledge = [
        path.relative_to(ROOT).as_posix()
        for package in ("core", "engines", "persistence", "apps")
        for path, tree in _python(package)
        if any(isinstance(n, ast.Constant) and n.value == "knowledge" for n in ast.walk(tree))
    ]
    assert knowledge == [READER]  # the only module that builds a path to the catalog's files
    definitions = [
        path.relative_to(ROOT).as_posix()
        for package in ("core", "engines", "apps")
        for path, tree in _python(package)
        for node in ast.walk(tree)
        if any(isinstance(t, ast.Name) and t.id == "CAPABILITIES" for t in _assigned(node))
    ]
    assert definitions == ["core/domain/components/capabilities.py"]


def test_nothing_is_claimed_guaranteed() -> None:
    assert "they do not guarantee the performance\nor capacity of a deployment" in DOC.read_text()
    for path in (
        DOC,
        DOCS / "api" / "components.md",
        DOCS / "adr" / "ADR-019-component-catalog.md",
        DOCS / "frontend" / "component-contract.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(r"[^.]*\b(?:guarantee[sd]?|proves?|certif\w*)\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Section 14's list and the two required examples."""
    from persistence.component_catalog import default_catalog  # noqa: PLC0415 - the shipped catalog

    text = DOC.read_text()
    for heading in (
        "## Catalog architecture",
        "## Specification schema",
        "## Supported categories and entries",
        "## Provenance: documented, estimated, measured and unknown",
        "## Specification versioning",
        "## Constraint evaluation outcomes",
        "## Adding a component",
        "## Adding or updating a constraint",
        "## How downstream engines consume component data",
        "## Known unsupported technologies and fields",
        "## Example: a complete specification",
        "## Example: a constraint evaluation",
        "## Security of the catalog",
        "## Tests",
    ):
        assert heading in text, heading

    def section(title: str) -> str:
        return text.split(title, 1)[1].split("\n## ", 1)[0]

    assert "constraints:" in section("## Example: a complete specification")
    assert "sources:" in section("## Example: a complete specification")
    assert "violation" in section("## Example: a constraint evaluation")
    assert "cannot_evaluate" in section("## Example: a constraint evaluation")
    entries = section("## Supported categories and entries")
    for entry in default_catalog().list():
        assert f"`{entry.id.split('/')[1]}`" in entries or f"`{entry.id}`" in entries, entry.id
    for status in ("supported", "partial", "planned"):
        assert f"`{status}`" in entries, status


def test_the_frontend_contract_is_documented() -> None:
    text = (DOCS / "frontend" / "component-contract.md").read_text()
    for item in ("COMPONENT_TYPES", "ComponentDefinition", "component", "cannot_evaluate", "supportStatus"):
        assert item in text, item


def test_the_audit_and_review_are_recorded() -> None:
    text = DOC.read_text()
    for heading in ("## Repository audit", "## Final review", "## Known unsupported technologies and fields"):
        assert heading in text, heading
    assert "Reused" in text
    assert "Still empty placeholders" in text
