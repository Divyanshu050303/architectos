"""Drift fixtures (Drift Detection Engine, phase 9): the eighteen fixture kinds the requirements name, each
run end to end through the use cases — a discovery run, its proposal accepted through the architecture
workflow, a later run, a drift analysis of the exact revision, drift items and their review — on the
in-memory unit of work, with what each must and must not produce. Then the service's own guarantees:
request validation, exact revision resolution, authorization, isolation, failures stored, determinism."""

import json
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from core.architecture_ir.component import NodeKind
from core.architecture_ir.configuration import Configuration
from core.architecture_ir.model import ArchitectureIR
from core.architecture_ir.node import Node
from core.architecture_ir.serialization import content_hash
from core.domain.architecture.architecture_service import ArchitectureService
from core.domain.architecture.errors import ArchitectureNotFound, ArchitectureRevisionNotFound
from core.domain.audit.entities import AuditAction
from core.domain.discovery.discovery_service import DiscoveryService
from core.domain.discovery.errors import DiscoveryRunInUse, DiscoveryRunNotFound
from core.domain.discovery.runs import ArtifactInput, DiscoveryRequest, DiscoveryRun
from core.domain.drift.analyses import DriftAnalysis, DriftRequest, DriftResult
from core.domain.drift.drift_service import DriftService
from core.domain.drift.errors import (
    DriftAnalysisNotFound,
    DriftItemNotFound,
    InvalidDriftRequest,
    InvalidDriftResult,
    InvalidReviewAction,
)
from core.domain.drift.findings import DriftFinding
from core.domain.drift.items import DriftItem, Link
from core.domain.drift.ports import DriftInputs
from core.domain.drift.values import (
    AnalysisStatus,
    Classification,
    Compatibility,
    FindingType,
    LinkKind,
    MatchMethod,
    ReviewAction,
    ReviewStatus,
)
from core.domain.evolution.values import EvidenceState
from core.domain.organizations.errors import PermissionDenied
from core.domain.projects.errors import ProjectNotFound
from engines.discovery.engine import DeterministicDiscoveryEngine
from engines.drift.comparison import shown_values
from engines.drift.engine import DeterministicDriftEngine
from persistence.component_catalog import default_catalog
from tests.unit.architecture.world import World, make_world
from tests.unit.identity.fakes import FakeClock, FakeUnitOfWork

C, F, A, S = Classification, FindingType, ReviewAction, ReviewStatus
CATALOG = default_catalog()
SHOP = "name: shop\nservices:\n  db:\n    image: postgres:16\n"
TWO = SHOP + "  ledger:\n    image: postgres:16\n"
TUNED = SHOP + "    deploy: {replicas: 3}\n"
RENAMED = "name: shop\nservices:\n  database:\n    image: postgres:16\n"
QUEUED = SHOP + "  queue:\n    image: rabbitmq:3\n    depends_on: [db]\n"
QUEUE_ALONE = SHOP + "  queue:\n    image: rabbitmq:3\n"
BILLING = "name: billing\nservices:\n  ledger:\n    image: postgres:16\n"
PARTLY = "name: billing\nservices: {}\ninclude: [more.yaml]\n"
HCL = 'resource "aws_sqs_queue" "jobs" {}\n'
SECRET, ROTATED = "hunter2-v1", "hunter2-v2"
DB, LEDGER, QUEUE = "compose:shop/service/db", "compose:shop/service/ledger", "compose:shop/service/queue"
DATABASE, BILLING_LEDGER = "compose:shop/service/database", "compose:billing/service/ledger"


class Env:
    names = 0

    def __init__(self, uow: FakeUnitOfWork, clock: FakeClock, world: World) -> None:
        self.uow, self.clock, self.world = uow, clock, world
        self.discovery = DiscoveryService(uow, DeterministicDiscoveryEngine(CATALOG), clock=clock)
        self.drift = DriftService(uow, DeterministicDriftEngine(CATALOG), clock=clock)
        self.architectures = ArchitectureService(uow, clock=clock)

    @property
    def project(self) -> uuid.UUID:
        return self.world.project.id

    @property
    def ada(self) -> uuid.UUID:
        return self.world.ada.id

    async def discover(self, *artifacts: tuple[str, str]) -> DiscoveryRun:
        self.clock.advance(timedelta(minutes=1))
        request = DiscoveryRequest(tuple(ArtifactInput(p, c) for p, c in artifacts))
        return await self.discovery.run(project_id=self.project, user_id=self.ada, request=request)

    async def accept(
        self, run: DiscoveryRun, architecture_id: uuid.UUID | None = None, base_version: int | None = None
    ) -> uuid.UUID:
        _, proposal = await self.discovery.proposal(project_id=self.project, run_id=run.id, user_id=self.ada)
        assert proposal.architecture is not None
        accepted = await self.discovery.accept(
            project_id=self.project, run_id=run.id, user_id=self.ada,
            proposal_content_hash=content_hash(proposal.architecture), architecture_id=architecture_id,
            base_version=base_version, name=None if architecture_id else self.name(),
        )  # fmt: skip
        return accepted.acceptance.architecture_id

    def name(self) -> str:
        Env.names += 1
        return f"Shop {Env.names}"

    async def baseline(self, *artifacts: tuple[str, str]) -> uuid.UUID:
        """An architecture accepted from a discovery of ``artifacts`` (its revision 1)."""
        return await self.accept(await self.discover(*artifacts))

    async def hand_written(self, ir: ArchitectureIR) -> uuid.UUID:
        created, _ = await self.architectures.create(
            project_id=self.project, user_id=self.ada, name=ir.name, ir=ir
        )
        return created.id

    async def analyze(
        self, architecture_id: uuid.UUID, run: DiscoveryRun, revision: int = 1, **extra: Any
    ) -> DriftAnalysis:
        self.clock.advance(timedelta(minutes=1))
        request = DriftRequest(architecture_id, revision, run.id, **extra)
        return await self.drift.analyze(project_id=self.project, user_id=self.ada, request=request)

    async def items(self, architecture_id: uuid.UUID) -> list[DriftItem]:
        page = await self.drift.list_items(
            project_id=self.project, user_id=self.ada, architecture_id=architecture_id
        )
        return list(page.items)

    async def review(self, item: DriftItem, action: ReviewAction, **extra: Any) -> DriftItem:
        self.clock.advance(timedelta(minutes=1))
        return await self.drift.review(
            project_id=self.project, item_id=item.id, user_id=self.ada, action=action, **extra
        )

    async def confirm(self, architecture_id: uuid.UUID, baseline_id: str, discovered_key: str | None) -> None:
        await self.drift.confirm_identity(
            project_id=self.project, architecture_id=architecture_id, user_id=self.ada,
            baseline_id=baseline_id, discovered_key=discovered_key,
        )  # fmt: skip

    def revisions(self, architecture_id: uuid.UUID) -> list[str]:
        stored = self.uow.architectures.revisions
        return [stored[k].content_hash for k in sorted(stored) if k[0] == architecture_id]


@pytest.fixture
async def env() -> Env:
    clock = FakeClock()
    uow = FakeUnitOfWork(clock)
    return Env(uow, clock, await make_world(uow, clock))


def result(analysis: DriftAnalysis) -> DriftResult:
    assert analysis.result is not None
    return analysis.result


def finding(analysis: DriftAnalysis, subject: str, path: str | None = None) -> DriftFinding:
    return next(f for f in result(analysis).findings if f.subject == subject and f.path == path)


def kinds(analysis: DriftAnalysis) -> set[tuple[FindingType, str]]:
    return {(f.type, f.subject) for f in result(analysis).findings}


# --- the eighteen fixtures ---------------------------------------------------------------------------


async def test_01_identical_inputs_with_complete_coverage_report_no_difference_within_it(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    analysis = await env.analyze(aid, await env.discover(("compose.yaml", SHOP)))
    found = result(analysis)
    assert (found.findings, found.coverage.complete, found.no_difference_within_coverage) == ((), True, True)
    assert found.status is Compatibility.COMPATIBLE
    assert analysis.status is AnalysisStatus.COMPLETED
    assert await env.items(aid) == []


async def test_02_one_confirmed_component_addition(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    added = finding(await env.analyze(aid, await env.discover(("compose.yaml", TWO))), f"node:{LEDGER}")
    assert (added.type, added.classification, added.match) == (
        F.COMPONENT_ADDED, C.CONFIRMED, MatchMethod.UNMATCHED,
    )  # fmt: skip
    assert added.evidence
    assert added.locations == ("compose.yaml#0:services.ledger",)


async def test_03_one_confirmed_removal_where_its_source_was_read_completely(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", TWO))
    removed = finding(await env.analyze(aid, await env.discover(("compose.yaml", SHOP))), f"node:{LEDGER}")
    assert (removed.type, removed.classification) == (F.COMPONENT_REMOVED, C.CONFIRMED)
    assert removed.baseline_reference == "compose.yaml#0:services.ledger"


async def test_04_a_component_missing_under_incomplete_coverage_is_never_removed(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP), ("billing.yaml", BILLING))
    unread = await env.analyze(aid, await env.discover(("compose.yaml", SHOP)))  # billing not supplied
    ledger = finding(unread, f"node:{BILLING_LEDGER}")
    assert (ledger.type, ledger.classification) == (F.COMPONENT_REMOVED, C.UNKNOWN)
    assert finding(unread, "artifact:billing.yaml").type is F.COVERAGE_CHANGED
    assert not any(
        f.type is F.COMPONENT_REMOVED and f.classification is C.CONFIRMED for f in result(unread).findings
    )
    assert unread.status is AnalysisStatus.COMPLETED_WITH_WARNINGS


async def test_05_a_component_configuration_change(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    changed = await env.analyze(aid, await env.discover(("compose.yaml", TUNED)))
    replicas = finding(changed, f"node:{DB}", "configuration.replicas")
    assert (replicas.type, replicas.classification) == (F.RESOURCE_CHANGED, C.CONFIRMED)
    assert (replicas.baseline_value, replicas.discovered_value, replicas.match) == (
        None, 3, MatchMethod.SAME_ID,
    )  # fmt: skip
    assert replicas.locations == ("compose.yaml#0:services.db",)


async def test_06_a_connection_addition_and_removal(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", QUEUE_ALONE))
    added = await env.analyze(aid, await env.discover(("compose.yaml", QUEUED)))
    (connection,) = [f for f in result(added).findings if f.type is F.CONNECTION_ADDED]
    assert connection.classification is C.CONFIRMED
    assert QUEUE in connection.subject
    assert DB in connection.subject

    connected = await env.baseline(("compose.yaml", QUEUED))
    removed = await env.analyze(connected, await env.discover(("compose.yaml", QUEUE_ALONE)))
    assert [f.type for f in result(removed).findings] == [F.CONNECTION_REMOVED]
    assert result(removed).findings[0].classification is C.CONFIRMED


async def test_07_a_renamed_resource_with_a_confirmed_identity_is_the_same_component(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    await env.confirm(aid, DB, DATABASE)
    renamed = await env.analyze(aid, await env.discover(("compose.yaml", RENAMED)))
    assert not {F.COMPONENT_ADDED, F.COMPONENT_REMOVED} & {f.type for f in result(renamed).findings}
    assert all(f.match is MatchMethod.CONFIRMED_MAPPING for f in result(renamed).findings if f.baseline_id)


async def test_08_similar_names_without_identity_evidence_are_never_merged(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    renamed = await env.analyze(aid, await env.discover(("compose.yaml", RENAMED)))
    assert kinds(renamed) >= {(F.COMPONENT_REMOVED, f"node:{DB}"), (F.COMPONENT_ADDED, f"node:{DATABASE}")}
    assert not any(f.baseline_id and f.discovered_key for f in result(renamed).findings)  # nothing paired


async def test_09_an_ambiguous_identity_stays_unresolved_with_its_candidates(env: Env) -> None:
    ir = ArchitectureIR(
        "Shop", nodes=(Node(DB, NodeKind.DATABASE, "db"), Node("old-db", NodeKind.DATABASE, "Old"))
    )
    aid = await env.hand_written(ir)
    await env.confirm(aid, "old-db", DB)  # DB is also a node's own id: two claims on one entity
    analysis = await env.analyze(aid, await env.discover(("compose.yaml", SHOP)))
    unresolved = finding(analysis, "node:old-db")
    assert (unresolved.type, unresolved.classification) == (F.UNRESOLVED_DIFFERENCE, C.UNKNOWN)
    assert unresolved.match is MatchMethod.AMBIGUOUS
    assert any(DB in limitation for limitation in unresolved.limitations)


async def test_10_a_parser_version_change_is_not_compared(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    later = await env.discover(("compose.yaml", TWO))
    assert later.result is not None
    # The same artifacts read by a later Compose extractor: its normalization may differ.
    reread = replace(
        later,
        id=uuid.uuid7(),
        result=replace(later.result, extractors=dict(later.result.extractors) | {"docker_compose": 2}),
    )
    await env.uow.discoveries.add(reread)
    analysis = await env.analyze(aid, reread)
    checks = {c.dimension: c.outcome for c in result(analysis).compatibility}
    assert checks["extractor_versions"] is Compatibility.INCOMPATIBLE  # its only source type changed
    assert analysis.status is AnalysisStatus.INCOMPATIBLE_INPUTS
    assert {(f.type, f.classification) for f in result(analysis).findings} == {
        (F.COMPARISON_INCOMPATIBLE, C.NOT_COMPARABLE),
    }  # the added ledger is not claimed: a parser change is never an architecture change
    assert all(i.type is not F.COMPONENT_ADDED for i in await env.items(aid))


async def test_11_an_unsupported_source_is_unread_never_absent(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    analysis = await env.analyze(aid, await env.discover(("compose.yaml", SHOP), ("main.tf", HCL)))
    found = result(analysis)
    assert found.coverage.unread == ("main.tf",)
    assert not found.coverage.complete  # said only of what was inspected: compose.yaml
    assert found.no_difference_within_coverage
    assert not any(f.type is F.COMPONENT_REMOVED for f in found.findings)
    assert analysis.status is AnalysisStatus.COMPLETED_WITH_WARNINGS


async def test_12_a_partial_run_completes_with_warnings_and_only_potential_removals(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP), ("billing.yaml", BILLING))
    analysis = await env.analyze(aid, await env.discover(("compose.yaml", SHOP), ("billing.yaml", PARTLY)))
    assert result(analysis).coverage.partial == ("billing.yaml",)
    ledger = finding(analysis, f"node:{BILLING_LEDGER}")
    assert (ledger.type, ledger.classification) == (F.COMPONENT_REMOVED, C.POTENTIAL)
    assert analysis.status is AnalysisStatus.COMPLETED_WITH_WARNINGS
    assert result(analysis).warnings


async def test_13_secret_bearing_configuration_is_never_kept_or_shown(env: Env) -> None:
    ir = ArchitectureIR(
        "Shop",
        nodes=(
            Node(DB, NodeKind.DATABASE, "db", configuration=Configuration(extra={"db_password": SECRET})),
        ),
    )
    aid = await env.hand_written(ir)
    await env.confirm(aid, DB, DB)
    env_var = SHOP + f"    environment: [POSTGRES_PASSWORD={ROTATED}]\n"
    analysis = await env.analyze(aid, await env.discover(("compose.yaml", env_var)))
    shown = json.dumps(result(analysis).to_dict()) + repr(await env.items(aid))
    shown += repr(env.uow.audit.events)
    assert SECRET not in shown
    assert ROTATED not in shown
    # Wherever a secret path differs, that it changed is stated — its values never.
    assert shown_values("configuration.extra.db_password", SECRET, ROTATED) == {
        "baseline_value": None, "discovered_value": None, "redacted": True,
    }  # fmt: skip


async def test_14_a_stale_baseline_or_an_older_run_is_compared_with_a_warning(env: Env) -> None:
    early = await env.discover(("compose.yaml", SHOP))
    aid = await env.baseline(("compose.yaml", SHOP))  # revision 1, after the early run
    await env.accept(await env.discover(("compose.yaml", TWO)), aid, base_version=1)  # revision 2
    analysis = await env.analyze(aid, early, revision=1)
    freshness = next(c for c in result(analysis).compatibility if c.dimension == "freshness")
    assert freshness.outcome is Compatibility.COMPATIBLE_WITH_WARNINGS
    assert "Revision 2 is later" in freshness.message
    assert "older than the baseline revision" in freshness.message
    assert result(analysis).baseline.revision_number == 1  # the exact revision asked for, never "latest"
    assert analysis.status is AnalysisStatus.COMPLETED_WITH_WARNINGS


async def test_15_impact_with_missing_downstream_analyses_claims_nothing(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    replicas = finding(
        await env.analyze(aid, await env.discover(("compose.yaml", TUNED))),
        f"node:{DB}",
        "configuration.replicas",
    )
    assert replicas.impact
    for ref in replicas.impact:
        assert (ref.state, ref.analysis_id, ref.items) == (EvidenceState.MISSING, None, ())
        assert "No analysis of the baseline is stored." in ref.basis
    assert replicas.classification is C.CONFIRMED  # detected without any downstream analysis


async def test_16_a_repeated_difference_is_one_item_across_analyses(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    later = await env.discover(("compose.yaml", TWO))
    first, second = await env.analyze(aid, later), await env.analyze(aid, later)
    (item,) = [i for i in await env.items(aid) if i.subject == f"node:{LEDGER}"]
    assert (item.first_analysis_id, item.last_analysis_id) == (first.id, second.id)
    assert [e.action for e in item.history] == [A.DETECTED, A.DETECTED]
    assert finding(first, f"node:{LEDGER}").id == finding(second, f"node:{LEDGER}").id


async def test_17_a_resolved_difference_detected_again_reopens(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    drifted = await env.discover(("compose.yaml", TWO))
    await env.analyze(aid, drifted)
    (item,) = await env.items(aid)
    reverted = await env.analyze(aid, await env.discover(("compose.yaml", SHOP)))
    resolved = await env.review(item, A.RESOLVE, evidence_analysis_id=reverted.id)
    assert resolved.status is S.RESOLVED
    again = await env.analyze(aid, drifted)
    (reopened,) = await env.items(aid)
    assert (reopened.status, reopened.last_analysis_id) == (S.REOPENED, again.id)
    assert [e.action for e in reopened.history] == [A.DETECTED, A.RESOLVE, A.DETECTED]  # nothing erased


async def test_18_review_actions_leave_the_canonical_architecture_unchanged(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    run = await env.discover(("compose.yaml", TWO))
    await env.analyze(aid, run)
    before, run_before = env.revisions(aid), env.uow.discoveries.runs[run.id]
    (item,) = await env.items(aid)
    item = await env.review(item, A.ACKNOWLEDGE)
    item = await env.review(item, A.INVESTIGATE)
    item = await env.review(item, A.NOTE, note="Ledger split out for billing.")
    item = await env.review(item, A.LINK, link=Link(LinkKind.REVISION, "1", aid))
    item = await env.review(item, A.ACCEPT)
    assert item.status is S.ACCEPTED
    assert env.revisions(aid) == before  # accepted drift does not update the baseline
    assert env.uow.discoveries.runs[run.id] == run_before
    reviewed = [e for e in env.uow.audit.events if e.action is AuditAction.DRIFT_ITEM_REVIEWED]
    assert len(reviewed) == 5
    assert all("Ledger" not in repr(e.metadata) for e in reviewed)


# --- the service's own guarantees -------------------------------------------------------------------


async def test_requests_are_validated_and_the_exact_revision_and_run_resolved(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    run = await env.discover(("compose.yaml", SHOP))
    with pytest.raises(InvalidDriftRequest):
        DriftRequest(aid, 1, run.id, policy="lenient")
    with pytest.raises(ArchitectureRevisionNotFound):
        await env.analyze(aid, run, revision=7)
    with pytest.raises(ArchitectureNotFound):
        await env.analyze(uuid.uuid4(), run)
    elsewhere = await env.discovery.run(
        project_id=env.world.other.id,
        user_id=env.ada,
        request=DiscoveryRequest((ArtifactInput("compose.yaml", SHOP),)),
    )
    with pytest.raises(DiscoveryRunNotFound):  # another project's run is not found
        await env.analyze(aid, elsewhere)
    failed = replace(run, id=uuid.uuid7(), result=None)
    await env.uow.discoveries.add(failed)
    with pytest.raises(InvalidDriftRequest) as refused:
        await env.analyze(aid, failed)
    assert refused.value.details == {"field": "discovery_run_id", "reason": "no_result"}
    assert env.uow.drift.analyses == {}


async def test_authorization_and_isolation(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    run = await env.discover(("compose.yaml", TWO))
    analysis = await env.analyze(aid, run)
    (item,) = await env.items(aid)
    vic, eve = env.world.vic.id, env.world.eve.id
    viewed = await env.drift.get(project_id=env.project, analysis_id=analysis.id, user_id=vic)
    assert viewed == analysis  # viewers read
    with pytest.raises(PermissionDenied):
        await env.drift.analyze(project_id=env.project, user_id=vic, request=DriftRequest(aid, 1, run.id))
    with pytest.raises(PermissionDenied):
        await env.drift.review(project_id=env.project, item_id=item.id, user_id=vic, action=A.ACKNOWLEDGE)
    with pytest.raises(PermissionDenied):
        await env.drift.confirm_identity(
            project_id=env.project, architecture_id=aid, user_id=vic, baseline_id=DB, discovered_key=DB
        )
    with pytest.raises(ProjectNotFound):  # another organization
        await env.drift.get(project_id=env.project, analysis_id=analysis.id, user_id=eve)
    other = env.world.other.id
    with pytest.raises(DriftAnalysisNotFound):  # IDOR: the right id under another project
        await env.drift.get(project_id=other, analysis_id=analysis.id, user_id=env.ada)
    with pytest.raises(DriftItemNotFound):
        await env.drift.review(project_id=other, item_id=item.id, user_id=env.ada, action=A.ACKNOWLEDGE)
    with pytest.raises(InvalidReviewAction) as foreign:  # a link to a record of no project of ours
        await env.review(item, A.LINK, link=Link(LinkKind.DECISION, str(uuid.uuid4())))
    assert foreign.value.details["reason"] == "link_target_not_found"
    with pytest.raises(DiscoveryRunInUse):  # the evidence an analysis rests on is kept
        await env.discovery.delete(project_id=env.project, run_id=run.id, user_id=env.ada)


async def test_resolution_needs_comparable_evidence_of_the_same_architecture(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    run = await env.discover(("compose.yaml", TWO))
    current = await env.analyze(aid, run)
    (item,) = await env.items(aid)
    other = await env.baseline(("compose.yaml", SHOP))
    elsewhere = await env.analyze(other, await env.discover(("compose.yaml", SHOP)))
    cases = {
        current.id: "still_detected",
        elsewhere.id: "another_architecture",
    }
    for evidence, reason in cases.items():
        with pytest.raises(InvalidReviewAction) as refused:
            await env.review(item, A.RESOLVE, evidence_analysis_id=evidence)
        assert refused.value.details["reason"] == reason
    unrelated = await env.analyze(aid, await env.discover(("other.yaml", SHOP.replace("shop", "misc"))))
    with pytest.raises(InvalidReviewAction) as outside:
        await env.review(item, A.RESOLVE, evidence_analysis_id=unrelated.id)
    assert outside.value.details["reason"] in {"outside_coverage", "not_compared"}


class Refusing:
    """An engine whose result fails its own checks: the analysis is stored failed, nothing else."""

    def run(self, inputs: DriftInputs) -> DriftResult:
        raise InvalidDriftResult(details={"fields": ["findings"]})

    def versions(self) -> dict[str, int]:
        return {}


async def test_an_engine_refusal_is_stored_as_a_failed_analysis(env: Env) -> None:
    aid = await env.baseline(("compose.yaml", SHOP))
    refusing = DriftService(env.uow, Refusing(), clock=env.clock)
    run = await env.discover(("compose.yaml", TWO))
    failed = await refusing.analyze(
        project_id=env.project, user_id=env.ada, request=DriftRequest(aid, 1, run.id)
    )
    assert (failed.status, failed.result) == (AnalysisStatus.FAILED, None)
    assert failed.error is not None
    assert failed.error.code == "invalid_drift_result"
    assert env.uow.drift.analyses[failed.id] == failed
    assert await env.items(aid) == []


async def test_identical_inputs_give_identical_normalized_results(env: Env) -> None:
    source = await env.discover(("compose.yaml", QUEUE_ALONE), ("billing.yaml", BILLING))
    aid = await env.accept(source)
    later = await env.discover(("compose.yaml", QUEUED.replace("rabbitmq:3", "rabbitmq:3.13")), ("x.tf", HCL))
    first, second = await env.analyze(aid, later), await env.analyze(aid, later)

    def normalized(analysis: DriftAnalysis) -> Any:
        found = result(analysis)
        return (
            [c.to_dict() for c in found.compatibility],
            [(f.id, f.type, f.path, f.match, f.baseline_id, f.discovered_key) for f in found.findings],
            [(f.evidence, f.locations) for f in found.findings],
            found.summary(),
            found.fingerprint,
        )

    assert first.id != second.id
    assert normalized(first) == normalized(second)
    assert result(first).findings  # something was compared
    subjects = [f.sort_key for f in result(first).findings]
    assert subjects == sorted(subjects)  # canonical order
    assert "score" not in json.dumps(result(first).summary())
