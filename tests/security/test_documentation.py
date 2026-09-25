"""The projects and requirements documentation stays in step with the code: every endpoint, error
code, audit action, metric and unit is documented, and nothing documented is missing."""

import re
from pathlib import Path

from fastapi import FastAPI

from core.domain.audit.entities import AuditAction
from core.domain.errors import DomainError
from core.domain.projects import errors as project_errors
from core.domain.requirements import errors as requirement_errors
from core.domain.requirements.enums import RequirementType
from core.domain.requirements.requirements import METRICS
from core.domain.requirements.value_objects import UNITS

from .support import inventory

DOCS = Path(__file__).resolve().parents[2] / "docs"
API_DOCS = (DOCS / "api" / "projects.md").read_text() + (DOCS / "api" / "requirements.md").read_text()
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
    assert (
        len(served) == 20
    )  # 7 project, 9 requirement (incl. analysis, validation, history), 4 set endpoints
    assert served - documented == set(), "undocumented endpoints"
    assert {d for d in documented if in_scope(d[1])} - served == set(), (
        "documented endpoints that do not exist"
    )


def test_every_error_code_is_documented() -> None:
    codes = {
        error.code
        for module in (project_errors, requirement_errors)
        for error in vars(module).values()
        if isinstance(error, type) and issubclass(error, DomainError) and error is not DomainError
    }
    assert {code for code in codes if code not in API_DOCS} == set()


def test_every_audit_action_is_documented() -> None:
    actions = {
        a.value for a in AuditAction if a.value.split(".")[0] in {"project", "requirement", "requirement_set"}
    }
    assert {a for a in actions if a not in API_DOCS} == set()


def test_the_taxonomy_metrics_and_units_are_documented() -> None:
    assert {t.value for t in RequirementType if f"`{t.value}`" not in DOMAIN_DOCS} == set()
    assert {m for m in METRICS if m not in DOMAIN_DOCS} == set()
    assert {u for u in UNITS if f"`{u}`" not in DOMAIN_DOCS} == set()


def test_the_decisions_are_recorded() -> None:
    for adr in ("ADR-007-requirement-versioning-and-sets.md", "ADR-008-project-write-locking.md"):
        text = (DOCS / "adr" / adr).read_text()
        assert "## Decision" in text
        assert "## Consequences" in text
    assert (DOCS / "frontend" / "projects-requirements-contract.md").exists()
