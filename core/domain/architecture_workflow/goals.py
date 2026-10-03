"""What a person asks a workflow for: an architecture objective in their own words, and how far to
take it. A goal is never invented or completed by the workflow — what it does not state stays
unstated (the requirements stage asks a person), and every part of it is untrusted data for a model.

- ``requirement_set_id``: an existing pinned requirement set to design against; without one, the goal
  is analyzed by the requirements engine and a person confirms the requirements before design.
- ``base``: the exact revision to iterate on (approval then revises that architecture).
- ``capacity_analysis_id`` / ``cost_analysis_id``: stored analyses whose workload and pricing inputs
  are reused for every candidate; without them, capacity and cost are reported ``not_evaluated``.
- ``scenario``: a simulation scenario run on every candidate; without one, simulation is not run.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from core.domain.architecture_agent.requests import (
    MAX_CONTEXT,
    MAX_ITEM,
    MAX_ITEMS,
    MAX_OBJECTIVE,
    AgentRequest,
    BaseRevision,
)
from core.domain.simulations.scenarios import Scenario

from .errors import InvalidWorkflowRequest


def _invalid(field_name: str, reason: str) -> InvalidWorkflowRequest:
    return InvalidWorkflowRequest(details={"field": field_name, "reason": reason})


def _clean(value: object, field_name: str, limit: int, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise _invalid(field_name, "required" if required else "invalid")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise _invalid(field_name, "required")
        return None
    if len(cleaned) > limit:
        raise _invalid(field_name, "too_long")
    return cleaned


def _list(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > MAX_ITEMS:
        raise _invalid(field_name, "too_many")
    cleaned = tuple(_clean(v, field_name, MAX_ITEM, required=True) or "" for v in values)
    return tuple(dict.fromkeys(cleaned))


def _optional_id(value: object, field_name: str) -> None:
    if value is not None and not isinstance(value, uuid.UUID):
        raise _invalid(field_name, "invalid")


@dataclass(frozen=True, slots=True)
class WorkflowGoal:
    objective: str  # the person's own words
    constraints: tuple[str, ...] = ()  # hard
    preferences: tuple[str, ...] = ()  # soft
    exclusions: tuple[str, ...] = ()  # out of scope
    context: str | None = None
    base: BaseRevision | None = None
    requirement_set_id: uuid.UUID | None = None
    capacity_analysis_id: uuid.UUID | None = None
    cost_analysis_id: uuid.UUID | None = None
    scenario: Scenario | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "objective", _clean(self.objective, "objective", MAX_OBJECTIVE, required=True)
        )
        object.__setattr__(self, "context", _clean(self.context, "context", MAX_CONTEXT, required=False))
        for name in ("constraints", "preferences", "exclusions"):
            object.__setattr__(self, name, _list(getattr(self, name), name))
        if self.base is not None and not isinstance(self.base, BaseRevision):
            raise _invalid("base", "invalid")
        for name in ("requirement_set_id", "capacity_analysis_id", "cost_analysis_id"):
            _optional_id(getattr(self, name), name)
        if self.scenario is not None and not isinstance(self.scenario, Scenario):
            raise _invalid("scenario", "invalid")

    def agent_request(self, requirement_set_id: uuid.UUID) -> AgentRequest:
        """The architecture agent's request for this goal, against the pinned set."""
        return AgentRequest(
            requirement_set_id,
            self.objective,
            self.constraints,
            self.preferences,
            self.exclusions,
            self.context,
            self.base,
        )

    def to_dict(self) -> dict[str, Any]:
        base = self.base
        return {
            "objective": self.objective,
            "constraints": list(self.constraints),
            "preferences": list(self.preferences),
            "exclusions": list(self.exclusions),
            "context": self.context,
            "base": {"architecture_id": str(base.architecture_id), "number": base.number} if base else None,
            "requirement_set_id": str(self.requirement_set_id) if self.requirement_set_id else None,
            "capacity_analysis_id": str(self.capacity_analysis_id) if self.capacity_analysis_id else None,
            "cost_analysis_id": str(self.cost_analysis_id) if self.cost_analysis_id else None,
            "scenario": self.scenario.to_dict() if self.scenario else None,
        }
