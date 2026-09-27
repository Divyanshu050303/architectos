"""Security policy evaluation: the project's architecture policy, as it was when the analysis ran
(its snapshot is part of the analysis inputs and fingerprint), checked field by field.

Each active security field is one fixed condition (``conditions.py``): ``require_tls``,
``require_encryption_at_rest``, ``require_authentication_on_public``,
``require_authorization_on_sensitive``, ``prohibit_public_management_interfaces``,
``approved_secret_sources``, ``require_secret_rotation``, ``require_audit_logging``,
``require_data_classification``. A field left at its default constrains nothing and is not checked.
A violation is ``policy_violated`` (high, validation's convention for policy); a verdict that cannot
be reached is ``policy_not_evaluable`` — never reported as met. ``require_tls`` is also enforced by
the validation engine's ``policy.tls`` rule, with the same meaning; it is reported here so a security
analysis states every security rule of the policy.
"""

from collections.abc import Callable

from core.domain.checks import Subject
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.security.results import CheckResult, Condition, FindingCategory, FindingType, SecurityFinding
from core.domain.validation.results import Severity

from .conditions import Ask, judge, report
from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress

# policy field -> (condition, whether the field is active)
RULES: dict[str, tuple[Condition, Callable[[ArchitecturePolicy], bool]]] = {
    "require_tls": (Condition.ENCRYPTION_IN_TRANSIT, lambda p: p.require_tls),
    "require_encryption_at_rest": (Condition.ENCRYPTION_AT_REST, lambda p: p.require_encryption_at_rest),
    "require_authentication_on_public": (
        Condition.AUTHENTICATION_ON_PUBLIC,
        lambda p: p.require_authentication_on_public,
    ),
    "require_authorization_on_sensitive": (
        Condition.AUTHORIZATION_ON_SENSITIVE,
        lambda p: p.require_authorization_on_sensitive,
    ),
    "prohibit_public_management_interfaces": (
        Condition.NO_PUBLIC_MANAGEMENT_INTERFACE,
        lambda p: p.prohibit_public_management_interfaces,
    ),
    "approved_secret_sources": (Condition.APPROVED_SECRET_SOURCE, lambda p: bool(p.approved_secret_sources)),
    "require_secret_rotation": (Condition.SECRET_ROTATION, lambda p: p.require_secret_rotation),
    "require_audit_logging": (Condition.AUDIT_LOGGING, lambda p: p.require_audit_logging),
    "require_data_classification": (Condition.DATA_CLASSIFICATION, lambda p: p.require_data_classification),
}


class Policy:
    meta = AnalyzerMeta(
        id="policy",
        version=1,
        name="Security policy",
        description="The project's security policy rules, checked against what the architecture declares.",
        category=FindingCategory.POLICY,
        finding_types=(FindingType.POLICY_VIOLATED, FindingType.POLICY_NOT_EVALUABLE),
        inputs=("components", "connections", "policy"),
        properties=(
            "tls",
            "encryption_at_rest",
            "exposure",
            "authentication",
            "sensitive_operations",
            "authorization",
            "management_interface",
            "secrets_required",
            "secret_source",
            "secret_rotation",
            "audit_logging",
            "data_classification",
        ),
        rules=tuple(f"{field} → {condition.value}" for field, (condition, _) in RULES.items()),
        produces=("findings", "checks"),
        unsupported=("Policy fields other than the security ones (the validation engine's).",),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        checks: list[CheckResult] = []
        findings: list[SecurityFinding] = []
        policy = context.policy
        for field, (condition, active) in RULES.items():
            if not active(policy):
                continue
            ask = Ask(condition, approved=policy.approved_secret_sources)
            subject = Subject(f"policy.{field}", f"The policy rule {field}", Severity.HIGH, policy_rule=field)
            check, found = report(self.meta, judge(context, ask), condition, subject)
            checks.append(check)
            if found is not None:
                findings.append(found)
        return AnalyzerOutput(tuple(findings), tuple(checks))
