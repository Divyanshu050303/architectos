"""Knowledge/RAG Engine: each testing requirement (sections 14.1 to 14.5) mapped to the tests that prove it,
plus structural guarantees — no embedding provider (and every result says so), no other engine
depending on knowledge, no agent, answer, crawl or fetch endpoint, no architecture mutation, and the
documentation covering every topic without claiming a guarantee. Fails if a mapped test is renamed or
removed, or a requirement is unmapped."""

import ast
import importlib
import re
import uuid
from pathlib import Path

import pytest

from core.domain.knowledge.retrieval import SEMANTIC_NOT_CONFIGURED, RetrievalQuery, Scope
from engines.knowledge.engine import DeterministicKnowledgeEngine
from persistence.models import Base

U = "tests.unit.knowledge"
DOMAIN = f"{U}.test_knowledge_domain"
ADAPTERS = f"{U}.test_knowledge_adapters"
CHUNKING = f"{U}.test_knowledge_chunking"
INGESTION = f"{U}.test_knowledge_ingestion"
RETRIEVAL = f"{U}.test_knowledge_retrieval"
SERVICE = f"{U}.test_knowledge_service"
API = "tests.integration.api.test_knowledge"
PARITY = "tests.integration.api.test_knowledge_evaluation"
MIGRATIONS = "tests.integration.database.test_migrations"
EVAL = "tests.evaluation.knowledge.test_knowledge_regression"
S = "tests.security"
SAFETY = f"{S}.test_knowledge_safety"
HERE = f"{S}.test_traceability_knowledge_engine"

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
DOC = DOCS / "architecture" / "knowledge-engine.md"
ROUTES = ROOT / "apps" / "api" / "routes" / "knowledge.py"

ACCEPTANCE: dict[str, list[str]] = {
    # 14.1 Unit tests.
    "14.1 source validation": [
        f"{DOMAIN}::test_a_source_is_an_upload_or_a_record_snapshot_of_one_project",
        f"{ADAPTERS}::test_uploads_are_refused_before_anything_is_stored",
    ],
    "14.1 adapter behavior": [
        f"{ADAPTERS}::test_a_markdown_document_is_read_as_written",
        f"{ADAPTERS}::test_an_adr_is_read_field_by_field_with_its_status",
        f"{ADAPTERS}::test_a_requirement_version_is_read_with_its_classification",
    ],
    "14.1 normalization": [f"{CHUNKING}::test_markdown_structure_is_found_without_changing_a_word"],
    "14.1 chunking determinism": [
        f"{CHUNKING}::test_chunking_is_deterministic_and_ids_follow_the_words",
        f"{CHUNKING}::test_large_content_is_split_between_lines_then_words_within_the_limit",
        f"{CHUNKING}::test_repeated_headings_and_duplicate_passages_get_distinct_ids",
    ],
    "14.1 stable ids and checksums": [
        f"{DOMAIN}::test_chunk_and_document_ids_are_stable_while_the_content_is",
        f"{ADAPTERS}::test_equal_content_has_an_equal_checksum",
    ],
    "14.1 ingestion state transitions": [
        f"{DOMAIN}::test_an_ingestion_ends_exactly_one_way",
        f"{DOMAIN}::test_the_last_known_good_version_survives_a_failed_ingestion",
        f"{DOMAIN}::test_a_record_snapshot_becomes_stale_and_archived_sources_are_never_ingested",
    ],
    "14.1 idempotency": [f"{INGESTION}::test_unchanged_content_creates_nothing"],
    "14.1 embedding provider contract": [
        f"{HERE}::test_no_embedding_provider_is_configured_and_results_say_so"
    ],
    "14.1 vector metadata compatibility": [
        f"{HERE}::test_no_embedding_provider_is_configured_and_results_say_so"
    ],
    "14.1 query validation": [f"{DOMAIN}::test_a_query_narrows_its_scope_and_never_asks_for_everything"],
    "14.1 retrieval filtering and merging": [
        f"{RETRIEVAL}::test_filters_only_narrow",
        f"{RETRIEVAL}::test_identifiers_are_found_exactly_and_first",
        f"{RETRIEVAL}::test_a_passage_must_hold_half_the_query_terms",
        f"{RETRIEVAL}::test_retrieval_is_deterministic_and_never_duplicates",
    ],
    "14.1 citation construction": [
        f"{RETRIEVAL}::test_passages_are_cited_where_they_are_and_as_they_are_known",
        f"{DOMAIN}::test_locators_claim_only_what_the_source_supports",
    ],
    "14.1 empty and insufficient evidence": [
        f"{RETRIEVAL}::test_results_are_never_padded_and_say_what_was_not_searched",
        f"{DOMAIN}::test_a_result_is_evidence_with_citations_never_padded",
    ],
    "14.1 error classification and retries": [
        f"{ADAPTERS}::test_hostile_or_empty_content_fails_with_the_line_never_the_content",
        f"{SERVICE}::test_a_deleted_record_is_a_failed_ingestion_that_keeps_the_version",
    ],
    # 14.2 Integration tests.
    "14.2 source creation and ingestion": [f"{API}::test_a_document_is_indexed_searched_and_cited"],
    "14.2 re-ingestion of unchanged content": [
        f"{API}::test_versions_unchanged_and_failures",
        f"{SAFETY}::test_unchanged_content_stores_nothing_more",
    ],
    "14.2 changed source versions": [
        f"{API}::test_versions_unchanged_and_failures",
        f"{API}::test_a_requirement_snapshot_goes_stale_and_is_re_read",
    ],
    "14.2 failed ingestion preserves last known-good content": [
        f"{API}::test_versions_unchanged_and_failures"
    ],
    "14.2 retrieval from indexed content": [
        f"{API}::test_a_document_is_indexed_searched_and_cited",
        f"{PARITY}::test_the_api_returns_what_the_evaluation_measures",
    ],
    "14.2 source, document and chunk lookup": [
        f"{API}::test_a_document_is_indexed_searched_and_cited",
        f"{API}::test_archived_sources_are_kept_but_never_searched",
    ],
    "14.2 persistence transactions": [
        f"{API}::test_stored_records_are_kept_as_written",
        f"{SERVICE}::test_registration_stores_the_run_then_the_version_then_the_source",
        f"{MIGRATIONS}::test_downgrading_knowledge_leaves_drift_intact",
    ],
    "14.2 API schemas and error envelopes": [
        f"{API}::test_invalid_requests_store_nothing",
        f"{S}.test_documentation::test_every_endpoint_is_documented_and_nothing_else_is",
        f"{S}.test_mass_assignment_sweep::test_every_body_endpoint_is_in_the_sweep",
    ],
    "14.2 tenant and project authorization": [f"{API}::test_authorization_and_tenant_isolation"],
    # 14.3 Security tests.
    "14.3 cross-organization retrieval denial": [
        f"{API}::test_authorization_and_tenant_isolation",
        f"{S}.test_tenant_isolation_sweep::test_a_stranger_gets_404_on_every_project_endpoint_and_changes_nothing",
    ],
    "14.3 cross-project retrieval denial": [
        f"{RETRIEVAL}::test_another_projects_passage_is_never_returned",
        f"{SERVICE}::test_retrieval_finds_staleness_and_never_leaves_the_project",
    ],
    "14.3 unauthorized source access": [
        f"{API}::test_authorization_and_tenant_isolation",
        f"{SERVICE}::test_sources_of_another_project_are_not_found",
    ],
    "14.3 IDOR attempts": [f"{API}::test_authorization_and_tenant_isolation"],
    "14.3 unauthorized ingestion, re-indexing or deletion": [
        f"{API}::test_authorization_and_tenant_isolation",
        f"{S}.test_authentication_sweep::test_the_api_is_exactly_the_specified_endpoint_list",
    ],
    "14.3 citation and metadata leakage": [
        f"{RETRIEVAL}::test_another_projects_passage_is_never_returned",
        f"{SAFETY}::test_revoked_access_is_revoked_at_once",
    ],
    "14.3 cache isolation": [f"{SAFETY}::test_knowledge_code_cannot_execute_fetch_cache_or_score"],
    "14.3 revoked access": [f"{SAFETY}::test_revoked_access_is_revoked_at_once"],
    "14.3 malformed and oversized inputs": [
        f"{SAFETY}::test_hostile_oversized_and_malformed_input_is_refused",
        f"{SAFETY}::test_ingestion_is_rate_limited",
    ],
    "14.3 path traversal": [
        f"{ADAPTERS}::test_uploads_are_refused_before_anything_is_stored",
        f"{SAFETY}::test_hostile_oversized_and_malformed_input_is_refused",
    ],
    "14.3 secret redaction in logs and errors": [
        f"{SAFETY}::test_secrets_and_queries_never_reach_the_logs",
        f"{ADAPTERS}::test_secrets_are_redacted_before_anything_is_indexed",
        f"{SAFETY}::test_a_documents_instructions_are_data_never_followed",
    ],
    # 14.4 Regression and determinism.
    "14.4 stable normalized output and chunking": [
        f"{CHUNKING}::test_chunking_is_deterministic_and_ids_follow_the_words",
        f"{INGESTION}::test_ingestion_is_deterministic_and_configurable",
    ],
    "14.4 unchanged re-ingestion creates no duplicates": [
        f"{SAFETY}::test_unchanged_content_stores_nothing_more"
    ],
    "14.4 filters cannot broaden authorization scope": [
        f"{RETRIEVAL}::test_filters_only_narrow",
        f"{API}::test_authorization_and_tenant_isolation",
    ],
    "14.4 source version changes traceable": [
        f"{INGESTION}::test_changed_content_indexes_the_next_version_keeping_unchanged_ids",
        f"{INGESTION}::test_a_requirement_snapshot_follows_its_versions_and_its_deletion",
    ],
    "14.4 failed indexing never reported successful": [
        f"{INGESTION}::test_a_failure_keeps_the_last_known_good_version",
        f"{DOMAIN}::test_an_ingestion_ends_exactly_one_way",
    ],
    "14.4 no downstream engine changes": [
        f"{HERE}::test_no_other_engine_depends_on_knowledge",
        f"{S}.test_audit_sweep::test_every_mutating_project_endpoint_is_classified",
    ],
    # 14.5 Retrieval evaluation.
    "14.5 expected evidence in top-k": [f"{EVAL}::test_quality_does_not_regress"],
    "14.5 citation correctness": [
        f"{EVAL}::test_quality_does_not_regress",
        f"{PARITY}::test_the_api_returns_what_the_evaluation_measures",
    ],
    "14.5 scope-filter correctness": [f"{EVAL}::test_quality_does_not_regress"],
    "14.5 empty-result behavior": [f"{EVAL}::test_every_metric_has_a_threshold_and_ceilings_hold_at_zero"],
    "14.5 duplicates; dataset size and limits documented": [
        f"{EVAL}::test_every_metric_has_a_threshold_and_ceilings_hold_at_zero",
        f"{EVAL}::test_the_set_is_well_formed_and_its_size_is_stated",
    ],
}


@pytest.mark.parametrize(("criterion", "tests"), ACCEPTANCE.items(), ids=list(ACCEPTANCE))
def test_criterion_is_proven_by_existing_tests(criterion: str, tests: list[str]) -> None:
    assert tests, criterion
    for reference in tests:
        module_name, _, function = reference.partition("::")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None)), f"{criterion}: {reference} not found"


def test_every_requirement_is_mapped() -> None:
    assert len(ACCEPTANCE) == 45  # 14 unit, 9 integration, 11 security, 6 regression, 5 evaluation


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


def test_no_embedding_provider_is_configured_and_results_say_so() -> None:
    for package in ("core/domain/knowledge", "engines/knowledge"):
        assert not [p for p, _ in _python(package) if "embedding" in p.name or "vector" in p.name]
    for table in Base.metadata.sorted_tables:
        if table.name.startswith("knowledge_"):
            kinds = {type(column.type).__name__ for column in table.columns}
            assert not kinds & {"Float", "REAL", "DOUBLE_PRECISION", "Vector"}, table.name  # no vectors
    result = DeterministicKnowledgeEngine().retrieve(
        RetrievalQuery("anything"), (), Scope(uuid.UUID(int=1), 0)
    )
    assert result.limitations[0] == SEMANTIC_NOT_CONFIGURED


# The architecture agent is the retriever's consumer: it may use the port and its result types —
# never the knowledge engine, its repository or its tables.
RETRIEVAL_BOUNDARY = frozenset({"core.domain.knowledge.ports", "core.domain.knowledge.retrieval"})


def test_no_other_engine_depends_on_knowledge() -> None:
    allowed = {"core/domain/unit_of_work.py"}  # the unit of work names every repository
    for package in ("core/domain", "engines"):
        for path, tree in _python(package):
            relative = path.relative_to(ROOT).as_posix()
            if "/knowledge/" in relative or relative in allowed:
                continue
            knowledge = [n for n in _imports(tree) if ".knowledge" in n]
            if "architecture_agent/" in relative:
                knowledge = [n for n in knowledge if n not in RETRIEVAL_BOUNDARY]
            assert not knowledge, relative


def test_there_is_no_agent_answer_crawl_or_mutation_endpoint() -> None:
    text = ROUTES.read_text()
    for verb in ("put", "patch", "delete"):
        assert f"@router.{verb}(" not in text
    for word in ("agent", "answer", "generate", "chat", "crawl", "fetch", "url", "embed"):
        assert not re.search(rf'["/][a-z-]*{word}', text), word
    service = (ROOT / "core" / "domain" / "knowledge" / "knowledge_service.py").read_text()
    assert "architecture_service" not in service  # nothing here revises an architecture
    assert "uow.architectures" not in service


def test_nothing_is_claimed_guaranteed() -> None:
    for path in (
        DOC,
        DOCS / "api" / "knowledge.md",
        DOCS / "adr" / "ADR-023-lexical-evidence-retrieval.md",
        DOCS / "frontend" / "knowledge-contract.md",
        ROOT / "ai" / "evaluation" / "datasets" / "knowledge" / "v1" / "README.md",
    ):
        text = " ".join(path.read_text().split())
        for sentence in re.findall(r"[^.]*\b(?:guarantee[sd]?|proves?|certif\w*)\b[^.]*", text, re.I):
            assert re.search(r"\b(not|never|no|nor|without)\b|out of scope", sentence, re.I), (
                path.name,
                sentence,
            )


def test_every_documentation_topic_is_covered() -> None:
    """Section 15's topics."""
    text = DOC.read_text()
    flat = " ".join(text.replace("\n>", "\n").split())
    assert "Retrieving a passage verifies nothing" in flat
    assert "untrusted data" in flat
    for heading in (
        "## Architecture and module boundaries",
        "## Supported source types and limitations",
        "## Ingestion lifecycle",
        "## Normalization and chunking",
        "## Embedding provider configuration",
        "## Indexing lifecycle and retries",
        "## Retrieval",
        "## Citation and evidence semantics",
        "## Authorization and tenant isolation",
        "## Resource limits",
        "## Error handling",
        "## Database migrations",
        "## Observability",
        "## Local development and tests",
        "## Operational troubleshooting",
        "## Known limitations and future work",
    ):
        assert heading in text, heading
    assert "MAX_KNOWLEDGE_BODY_BYTES" in (ROOT / ".env.example").read_text()
    assert "## Decision" in (DOCS / "adr" / "ADR-023-lexical-evidence-retrieval.md").read_text()
    assert (DOCS / "frontend" / "knowledge-contract.md").exists()
