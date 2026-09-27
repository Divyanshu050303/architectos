"""A requirement or policy check over HTTP, shared by the engines that judge declared configuration
(``core/domain/checks.py``): each engine's model adds its own ``condition`` enum."""

from pydantic import Field

from core.domain.checks import CheckSource
from core.domain.validation.results import Verdict

from .capacity import EvidenceModel
from .common import ApiModel


class CheckBaseModel(ApiModel):
    key: str
    source: CheckSource
    verdict: Verdict = Field(
        description="satisfied or violated by modeled evidence only; not_verifiable when it cannot be "
        "decided or the requirement is unsupported (never a pass); not_applicable when nothing is concerned."
    )
    explanation: str
    node_ids: list[str]
    connection_ids: list[str]
    actual: list[EvidenceModel]
    missing: list[str]
    requirement_id: str | None
    policy_rule: str | None
    mapping: str | None = Field(description="How a requirement's words became the condition.")
