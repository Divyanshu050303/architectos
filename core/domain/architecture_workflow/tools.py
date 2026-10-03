"""The workflow's server-side tool registry: every action the controller may execute, what it may
change, who it acts for, when it may run and how it is retried. Nothing outside this registry runs —
there is no generic code, shell, SQL or browsing tool, and a model never names an action.

Every tool runs **as the person who started the workflow**: its permission is checked against that
person's current membership before the action runs, never assumed from when the workflow started.

The workflow may perform ``read_only``, ``analysis`` and ``candidate_mutation`` actions on its own.
Nothing in the registry is a ``canonical_mutation`` or an ``external_side_effect``: confirming
requirements and approving a candidate are a person's own requests, under their own permissions.
"""

from dataclasses import dataclass

from core.domain.organizations.permissions import Permission

from .errors import ToolNotAllowed
from .values import AUTOMATIC, Action, SideEffect, Stage, WorkflowStatus

A, S, E = Action, Stage, SideEffect
ENGINES = (S.ANALYSIS,)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    action: Action
    description: str
    side_effect: SideEffect
    permission: Permission  # the starting person must still hold it
    stages: tuple[Stage, ...]  # the stages it may run in
    retryable: bool  # whether a failed attempt may be repeated under the same operation key
    calls_model: bool = False


TOOLS: dict[Action, ToolSpec] = {
    t.action: t
    for t in (
        ToolSpec(
            A.ANALYZE_REQUIREMENTS,
            "Extract requirement candidates from the goal with the requirements engine (a stored analysis).",
            E.CANDIDATE_MUTATION, Permission.REQUIREMENT_CREATE, (S.REQUIREMENTS,), True, calls_model=True,
        ),
        ToolSpec(
            A.REQUEST_CLARIFICATION,
            "Ask a person to confirm requirements or answer blocking questions; the workflow waits.",
            E.CANDIDATE_MUTATION, Permission.ARCHITECTURE_GENERATE, (S.REQUIREMENTS, S.GENERATION), False,
        ),
        ToolSpec(
            A.RETRIEVE_KNOWLEDGE,
            "Retrieve cited passages of the project's knowledge, authorized for the person.",
            E.READ_ONLY, Permission.KNOWLEDGE_READ, (S.KNOWLEDGE,), True,
        ),
        ToolSpec(
            A.GENERATE_ARCHITECTURE,
            "The architecture agent's candidate for the pinned requirement set, as canonical IR.",
            E.CANDIDATE_MUTATION, Permission.ARCHITECTURE_GENERATE, (S.GENERATION,), True, calls_model=True,
        ),
        ToolSpec(
            A.VALIDATE_ARCHITECTURE,
            "The validation engine on a candidate.",
            E.ANALYSIS, Permission.ARCHITECTURE_GENERATE, (S.VALIDATION,), False,
        ),
        ToolSpec(
            A.RUN_CAPACITY_ANALYSIS,
            "The capacity engine on a candidate, with a stated workload only.",
            E.ANALYSIS, Permission.ARCHITECTURE_GENERATE, ENGINES, False,
        ),
        ToolSpec(
            A.RUN_COST_ANALYSIS,
            "The cost engine on a candidate, with a stated pricing snapshot only.",
            E.ANALYSIS, Permission.ARCHITECTURE_GENERATE, ENGINES, False,
        ),
        ToolSpec(
            A.RUN_RELIABILITY_ANALYSIS,
            "The reliability engine on a candidate.",
            E.ANALYSIS, Permission.ARCHITECTURE_GENERATE, ENGINES, False,
        ),
        ToolSpec(
            A.RUN_SECURITY_ANALYSIS,
            "The security engine on a candidate.",
            E.ANALYSIS, Permission.ARCHITECTURE_GENERATE, ENGINES, False,
        ),
        ToolSpec(
            A.RUN_OBSERVABILITY_ANALYSIS,
            "The observability engine on a candidate.",
            E.ANALYSIS, Permission.ARCHITECTURE_GENERATE, ENGINES, False,
        ),
        ToolSpec(
            A.RUN_SIMULATION,
            "The simulation engine on a candidate, for a stated scenario only.",
            E.ANALYSIS, Permission.ARCHITECTURE_GENERATE, ENGINES, False,
        ),
        ToolSpec(
            A.GENERATE_ALTERNATIVE,
            "An improvement candidate from a candidate's findings: a deterministic evolution rule, or the "
            "agent again when validation blocks or no rule applies.",
            E.CANDIDATE_MUTATION, Permission.ARCHITECTURE_GENERATE, (S.ITERATION,), True, calls_model=True,
        ),
        ToolSpec(
            A.COMPARE_CANDIDATES,
            "The deterministic architecture diff between candidates (and their base).",
            E.CANDIDATE_MUTATION, Permission.ARCHITECTURE_GENERATE, (S.COMPARISON,), True,
        ),
        ToolSpec(
            A.PREPARE_REVIEW,
            "Select the candidates for review and assemble the review package.",
            E.CANDIDATE_MUTATION, Permission.ARCHITECTURE_GENERATE, (S.REVIEW,), False,
        ),
    )
}  # fmt: skip


def authorize(action: object, status: WorkflowStatus, stage: Stage) -> ToolSpec:
    """The tool for ``action`` if the workflow may run it now on its own; ``ToolNotAllowed`` otherwise.
    Permissions are checked separately, against the person's membership at the time."""
    spec = TOOLS.get(action) if isinstance(action, Action) else None
    if spec is None:
        raise ToolNotAllowed(details={"action": str(action)[:64], "reason": "unknown_action"})
    if spec.side_effect not in AUTOMATIC:
        raise ToolNotAllowed(details={"action": spec.action.value, "reason": "needs_a_person"})
    if status is not WorkflowStatus.RUNNING:
        raise ToolNotAllowed(details={"action": spec.action.value, "reason": "not_running"})
    if stage not in spec.stages:
        raise ToolNotAllowed(details={"action": spec.action.value, "reason": "wrong_stage"})
    return spec
