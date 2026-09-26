import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from core.domain.requirements.analyses import MAX_INPUT_CHARACTERS, RequirementAnalysis
from core.domain.requirements.analysis_service import MAX_PROMOTIONS, Promotion

from .common import ApiModel, RequestModel
from .requirement import RequirementResponse


class SpanModel(ApiModel):
    start: int
    end: int
    text: str = Field(description="Exactly the characters [start, end) of the input.")


class CandidateModel(ApiModel):
    key: str = Field(examples=["cand_3f2a9c1b0d4e5f67"], description="Deterministic; pass it to promote.")
    method: str = Field(description="pattern (deterministic rules) or llm")
    source: str = Field(description="system (rules) or ai (model): never authoritative until promoted")
    confidence: str = Field(description="Confidence in the interpretation, 0-1, as a decimal string.")
    span: SpanModel | None
    type: str
    category: str
    scope: str
    title: str
    statement: str = Field(description="The user's own sentence.")
    priority: str = Field(description="A proposal: a person decides when promoting.")
    structured_data: dict[str, Any]
    normalized_data: dict[str, Any] | None


class FindingModel(ApiModel):
    key: str
    kind: str = Field(
        description="invalid, rejected, duplicate, unresolved, extraction, ambiguity, assumption, "
        "conflict, consistency or completeness"
    )
    code: str
    severity: str = Field(description="blocking, warning or info")
    message: str
    suggestion: str | None
    field: str | None
    metric: str | None
    options: list[str]
    confidence: str | None
    candidate_keys: list[str]
    requirement_references: list[str] = Field(description="Existing requirements, e.g. REQ-3@v2.")
    span: SpanModel | None


class ProfileModel(ApiModel):
    name: str
    evidence: list[str]


class CompletenessModel(ApiModel):
    status: str = Field(description="complete, incomplete or unknown")
    profiles: list[ProfileModel]
    importance: dict[str, str]
    covered: list[str]
    missing: list[str]
    findings: list[FindingModel]


class ConfidenceModel(ApiModel):
    lowest: str | None
    average: str | None


class SemanticModel(ApiModel):
    used: bool
    reason: str | None
    source: str | None = None
    prompt_version: str | None = None
    status: str | None = None
    accepted: int | None = None
    rejected: int | None = None
    usage: dict[str, int] | None = None


class CountsModel(ApiModel):
    candidates: int
    blocking: int
    warnings: int
    info: int


class AnalysisResponse(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    input: str = Field(description="The text analyzed, exactly as written.")
    input_sha256: str
    engine_version: str
    result_schema: int
    ready_for_architecture: bool = Field(description="True when no finding is blocking.")
    blocking: list[str] = Field(description="Keys of the blocking findings.")
    counts: CountsModel
    confidence: ConfidenceModel
    semantic: SemanticModel
    candidates: list[CandidateModel]
    issues: list[FindingModel]
    ambiguities: list[FindingModel]
    assumptions: list[FindingModel]
    conflicts: list[FindingModel]
    completeness: CompletenessModel
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def from_analysis(cls, analysis: RequirementAnalysis) -> AnalysisResponse:
        result = analysis.result
        return cls.model_validate(
            {
                "id": analysis.id,
                "project_id": analysis.project_id,
                "input": analysis.raw_input,
                "input_sha256": analysis.input_sha256,
                "engine_version": analysis.engine_version,
                "result_schema": result["result_schema"],
                "ready_for_architecture": result["ready_for_architecture"],
                "blocking": result["blocking"],
                "counts": result["counts"],
                "confidence": result["confidence"],
                "semantic": result["semantic"],
                "candidates": result["candidates"],
                "issues": result["issues"],
                "ambiguities": result["ambiguities"],
                "assumptions": result["assumptions"],
                "conflicts": result["conflicts"],
                "completeness": result["completeness"] | {"findings": result["completeness_findings"]},
                "created_by_user_id": analysis.created_by_user_id,
                "created_at": analysis.created_at,
            }
        )


class AnalyzeRequest(RequestModel):
    input: Annotated[
        str,
        Field(
            max_length=MAX_INPUT_CHARACTERS,
            description="A description of the system and its requirements, in plain language.",
            examples=["A food delivery platform for 100K daily users, 2K RPS, p95 latency under 300 ms."],
        ),
    ]


class PromoteRequest(RequestModel):
    candidate_keys: Annotated[
        list[Annotated[str, Field(max_length=64)]],
        Field(
            max_length=MAX_PROMOTIONS, description="Keys of the candidates to turn into draft requirements."
        ),
    ]


class PromotionModel(ApiModel):
    candidate_key: str
    created: bool = Field(description="False when it had already been promoted (a retry).")
    requirement: RequirementResponse


class PromotionResponse(ApiModel):
    promotions: list[PromotionModel]

    @classmethod
    def from_promotions(cls, promotions: list[Promotion]) -> PromotionResponse:
        return cls(
            promotions=[
                PromotionModel(
                    candidate_key=p.candidate_key,
                    created=p.created,
                    requirement=RequirementResponse.from_requirement(p.requirement),
                )
                for p in promotions
            ]
        )
