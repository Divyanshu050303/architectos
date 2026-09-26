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
from core.domain.cost import errors as cost_errors
from core.domain.errors import DomainError
from core.domain.projects import errors as project_errors
from core.domain.requirements import errors as requirement_errors
from core.domain.requirements.enums import RequirementType
from core.domain.requirements.requirements import METRICS
from core.domain.requirements.value_objects import UNITS
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
    # validation, 6 capacity and 4 cost endpoints (GET /validation/rules, /capacity/models and
    # /cost/models are not project-scoped)
    assert len(served) == 53
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
        )
    }
    assert {code for code in codes if code not in API_DOCS} == set()


def test_every_audit_action_is_documented() -> None:
    actions = {
        a.value
        for a in AuditAction
        if a.value.split(".")[0] in {"project", "requirement", "requirement_set", "architecture"}
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
    ):
        text = (DOCS / "adr" / adr).read_text()
        assert "## Decision" in text
        assert "## Consequences" in text
    assert (DOCS / "frontend" / "projects-requirements-contract.md").exists()
    assert (DOCS / "requirements-engine.md").exists()
    assert (DOCS / "frontend" / "architecture-contract.md").exists()
    assert (DOCS / "frontend" / "validation-contract.md").exists()
    assert (DOCS / "architecture" / "validation-engine.md").exists()
    assert (DOCS / "frontend" / "capacity-contract.md").exists()
    assert (DOCS / "architecture" / "capacity-engine.md").exists()
    assert (DOCS / "frontend" / "cost-contract.md").exists()
    assert (DOCS / "architecture" / "cost-engine.md").exists()
