"""The projects and requirements documentation stays in step with the code: every endpoint, error
code, audit action, metric and unit is documented, and nothing documented is missing."""

import re
from pathlib import Path

from fastapi import FastAPI

from core.architecture_ir import commands as ir_commands
from core.architecture_ir import errors as ir_errors
from core.domain.architecture import errors as architecture_errors
from core.domain.audit.entities import AuditAction
from core.domain.capacity import errors as capacity_errors
from core.domain.components import errors as component_errors
from core.domain.cost import errors as cost_errors
from core.domain.decisions import errors as decision_errors
from core.domain.discovery import errors as discovery_errors
from core.domain.errors import DomainError
from core.domain.evolution import errors as evolution_errors
from core.domain.migrations import errors as migration_errors
from core.domain.observability import errors as observability_errors
from core.domain.projects import errors as project_errors
from core.domain.reliability import errors as reliability_errors
from core.domain.requirements import errors as requirement_errors
from core.domain.requirements.enums import RequirementType
from core.domain.requirements.requirements import METRICS
from core.domain.requirements.value_objects import UNITS
from core.domain.security import errors as security_errors
from core.domain.simulations import errors as simulation_errors
from core.domain.validation.errors import InvalidValidationConfig, ValidationRunNotFound

from .support import inventory

DOCS = Path(__file__).resolve().parents[2] / "docs"
API_DOCS = "".join(
    (DOCS / "api" / name).read_text()
    for name in (
        "projects.md",
        "requirements.md",
        "architecture.md",
        "validation.md",
        "capacity.md",
        "cost.md",
        "reliability.md",
        "security.md",
        "observability.md",
        "simulations.md",
        "evolution.md",
        "decisions.md",
        "migration-plans.md",
        "discovery.md",
        "components.md",
    )
)
DOMAIN_DOCS = (DOCS / "domain" / "projects.md").read_text() + (
    DOCS / "domain" / "requirements.md"
).read_text()
ENDPOINT = re.compile(r"`(GET|POST|PUT|PATCH|DELETE) (/[^`?\s]*)")


def shape(path: str) -> str:
    """/api/v1/projects/{project_id}/x -> /projects/{}/x (docs use shorter parameter names)."""
    return re.sub(r"\{[^}]+\}", "{}", path.removeprefix("/api/v1"))


def in_scope(path: str) -> bool:
    return "/projects" in path


def test_every_endpoint_is_documented_and_nothing_else_is(app: FastAPI) -> None:
    served = {(op.method, shape(op.path)) for op in inventory(app) if in_scope(op.path)}
    documented = {(method, shape(path)) for method, path in ENDPOINT.findall(API_DOCS)}
    # 9 project, 9 requirement, 4 requirement set, 3 requirement analysis, 14 architecture, 4
    # validation, 6 capacity, 4 cost, 5 reliability, 5 security, 5 observability, 6 simulation, 6
    # evolution, 8 decision, 14 migration plan and 10 discovery endpoints (GET /validation/rules,
    # /capacity/models, /cost/models, /reliability/models, /security/analyzers,
    # /observability/analyzers, /simulation/catalog and /evolution/catalog are not project-scoped)
    assert len(served) == 112
    assert served - documented == set(), "undocumented endpoints"
    assert {d for d in documented if in_scope(d[1])} - served == set(), (
        "documented endpoints that do not exist"
    )


def test_every_error_code_is_documented() -> None:
    codes = {
        error.code
        for module in (project_errors, requirement_errors, architecture_errors, ir_errors, ir_commands)
        for error in vars(module).values()
        if isinstance(error, type) and issubclass(error, DomainError) and error is not DomainError
    }
    codes |= {InvalidValidationConfig.code, ValidationRunNotFound.code}  # the others are never returned
    codes |= {
        e.code
        for e in (
            capacity_errors.InvalidWorkload,
            capacity_errors.InvalidQuantity,
            capacity_errors.InvalidCapacityConfig,
            capacity_errors.InvalidScenario,
            capacity_errors.CapacityAnalysisNotFound,
            cost_errors.InvalidCostRequest,
            cost_errors.IncompatibleCapacityAnalysis,
            cost_errors.CostAnalysisNotFound,
            cost_errors.PricingSnapshotNotFound,
            cost_errors.InvalidMoney,
            reliability_errors.InvalidReliabilityRequest,
            reliability_errors.ReliabilityAnalysisNotFound,
            security_errors.InvalidSecurityRequest,
            security_errors.SecurityAnalysisNotFound,
            observability_errors.InvalidObservabilityRequest,
            observability_errors.ObservabilityAnalysisNotFound,
            simulation_errors.InvalidSimulationRequest,
            simulation_errors.SimulationNotFound,
            evolution_errors.InvalidEvolutionRequest,
            evolution_errors.EvolutionAnalysisNotFound,
            evolution_errors.CandidateNotFound,
            decision_errors.InvalidDecision,
            decision_errors.DecisionNotFound,
            decision_errors.InvalidDecisionTransition,
            migration_errors.InvalidMigrationRequest,
            migration_errors.MigrationPlanNotFound,
            migration_errors.InvalidPlanTransition,
            migration_errors.PlanVersionMismatch,
            migration_errors.StaleMigrationPlan,
            migration_errors.ReviewedPlanNotReplaced,
            discovery_errors.InvalidDiscoveryRequest,
            discovery_errors.DiscoveryRunNotFound,
            discovery_errors.InvalidDiscoveryTransition,
            discovery_errors.ProposalNotAcceptable,
            discovery_errors.AcceptedRunNotDeleted,
            component_errors.ComponentNotFound,
        )
    }
    assert {code for code in codes if code not in API_DOCS} == set()


def test_every_audit_action_is_documented() -> None:
    actions = {
        a.value
        for a in AuditAction
        if a.value.split(".")[0]
        in {
            "project",
            "requirement",
            "requirement_set",
            "architecture",
            "decision",
            "migration_plan",
            "discovery_run",
        }
    }
    assert {a for a in actions if a not in API_DOCS} == set()


def test_the_taxonomy_metrics_and_units_are_documented() -> None:
    assert {t.value for t in RequirementType if f"`{t.value}`" not in DOMAIN_DOCS} == set()
    assert {m for m in METRICS if m not in DOMAIN_DOCS} == set()
    assert {u for u in UNITS if f"`{u}`" not in DOMAIN_DOCS} == set()


def test_the_decisions_are_recorded() -> None:
    for adr in (
        "ADR-007-requirement-versioning-and-sets.md",
        "ADR-008-project-write-locking.md",
        "ADR-009-requirements-engine.md",
        "ADR-002-architecture-ir.md",
        "ADR-010-architecture-records-and-versioning.md",
        "ADR-011-deterministic-validation.md",
        "ADR-012-deterministic-capacity.md",
        "ADR-013-deterministic-cost.md",
        "ADR-014-deterministic-reliability.md",
        "ADR-015-deterministic-security.md",
        "ADR-016-deterministic-observability.md",
        "ADR-017-deterministic-simulation.md",
        "ADR-018-deterministic-evolution.md",
        "ADR-019-component-catalog.md",
        "ADR-020-deterministic-migration-planning.md",
        "ADR-021-deterministic-discovery.md",
    ):
        text = (DOCS / "adr" / adr).read_text()
        assert "## Decision" in text
        assert "## Consequences" in text
    assert (DOCS / "frontend" / "projects-requirements-contract.md").exists()
    assert (DOCS / "frontend" / "migration-contract.md").exists()
    assert (DOCS / "architecture" / "migration-planning-engine.md").exists()
    assert (DOCS / "frontend" / "discovery-contract.md").exists()
    assert (DOCS / "architecture" / "discovery-engine.md").exists()
    assert (DOCS / "requirements-engine.md").exists()
    assert (DOCS / "frontend" / "architecture-contract.md").exists()
    assert (DOCS / "frontend" / "validation-contract.md").exists()
    assert (DOCS / "architecture" / "validation-engine.md").exists()
    assert (DOCS / "frontend" / "capacity-contract.md").exists()
    assert (DOCS / "architecture" / "capacity-engine.md").exists()
    assert (DOCS / "frontend" / "cost-contract.md").exists()
    assert (DOCS / "architecture" / "cost-engine.md").exists()
    assert (DOCS / "frontend" / "reliability-contract.md").exists()
    assert (DOCS / "architecture" / "reliability-engine.md").exists()
    assert (DOCS / "frontend" / "security-contract.md").exists()
    assert (DOCS / "architecture" / "security-engine.md").exists()
    assert (DOCS / "frontend" / "observability-contract.md").exists()
    assert (DOCS / "architecture" / "observability-engine.md").exists()
    assert (DOCS / "frontend" / "simulation-contract.md").exists()
    assert (DOCS / "architecture" / "simulation-engine.md").exists()
    assert (DOCS / "frontend" / "evolution-contract.md").exists()
    assert (DOCS / "architecture" / "evolution-engine.md").exists()
    assert (DOCS / "frontend" / "component-contract.md").exists()
    assert (DOCS / "architecture" / "component-catalog.md").exists()
