"""The security analyzer contract and orchestrator (Milestone 10, phase 2): explicit selection,
isolated failures, outputs checked against their declaration, scope, coverage, trust zones and
determinism."""

import dataclasses
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration, ConfigValue
from core.architecture_ir.model import ArchitectureIR
from core.domain.capacity.results import Certainty
from core.domain.engine_results import Evidence, ModelSet
from core.domain.projects.policies import ArchitecturePolicy
from core.domain.security.analyses import SecurityAnalysisRequest
from core.domain.security.errors import InvalidSecurityRequest
from core.domain.security.results import (
    CheckResult,
    CheckSource,
    Condition,
    Coverage,
    FindingCategory,
    FindingType,
    SecurityFinding,
    SecurityStatus,
    TrustZone,
)
from core.domain.validation.options import RevisionInfo
from core.domain.validation.results import Severity, Verdict
from engines.security.context import SecurityContext
from engines.security.engine import (
    AnalyzerMeta,
    AnalyzerOutput,
    DuplicateAnalyzer,
    Progress,
    Registry,
    analyze,
)
from engines.security.registry import default_registry
from tests.unit.architecture_ir.builders import connection, node

REVISION = RevisionInfo("arch-1", 1, "c" * 64)
T = FindingType


def meta(analyzer_id: str, *types: FindingType, **overrides: Any) -> AnalyzerMeta:
    fields: dict[str, Any] = {
        "id": analyzer_id,
        "version": 1,
        "name": analyzer_id.title(),
        "description": f"The {analyzer_id} analyzer.",
        "category": FindingCategory.EXPOSURE,
        "finding_types": types or (T.EXPOSURE_NOT_MODELED,),
        "inputs": ("components",),
    }
    return AnalyzerMeta(**(fields | overrides))


@dataclass(frozen=True)
class Fake:
    meta: AnalyzerMeta
    run: Callable[[SecurityContext, Progress], Any]

    def analyze(self, context: SecurityContext, progress: Progress) -> AnalyzerOutput:
        output: AnalyzerOutput = self.run(context, progress)
        return output


def found(
    analyzer: AnalyzerMeta, node_ids: tuple[str, ...], type_: FindingType | None = None
) -> SecurityFinding:
    return SecurityFinding(
        type=type_ or analyzer.finding_types[0],
        severity=Severity.MEDIUM,
        certainty=Certainty.MODELED,
        title=f"Something about {', '.join(node_ids)}",
        explanation="What was detected.",
        recommendation="What to review.",
        node_ids=node_ids,
        analyzer_id=analyzer.id,
        analyzer_version=analyzer.version,
    )


def per_component(analyzer: AnalyzerMeta) -> Fake:
    """One finding per component in scope."""

    def run(context: SecurityContext, _: Progress) -> AnalyzerOutput:
        return AnalyzerOutput(tuple(found(analyzer, (n.id,)) for n in context.components))

    return Fake(analyzer, run)


def trust_zone(level: str | None = None) -> Configuration:
    values = {"boundary_type": "trust_zone"} | ({"trust_level": level} if level else {})
    return Configuration(values)


def architecture() -> ArchitectureIR:
    api: dict[str, ConfigValue] = {
        "exposure": "public",
        "authentication": "oauth2",
        "data_classification": "internal",
        "secrets_required": False,
    }
    return ArchitectureIR(
        "Shop",
        nodes=(
            node("web", NodeKind.CLIENT),
            node("dmz", NodeKind.BOUNDARY, configuration=trust_zone("untrusted")),
            node("core", NodeKind.BOUNDARY, configuration=trust_zone()),
            node(
                "vpc",
                NodeKind.BOUNDARY,
                parent_id="core",
                configuration=Configuration({"boundary_type": "network"}),
            ),
            node("api", parent_id="dmz", configuration=Configuration(api)),
            node(
                "db",
                NodeKind.DATABASE,
                parent_id="vpc",
                configuration=Configuration({"exposure": "private", "personal_data": True}),
            ),
            node("idp", NodeKind.EXTERNAL),
        ),
        connections=(connection("web-api", "web", "api"), connection("api-db", "api", "db")),
    )


def context(
    ir: ArchitectureIR | None = None, *, policy: ArchitecturePolicy | None = None, **request: Any
) -> SecurityContext:
    return SecurityContext(
        ir or architecture(),
        REVISION,
        SecurityAnalysisRequest(uuid.UUID(int=1), 1, **request),
        policy=policy or ArchitecturePolicy(),
    )


# --- registry --------------------------------------------------------------------------------------


def test_the_registry_refuses_duplicates_and_inconsistent_declarations() -> None:
    first = per_component(meta("exposure"))
    with pytest.raises(DuplicateAnalyzer):
        Registry([first, per_component(meta("exposure"))])
    with pytest.raises(DuplicateAnalyzer):
        Registry([per_component(meta("x", inputs=("the_internet",)))])
    with pytest.raises(DuplicateAnalyzer):
        Registry([per_component(meta("x", produces=("scores",)))])
    with pytest.raises(DuplicateAnalyzer):
        Registry([per_component(meta("x", finding_types=()))])
    with pytest.raises(DuplicateAnalyzer):  # what it builds on must run before it
        Registry([per_component(meta("threats", requires=("exposure",))), first])
    registry = Registry([first, per_component(meta("threats", requires=("exposure",)))])
    assert [a.meta.id for a in registry.analyzers()] == ["exposure", "threats"]
    assert registry.analyzer_set() == ModelSet.of([("exposure", 1), ("threats", 1)])


# --- selection -----------------------------------------------------------------------------------


def test_analyzer_selection_is_explicit() -> None:
    ran: list[str] = []

    def recording(analyzer_id: str) -> Fake:
        def run(ctx: SecurityContext, _: Progress) -> AnalyzerOutput:
            ran.append(analyzer_id)
            return AnalyzerOutput()

        return Fake(meta(analyzer_id), run)

    registry = Registry([recording("a"), recording("b"), recording("c")])
    result = analyze(context(analyzers=("c", "a")), registry)
    assert ran == ["a", "c"]  # only those selected, in registered order
    assert result.analyzer_set == ModelSet.of([("a", 1), ("c", 1)])  # what ran is recorded
    ran.clear()
    analyze(context(), registry)
    assert ran == ["a", "b", "c"]  # no selection: every analyzer


@pytest.mark.parametrize(
    ("request_fields", "details"),
    [
        (
            {"analyzers": ("ghost",)},
            {"field": "analyzers", "reason": "unknown_analyzer", "analyzer": "ghost"},
        ),
        (
            {"analyzers": ("threats",)},
            {
                "field": "analyzers",
                "reason": "requires_analyzer",
                "analyzer": "threats",
                "requires": "exposure",
            },
        ),
        ({"scope": ("ghost",)}, {"field": "scope", "reason": "unknown_node", "node_id": "ghost"}),
        ({"scope": ("web",)}, {"field": "scope", "reason": "unknown_node", "node_id": "web"}),  # a client
        ({"scope": ("dmz",)}, {"field": "scope", "reason": "unknown_node", "node_id": "dmz"}),  # a boundary
    ],
)
def test_requests_naming_what_does_not_exist_are_refused(
    request_fields: dict[str, Any], details: dict[str, str]
) -> None:
    registry = Registry(
        [per_component(meta("exposure")), per_component(meta("threats", requires=("exposure",)))]
    )
    with pytest.raises(InvalidSecurityRequest) as error:
        analyze(context(**request_fields), registry)
    assert error.value.details == details


# --- failures ------------------------------------------------------------------------------------


def test_a_failing_analyzer_is_isolated_and_logged_without_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def crash(ctx: SecurityContext, _: Progress) -> AnalyzerOutput:
        raise ValueError("password=hunter2")  # a message that could carry a configuration value

    registry = Registry([Fake(meta("broken"), crash), per_component(meta("exposure"))])
    with caplog.at_level(logging.ERROR, logger="architectos.security"):
        result = analyze(context(), registry)
    assert [u.code for u in result.unsupported] == ["analyzer_failed"]
    assert "broken" in result.unsupported[0].message
    assert len(result.findings) == 3  # the other analyzer's findings still count
    assert result.status is SecurityStatus.PARTIAL
    [record] = caplog.records
    assert (record.analyzer_id, record.error_type) == ("broken", "ValueError")  # type: ignore[attr-defined]
    assert "hunter2" not in caplog.text
    assert record.exc_info is None  # no traceback: it would carry the message


def _undeclared_check(m: AnalyzerMeta) -> AnalyzerOutput:
    check = CheckResult(
        "policy.require_tls",
        CheckSource.POLICY,
        Condition.ENCRYPTION_IN_TRANSIT,
        Verdict.NOT_VERIFIABLE,
        "Not modeled.",
        policy_rule="require_tls",
    )
    return AnalyzerOutput(checks=(check,))


@pytest.mark.parametrize(
    "bad_output",
    [
        lambda m: "not an output",
        lambda m: AnalyzerOutput((found(m, ("api",), T.MISSING_AUTHENTICATION),)),  # not declared
        lambda m: AnalyzerOutput((found(m, ("ghost",)),)),  # an element this revision lacks
        lambda m: AnalyzerOutput((dataclasses.replace(found(m, ("api",)), analyzer_id="other"),)),
        _undeclared_check,  # it does not declare checks
    ],
)
def test_a_malformed_output_is_refused_and_the_rest_counts(bad_output: Callable[[AnalyzerMeta], Any]) -> None:
    m = meta("bad")
    registry = Registry([Fake(m, lambda ctx, _: bad_output(m)), per_component(meta("exposure"))])
    result = analyze(context(), registry)
    assert [u.code for u in result.unsupported] == ["invalid_output"]
    assert {f.analyzer_id for f in result.findings} == {"exposure"}


def test_a_check_key_is_used_once_across_analyzers() -> None:
    def checking(analyzer_id: str) -> Fake:
        m = meta(analyzer_id, produces=("checks",), finding_types=())
        return Fake(m, lambda ctx, _: _undeclared_check(m))

    result = analyze(context(), Registry([checking("first"), checking("second")]))
    assert [c.key for c in result.checks] == ["policy.require_tls"]
    assert [u.message for u in result.unsupported] == ["The analyzer second produced a malformed result."]


# --- progress and scope --------------------------------------------------------------------------


def test_analyzers_see_the_findings_before_them() -> None:
    seen: list[int] = []

    def threats(ctx: SecurityContext, progress: Progress) -> AnalyzerOutput:
        seen.append(len(progress.findings))
        return AnalyzerOutput()

    registry = Registry(
        [per_component(meta("exposure")), Fake(meta("threats", requires=("exposure",)), threats)]
    )
    analyze(context(), registry)
    assert seen == [3]


def test_a_scope_limits_components_connections_and_findings() -> None:
    ctx = context(scope=("db",))
    assert [n.id for n in ctx.components] == ["db"]
    assert [c.id for c in ctx.connections] == ["api-db"]  # what touches the scope

    m = meta("everything")
    every = Fake(m, lambda c, _: AnalyzerOutput(tuple(found(m, (n,)) for n in ("api", "db", "idp"))))
    result = analyze(ctx, Registry([every]))
    assert [f.node_ids for f in result.findings] == [("db",)]  # findings about nothing in scope dropped
    assert [c.node_id for c in result.components] == ["db"]


# --- components and trust zones ------------------------------------------------------------------


def test_each_component_says_what_it_models_and_what_it_lacks() -> None:
    result = analyze(context(), default_registry())
    api, db, idp = result.components
    assert (api.node_id, api.coverage, api.missing) == ("api", Coverage.MODELED, ())
    assert (api.exposure, api.sensitive, api.trust_zone_ids) == ("public", False, ("dmz",))
    assert db.coverage is Coverage.PARTIAL
    assert db.missing == (
        "configuration.authentication",
        "configuration.data_classification",
        "configuration.encryption_at_rest",
        "configuration.secrets_required",
    )
    assert (db.sensitive, db.trust_zone_ids) == (True, ("core",))  # personal data; zone through vpc
    assert (idp.coverage, idp.missing) == (Coverage.NOT_MODELED, ("configuration.data_classification",))
    assert Evidence("configuration.personal_data", "true") in db.inputs
    assert result.status is SecurityStatus.PARTIAL


def test_trust_zones_are_the_declared_ones_with_their_level() -> None:
    result = analyze(context(), default_registry())
    assert result.trust_zones == (
        TrustZone("core", None, ("db",)),  # its trust level is not modeled: None, never assumed
        TrustZone("dmz", "untrusted", ("api",)),
    )


def test_what_a_component_should_model_follows_what_it_declares() -> None:
    pay: dict[str, ConfigValue] = {
        "exposure": "internal",
        "authentication": "mtls",
        "data_classification": "restricted",
        "secrets_required": True,
        "sensitive_operations": True,
    }
    ir = ArchitectureIR("Shop", nodes=(node("pay", configuration=Configuration(pay)),))
    [result] = analyze(context(ir), default_registry()).components
    # it performs sensitive operations and needs secrets: authorization and a source now matter
    assert result.missing == ("configuration.authorization", "configuration.secret_source")


def test_statuses_without_components_or_without_any_security_property() -> None:
    empty = analyze(
        context(ArchitectureIR("Empty", nodes=(node("web", NodeKind.CLIENT),))), default_registry()
    )
    assert empty.status is SecurityStatus.UNSUPPORTED
    assert "no_components" in {x.code for x in empty.limitations}
    bare = analyze(context(ArchitectureIR("Bare", nodes=(node("api"),))), default_registry())
    assert bare.status is SecurityStatus.INSUFFICIENT_INPUT  # unknown is not secure
    assert {x.code for x in bare.limitations} == {"architecture_level", "no_defaults"}


# --- determinism ---------------------------------------------------------------------------------


def test_the_same_inputs_give_the_same_result_whatever_the_order() -> None:
    registry = Registry([per_component(meta("exposure"))])
    ir = architecture()
    first = analyze(context(ir), registry)
    again = analyze(context(ir), registry)
    shuffled = dataclasses.replace(
        ir, nodes=tuple(reversed(ir.nodes)), connections=tuple(reversed(ir.connections))
    )
    reordered = analyze(context(shuffled), registry)
    assert first.to_dict() == again.to_dict() == reordered.to_dict()
    assert first.fingerprint == reordered.fingerprint
    assert ir == architecture()  # the architecture is never modified


def test_the_context_fingerprint_follows_every_input() -> None:
    base = context().fingerprint
    assert context().fingerprint == base
    assert context(scope=("api",)).fingerprint != base
    assert context(policy=ArchitecturePolicy(require_tls=True)).fingerprint != base
    other = SecurityContext(architecture(), RevisionInfo("arch-1", 2, "d" * 64), context().request)
    assert other.fingerprint != base
