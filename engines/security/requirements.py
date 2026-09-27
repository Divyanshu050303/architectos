"""Security requirement evaluation: the project's in-force requirements of type ``security``,
translated into fixed conditions (``conditions.py``) by a documented keyword table, never by a
language model, and judged from what the architecture declares.

Security requirements are stated in words (their categories — ``encryption``, ``authentication``,
``authorization``, ``pii``, ``secrets`` — carry no structured constraint). A requirement is checked
only when its category and words match an entry of ``MAPPING``; the entry used is recorded with the
verdict (``mapping``, e.g. ``encryption + 'at rest'``). One requirement may map to several conditions
("encrypted at rest and in transit": two checks). A requirement whose words match no entry is listed
with condition ``unsupported`` and verdict ``not_verifiable``: it is never passed.

Words that narrow a condition to sensitive data or operations (``sensitive``, ``personal``, ``pii``,
``confidential``, ``restricted``, ``customer``, or the ``pii`` category) make it concern only what is
declared sensitive (and leave elements whose sensitivity is not declared unknown).

On what: the components that reference the requirement, else those of its scope (``service``,
``api``, ``database``, ``queue``, ``data``), else the whole architecture (``system``); ``user`` and
``region`` scopes are not modeled and are unsupported. A violation takes the requirement's priority
as severity (validation's convention).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from core.architecture_ir.node import Node
from core.domain.requirements.entities import Requirement
from core.domain.requirements.enums import RequirementScope, RequirementType
from core.domain.security.results import (
    CheckResult,
    CheckSource,
    Condition,
    FindingCategory,
    FindingType,
    SecurityFinding,
)
from core.domain.validation.results import Verdict
from engines.validation.rules.requirements import SCOPE_KINDS, SEVERITY_BY_PRIORITY

from .conditions import Ask, Subject, judge, report
from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress

ANY = frozenset({"encryption", "authentication", "authorization", "pii", "secrets"})
SENSITIVE = re.compile(r"\b(sensitive|personal|pii|confidential|restricted|customer)\b", re.IGNORECASE)
ENCRYPT = re.compile(r"\bencrypt", re.IGNORECASE)
ENCRYPTION = frozenset({Condition.ENCRYPTION_AT_REST, Condition.ENCRYPTION_IN_TRANSIT})


def _words(*patterns: str) -> Callable[[str], str | None]:
    """A matcher: the matched words when every pattern occurs in the statement, else None."""
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

    def match(statement: str) -> str | None:
        found = [m.group(0).lower() for c in compiled if (m := c.search(statement))]
        return " … ".join(found) if len(found) == len(compiled) else None

    return match


@dataclass(frozen=True, slots=True)
class Entry:
    categories: frozenset[str]
    words: Callable[[str], str | None]
    condition: Condition
    approved: frozenset[str] = frozenset()


# The documented mapping, in order (a requirement collects every entry it matches).
MAPPING: tuple[Entry, ...] = (
    Entry(
        frozenset({"encryption", "pii"}),
        _words(r"\b(at\s+rest|stored|storage|disks?|databases?|backups?)\b"),
        Condition.ENCRYPTION_AT_REST,
    ),
    Entry(
        frozenset({"encryption", "pii"}),
        _words(r"\b(in\s+transit|transit|tls|ssl|https|over\s+the\s+network)\b"),
        Condition.ENCRYPTION_IN_TRANSIT,
    ),
    Entry(
        frozenset({"authentication"}),
        _words(r"\b(public(ly)?|internet|external(ly)?|exposed)\b"),
        Condition.AUTHENTICATION_ON_PUBLIC,
    ),
    Entry(
        frozenset({"authorization"}),
        _words(r"\b(sensitive|privileged|admin(istrative)?|operations?)\b"),
        Condition.AUTHORIZATION_ON_SENSITIVE,
    ),
    Entry(
        ANY,
        _words(
            r"\b(admin(istrative)?|management)\b",
            r"\b(interfaces?|consoles?|panels?|endpoints?)\b",
            r"\b(public(ly)?|internet|exposed)\b",
        ),
        Condition.NO_PUBLIC_MANAGEMENT_INTERFACE,
    ),
    Entry(
        frozenset({"secrets"}),
        _words(r"\b(secrets?\s+manager|vault|kms|key\s+vault|secret\s+store)\b"),
        Condition.APPROVED_SECRET_SOURCE,
        frozenset({"secret_manager"}),
    ),
    Entry(frozenset({"secrets"}), _words(r"\brotat\w*"), Condition.SECRET_ROTATION),
    Entry(ANY, _words(r"\baudit\w*"), Condition.AUDIT_LOGGING),
    Entry(ANY, _words(r"\bclassif\w*"), Condition.DATA_CLASSIFICATION),
)


def translate(requirement: Requirement) -> list[tuple[Entry, str]]:
    """The entries a requirement's category and words match, with the words matched. A ``pii``
    requirement maps to encryption only when it speaks of encrypting."""
    content = requirement.content
    silent = content.category == "pii" and not ENCRYPT.search(content.statement)
    return [
        (entry, words)
        for entry in MAPPING
        if content.category in entry.categories
        and not (silent and entry.condition in ENCRYPTION)
        and (words := entry.words(content.statement)) is not None
    ]


def _scope(context: SecurityContext, requirement: Requirement) -> tuple[Node, ...] | None:
    """The components it concerns (within the analysis scope); None when its scope is not modeled."""
    components = context.components
    referencing = tuple(
        n for n in components if any(r.requirement_id == requirement.id for r in n.requirement_refs)
    )
    if referencing:
        return referencing
    scope = requirement.content.scope
    if scope is RequirementScope.SYSTEM:
        return components
    kinds = SCOPE_KINDS.get(scope)
    return None if kinds is None else tuple(n for n in components if n.kind in kinds)


class Requirements:
    meta = AnalyzerMeta(
        id="requirements",
        version=1,
        name="Security requirements",
        description="In-force security requirements translated by a documented keyword table into "
        "fixed conditions, judged from what the architecture declares.",
        category=FindingCategory.REQUIREMENT,
        finding_types=(FindingType.REQUIREMENT_VIOLATED, FindingType.REQUIREMENT_NOT_EVALUABLE),
        inputs=("components", "connections", "requirements"),
        rules=(
            "encryption or pii (with 'encrypt') + at rest / stored / storage / database / backup → "
            "encryption_at_rest",
            "encryption or pii (with 'encrypt') + in transit / TLS / SSL / HTTPS / over the network → "
            "encryption_in_transit",
            "authentication + public / internet / external / exposed → authentication_on_public",
            "authorization + sensitive / privileged / admin / operation → authorization_on_sensitive",
            "admin or management + interface / console / panel / endpoint + public / internet / exposed → "
            "no_public_management_interface",
            "secrets + secret(s) manager / vault / KMS / key vault / secret store → approved_secret_source "
            "(secret_manager)",
            "secrets + rotate / rotation → secret_rotation",
            "any security category + audit → audit_logging",
            "any security category + classify / classification → data_classification",
            "sensitive / personal / PII / confidential / restricted / customer (or category pii) → only "
            "what is declared sensitive",
            "anything else → unsupported (not_verifiable, never passed)",
        ),
        produces=("findings", "checks"),
        unsupported=(
            "Requirements whose words match no entry (e.g. MFA, SSO, password policies, key lengths).",
            "Requirements scoped to users or regions.",
            "Compliance frameworks (GDPR, HIPAA, PCI DSS, SOC 2): no compliance is claimed.",
        ),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        checks: list[CheckResult] = []
        findings: list[SecurityFinding] = []
        chosen = [
            r
            for r in context.requirements
            if r.content.in_force and r.content.type is RequirementType.SECURITY
        ]
        for requirement in sorted(chosen, key=lambda r: r.reference):
            found_checks, found = self._requirement(context, requirement)
            checks += found_checks
            findings += found
        return AnalyzerOutput(tuple(findings), tuple(checks))

    def _requirement(
        self, context: SecurityContext, requirement: Requirement
    ) -> tuple[list[CheckResult], list[SecurityFinding]]:
        key = f"requirement.{requirement.reference.lower()}"
        requirement_id = str(requirement.id)
        matched = translate(requirement)
        nodes = _scope(context, requirement)
        if not matched or nodes is None:
            why = (
                "Its words match no supported condition of the documented mapping."
                if not matched
                else f"Its scope ({requirement.content.scope.value}) is not modeled by the architecture."
            )
            unsupported = CheckResult(
                key,
                CheckSource.REQUIREMENT,
                Condition.UNSUPPORTED,
                Verdict.NOT_VERIFIABLE,
                f"{why} It is never reported as met.",
                requirement_id=requirement_id,
            )
            return [unsupported], []
        content = requirement.content
        sensitive = content.category == "pii" or bool(SENSITIVE.search(content.statement))
        severity = SEVERITY_BY_PRIORITY[content.priority]
        checks, findings = [], []
        for entry, words in matched:
            ask = Ask(entry.condition, nodes, sensitive_only=sensitive, approved=entry.approved)
            mapping = f"{content.category} + '{words}'" + (" (sensitive only)" if sensitive else "")
            subject = Subject(
                key if len(matched) == 1 else f"{key}.{entry.condition.value}",
                f"The requirement {requirement.reference}",
                severity,
                requirement_id=requirement_id,
                mapping=mapping,
            )
            check, found = report(self.meta, judge(context, ask), entry.condition, subject)
            checks.append(check)
            if found is not None:
                findings.append(found)
        return checks, findings
