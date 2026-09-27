"""Observability policy evaluation: the project's architecture policy, as it was when the analysis
ran (its snapshot is part of the analysis inputs and fingerprint), checked field by field.

Each active observability field is one fixed condition (``conditions.py``): ``require_logs_on_critical``,
``required_metric_kinds_on_critical``, ``require_traces_on_critical``, ``require_trace_propagation``,
``require_health_checks_on_critical``, ``require_alerting_on_critical``, ``require_structured_logs``,
``require_correlation_ids``, ``require_ownership``, ``require_telemetry_collection``,
``min_telemetry_retention_seconds``. A field left at its default constrains nothing and is not
checked. A violation is ``policy_violated`` (high, validation's convention for policy); a verdict that
cannot be reached is ``policy_not_evaluable`` — never reported as met.
"""

from collections.abc import Callable

from core.domain.checks import Subject
from core.domain.observability.results import (
    CheckResult,
    Condition,
    FindingCategory,
    FindingType,
    ObservabilityFinding,
)
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.validation.results import Severity

from .conditions import Ask, judge, report
from .context import ObservabilityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress

C = Condition
# policy field -> (condition, whether the field is active)
RULES: dict[str, tuple[Condition, Callable[[ArchitecturePolicy], bool]]] = {
    "require_logs_on_critical": (C.LOGS_ON_CRITICAL, lambda p: p.require_logs_on_critical),
    "required_metric_kinds_on_critical": (
        C.METRICS_ON_CRITICAL,
        lambda p: bool(p.required_metric_kinds_on_critical),
    ),
    "require_traces_on_critical": (C.TRACES_ON_CRITICAL, lambda p: p.require_traces_on_critical),
    "require_trace_propagation": (C.PROPAGATION_ON_CRITICAL, lambda p: p.require_trace_propagation),
    "require_health_checks_on_critical": (
        C.HEALTH_CHECKS_ON_CRITICAL,
        lambda p: p.require_health_checks_on_critical,
    ),
    "require_alerting_on_critical": (C.ALERTING_ON_CRITICAL, lambda p: p.require_alerting_on_critical),
    "require_structured_logs": (C.STRUCTURED_LOGS, lambda p: p.require_structured_logs),
    "require_correlation_ids": (C.CORRELATION_IDS, lambda p: p.require_correlation_ids),
    "require_ownership": (C.OWNERSHIP, lambda p: p.require_ownership),
    "require_telemetry_collection": (C.TELEMETRY_COLLECTED, lambda p: p.require_telemetry_collection),
    "min_telemetry_retention_seconds": (
        C.TELEMETRY_RETENTION,
        lambda p: p.min_telemetry_retention_seconds is not None,
    ),
}


class Policy:
    meta = AnalyzerMeta(
        id="policy",
        version=1,
        name="Observability policy",
        description="The project's observability policy rules, checked against what the architecture "
        "declares.",
        category=FindingCategory.POLICY,
        finding_types=(FindingType.POLICY_VIOLATED, FindingType.POLICY_NOT_EVALUABLE),
        inputs=("components", "connections", "policy"),
        properties=(
            "criticality",
            "logs",
            "structured_logs",
            "correlation_ids",
            "metrics",
            "traces",
            "health_check",
            "alerts",
            "owner",
            "retention_seconds",
            "trace_propagation",
            "telemetry",
        ),
        rules=tuple(f"{field} → {condition.value}" for field, (condition, _) in RULES.items()),
        produces=("findings", "checks"),
        unsupported=(
            "Policy fields other than the observability ones (the validation and security engines').",
        ),
    )

    def analyze(self, context: ObservabilityContext, progress: Progress) -> AnalyzerOutput:
        checks: list[CheckResult] = []
        findings: list[ObservabilityFinding] = []
        policy = context.policy
        for field, (condition, active) in RULES.items():
            if not active(policy):
                continue
            ask = Ask(
                condition,
                kinds=policy.required_metric_kinds_on_critical,
                min_retention=policy.min_telemetry_retention_seconds,
            )
            subject = Subject(f"policy.{field}", f"The policy rule {field}", Severity.HIGH, policy_rule=field)
            check, found = report(self.meta, judge(context, ask), condition, subject)
            checks.append(check)
            if found is not None:
                findings.append(found)
        return AnalyzerOutput(tuple(findings), tuple(checks))
