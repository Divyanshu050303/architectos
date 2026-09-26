"""Validation runs over HTTP: the request (which revision, which rules), the stored run with its
summary and requirement verdicts, findings page by page, and the rule catalog."""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, StrictBool, StrictInt

from core.domain.validation.options import MAX_SELECTED_RULES, ValidationConfig
from core.domain.validation.results import Category, Finding, RequirementResult, Severity, Verdict
from core.domain.validation.runs import RunReport, RunStatus

from .common import ApiModel, RequestModel

type RuleId = Annotated[str, Field(min_length=3, max_length=64, examples=["structure.synchronous-cycle"])]
type ParameterName = Annotated[str, Field(min_length=1, max_length=64)]
type ParameterValue = StrictBool | StrictInt | Annotated[str, Field(max_length=200)]
type RuleParameters = Annotated[dict[ParameterName, ParameterValue], Field(max_length=20)]


class RunValidationRequest(RequestModel):
    """Every field is optional: the default validates the current revision with every rule of
    the default profile. Rule ids are map keys as given (not camelCased)."""

    revision: Annotated[int | None, Field(ge=1, description="Default: the current revision.")] = None
    profile: Annotated[str, Field(min_length=1, max_length=64, examples=["default", "strict"])] = "default"
    rules: Annotated[
        list[RuleId] | None,
        Field(max_length=MAX_SELECTED_RULES, description="Only these (mandatory rules always run)."),
    ] = None
    parameters: Annotated[
        dict[RuleId, RuleParameters],
        Field(max_length=MAX_SELECTED_RULES, description="By rule id, then parameter name."),
    ] = Field(default_factory=dict)
    severity_overrides: Annotated[
        dict[RuleId, Severity],
        Field(max_length=MAX_SELECTED_RULES, description="Re-grade findings of non-mandatory rules."),
    ] = Field(default_factory=dict)

    def to_config(self) -> ValidationConfig:
        return ValidationConfig(
            profile=self.profile,
            rules=tuple(self.rules) if self.rules is not None else None,
            parameters=self.parameters,
            severity_overrides=self.severity_overrides,
        )


class EvidenceModel(ApiModel):
    label: str
    value: str


class FindingModel(ApiModel):
    id: str = Field(description="Stable for the same finding on the same content, e.g. fnd_3f…")
    rule_id: str
    rule_version: int
    code: str
    severity: Severity
    category: Category
    title: str
    explanation: str
    remediation: str
    entity_ids: list[str] = Field(description="The nodes and connections involved.")
    field_paths: list[str]
    expected: str | None
    actual: str | None
    evidence: list[EvidenceModel]
    blocking: bool
    requirement_id: str | None
    policy_rule: str | None

    @classmethod
    def of(cls, finding: Finding) -> FindingModel:
        return cls(
            id=finding.id,
            rule_id=finding.rule_id,
            rule_version=finding.rule_version,
            code=finding.code,
            severity=finding.severity,
            category=finding.category,
            title=finding.title,
            explanation=finding.explanation,
            remediation=finding.remediation,
            entity_ids=list(finding.entity_ids),
            field_paths=list(finding.field_paths),
            expected=finding.expected,
            actual=finding.actual,
            evidence=[EvidenceModel(label=e.label, value=e.value) for e in finding.evidence],
            blocking=finding.blocking,
            requirement_id=finding.requirement_id,
            policy_rule=finding.policy_rule,
        )


class FindingPage(ApiModel):
    findings: list[FindingModel]
    next_cursor: str | None


class RequirementVerdictModel(ApiModel):
    requirement_id: str
    reference: str = Field(examples=["REQ-12"])
    requirement_version: int
    verdict: Verdict
    reason: str
    rule_id: str
    entity_ids: list[str]
    evidence: list[EvidenceModel]

    @classmethod
    def of(cls, result: RequirementResult) -> RequirementVerdictModel:
        return cls(
            requirement_id=result.requirement_id,
            reference=result.reference,
            requirement_version=result.requirement_version,
            verdict=result.verdict,
            reason=result.reason,
            rule_id=result.rule_id,
            entity_ids=list(result.entity_ids),
            evidence=[EvidenceModel(label=e.label, value=e.value) for e in result.evidence],
        )


class SummaryModel(ApiModel):
    """Counts only: no score is derived from them."""

    total: int
    by_severity: dict[str, int]
    by_category: dict[str, int]
    blocking: int
    requirements: dict[str, int] = Field(description="Requirement verdicts, counted per verdict.")
    rule_failures: int


class RuleFailureModel(ApiModel):
    rule_id: str
    rule_version: int
    error: str
    message: str


class LimitationModel(ApiModel):
    code: str
    message: str


class RuleSetModel(ApiModel):
    id: str
    version: str
    rules: list[tuple[str, int]] = Field(description="[ruleId, ruleVersion] of every rule that ran.")


class RunErrorModel(ApiModel):
    code: str
    message: str


class RunInputsModel(ApiModel):
    """What the run was given besides the revision. Rule ids stay map keys as given."""

    config: dict[str, Any]
    policy: dict[str, Any] | None
    requirement_count: int


class ValidationRunSummary(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    architecture_id: uuid.UUID
    revision: int
    revision_content_hash: str
    profile: str
    status: RunStatus
    requested_by_user_id: uuid.UUID | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_fingerprint: str | None
    summary: SummaryModel | None
    error: RunErrorModel | None

    @classmethod
    def fields_for(cls, report: RunReport) -> dict[str, Any]:
        run, summary = report.run, report.summary
        return {
            "id": run.id,
            "project_id": run.project_id,
            "architecture_id": run.architecture_id,
            "revision": run.revision_number,
            "revision_content_hash": run.revision_content_hash,
            "profile": run.profile,
            "status": run.status,
            "requested_by_user_id": run.requested_by_user_id,
            "requested_at": run.requested_at,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "result_fingerprint": report.result_fingerprint,
            "summary": SummaryModel(**summary.to_dict()) if summary is not None else None,
            "error": RunErrorModel(code=run.error.code, message=run.error.message) if run.error else None,
        }

    @classmethod
    def of(cls, report: RunReport) -> ValidationRunSummary:
        return cls(**cls.fields_for(report))


class ValidationRunResponse(ValidationRunSummary):
    """The run without its findings: GET …/findings pages through them."""

    rule_set: RuleSetModel | None
    context_fingerprint: str | None
    requirement_results: list[RequirementVerdictModel]
    failures: list[RuleFailureModel]
    limitations: list[LimitationModel]
    inputs: RunInputsModel

    @classmethod
    def of(cls, report: RunReport) -> ValidationRunResponse:
        rule_set = report.rule_set
        return cls(
            **cls.fields_for(report),
            rule_set=RuleSetModel(id=rule_set.id, version=rule_set.version, rules=list(rule_set.rules))
            if rule_set is not None
            else None,
            context_fingerprint=report.context_fingerprint,
            requirement_results=[RequirementVerdictModel.of(r) for r in report.requirement_results],
            failures=[RuleFailureModel(**f.to_dict()) for f in report.failures],
            limitations=[LimitationModel(**x.to_dict()) for x in report.limitations],
            inputs=RunInputsModel(
                config=dict(report.inputs.config),
                policy=dict(report.inputs.policy) if report.inputs.policy is not None else None,
                requirement_count=len(report.inputs.requirements),
            ),
        )


class ValidationRunPage(ApiModel):
    runs: list[ValidationRunSummary]
    next_cursor: str | None


class RuleParameterModel(ApiModel):
    type: str
    description: str
    default: Any


class RuleModel(ApiModel):
    id: str
    version: int
    name: str
    description: str
    category: Category
    severity: Severity = Field(description="The default severity of its findings.")
    profiles: list[str]
    inputs: list[str] = Field(description="What it needs besides the architecture: requirements, policy.")
    mandatory: bool = Field(description="Always runs; cannot be deselected or re-graded.")
    parameters: dict[str, RuleParameterModel]

    @classmethod
    def of(cls, data: Mapping[str, Any]) -> RuleModel:
        return cls(
            **{k: v for k, v in data.items() if k != "parameters"},
            parameters={name: RuleParameterModel(**p) for name, p in data["parameters"].items()},
        )


class RuleCatalogResponse(ApiModel):
    rules: list[RuleModel]
    profiles: list[str]
