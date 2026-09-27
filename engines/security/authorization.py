"""Authorization analysis: whether components that perform sensitive operations or hold sensitive
data model how they decide what an authenticated caller may do.

- A component declaring ``sensitive_operations: true`` needs an authorization model: ``none`` is a
  modeled gap (``missing_authorization``, high); no declared model cannot be evaluated
  (``authorization_not_modeled``).
- A component declared sensitive (``confidential``/``restricted`` data or personal data) is a
  sensitive resource: ``authorization: none`` is a gap (medium), no declared model cannot be
  evaluated (low).

Third parties' own authorization is not ours to model and is not analyzed. A model's name (``rbac``,
``abac``, …) is what the architecture says: never proof that its policies are right. ArchitectOS's
own roles and permissions are a platform concern, not analyzed here.
"""

from core.architecture_ir.component import NodeKind
from core.architecture_ir.node import Node
from core.domain.security.results import FindingCategory, FindingType, SecurityFinding
from core.domain.validation.results import Severity

from .context import SecurityContext
from .engine import AnalyzerMeta, AnalyzerOutput, Progress
from .support import MECHANISM_NOT_VERIFIED, certainty, evidence, finding

T = FindingType
FACTS = ("authorization", "sensitive_operations", "data_classification", "personal_data", "authentication")


class Authorization:
    meta = AnalyzerMeta(
        id="authorization",
        version=1,
        name="Authorization",
        description="Whether components performing sensitive operations or holding sensitive data model "
        "an authorization control.",
        category=FindingCategory.AUTHORIZATION,
        finding_types=(T.MISSING_AUTHORIZATION, T.AUTHORIZATION_NOT_MODELED),
        inputs=("components",),
        properties=FACTS,
        rules=(
            "A component performing sensitive operations with authorization none is a gap (high); without "
            "a declared authorization model it cannot be evaluated.",
            "A component holding sensitive data with authorization none is a gap (medium); without a "
            "declared model it cannot be evaluated (low).",
        ),
        unsupported=(
            "Whether a declared authorization model's policies are correct.",
            "Third-party components' own authorization.",
        ),
        limitations=(MECHANISM_NOT_VERIFIED,),
    )

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        ours = [n for n in context.components if n.kind is not NodeKind.EXTERNAL]
        return AnalyzerOutput(tuple(f for n in ours if (f := self._component(context, n))))

    def _component(self, context: SecurityContext, node: Node) -> SecurityFinding | None:
        facts = context.facts[node.id]
        operations = facts.known("sensitive_operations") is True
        if not operations and facts.sensitive is not True:
            return None
        model = facts.known("authorization")
        if model not in (None, "none"):
            return None
        what = "performs sensitive operations" if operations else "holds sensitive data"
        shown = evidence(facts, FACTS)
        used = (facts, FACTS)
        if model == "none":
            return finding(
                self.meta,
                T.MISSING_AUTHORIZATION,
                Severity.HIGH if operations else Severity.MEDIUM,
                certainty(used),
                title=f"{node.id} {what} without authorization",
                explanation=f"{node.id} {what} and declares authorization none: any caller that reaches "
                "it (authenticated or not) may do everything it allows.",
                recommendation=f"Review whether {node.id} should decide per caller what it may do (e.g. "
                "role- or attribute-based access control).",
                node_ids=(node.id,),
                evidence=shown,
            )
        return finding(
            self.meta,
            T.AUTHORIZATION_NOT_MODELED,
            Severity.MEDIUM if operations else Severity.LOW,
            certainty(used),
            title=f"How {node.id} authorizes its callers is not modeled",
            explanation=f"{node.id} {what}, but the architecture does not say how it decides what a "
            "caller may do: it is neither shown controlled nor shown open.",
            recommendation=f"State the authorization of {node.id} (none, or its model).",
            node_ids=(node.id,),
            evidence=shown,
            missing=(f"{node.id}.configuration.authorization",),
        )
